"""Scan session logs and write the static site. This is the core of `session-doc generate`.

Output layout (docs/DESIGN.md section 6):

    <out>/index.html                              all projects
    <out>/assets/                                 style, viewer script, MiniSearch
    <out>/projects/<project>/index.html           sessions of one project
    <out>/projects/<project>/search-index.js      session list and search docs
    <out>/projects/<project>/sessions/<s>.html    one page per session
    <out>/raw/<project>/<s>.jsonl                 archived copy of the source log
    <out>/state.json                              hashes and session metadata

Runs are incremental: a session is rendered again only when the sha256 of its log
changed. Source logs are only read. Pages of sessions whose log disappeared are kept.
Every file is written atomically (temp file in the same directory, then rename).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from session_doc import __version__, render
from session_doc.extractor import extract_items, session_title
from session_doc.parser import AssistantRecord, Session, UserRecord, parse_session
from session_doc.render import ProjectInfo, SessionInfo

STATE_FORMAT = 1
STATE_FILE = "state.json"
DATA_FILE = "search-index.js"


class GenerateError(Exception):
    """The run was refused or cannot go on. The message says why."""


class GitWorkTreeError(GenerateError):
    """The output dir is inside a git work tree and that was not allowed."""

    def __init__(self, out: Path, repo: Path) -> None:
        super().__init__(
            f"{out} is inside the git work tree at {repo}. Session logs hold file "
            "contents, paths, and secrets that should not end up in a repository."
        )
        self.out = out
        self.repo = repo


@dataclass
class GenerateResult:
    out: Path
    new: int = 0
    changed: int = 0
    skipped: int = 0
    # Unchanged sessions rendered again because session-doc or the config changed.
    rerendered: int = 0

    @property
    def index(self) -> Path:
        return self.out / "index.html"


# --------------------------------------------------------------------------- defaults


def default_source_dir() -> Path:
    """Claude Code's projects dir: `$CLAUDE_CONFIG_DIR/projects`, else `~/.claude/projects`."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    return base / "projects"


def default_out_dir() -> Path:
    return Path.home() / "session-doc"


# --------------------------------------------------------------------------- paths and names


def find_git_work_tree(path: Path) -> Path | None:
    """The nearest directory at or above `path` that holds a `.git` entry, if any.

    `.git` can be a directory (normal clone) or a file (worktree, submodule).
    """
    path = Path(path).absolute()
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def scan_sources(source: Path) -> list[Path]:
    """Session logs under `source`, sorted. Subagent logs (in `subagents/` dirs) are skipped."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = sorted(d for d in dirnames if d != "subagents")
        found += (Path(dirpath) / name for name in sorted(filenames) if name.endswith(".jsonl"))
    return found


_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def _slugify(text: str, limit: int) -> str:
    return _UNSAFE.sub("-", text.lower()).strip("-.")[:limit].strip("-.")


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def project_for(cwd: str | None, log_dir: str) -> ProjectInfo:
    """The project of a session: its first working directory.

    The slug is the directory name made filesystem-safe, plus a short hash of the full
    path, so two projects with the same directory name stay apart. Without a `cwd`, the
    name of the directory holding the log stands in for it.
    """
    if cwd:
        name = re.split(r"[\\/]+", cwd.rstrip("\\/"))[-1] or cwd
        key = cwd.replace("\\", "/").rstrip("/") or "/"
        if re.match(r"[A-Za-z]:", key):  # Windows paths are case-insensitive
            key = key.lower()
        return ProjectInfo(f"{_slugify(name, 40) or 'project'}-{_short_hash(key)}", name, cwd)
    name = log_dir or "unknown"
    return ProjectInfo(f"{_slugify(name, 40) or 'project'}-{_short_hash('dir:' + name)}", name, "")


# --------------------------------------------------------------------------- file helpers


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_to_temp(src: Path, directory: Path) -> tuple[Path, str]:
    """Copy `src` to a temp file in `directory`, hashing the bytes copied."""
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=directory, prefix=".incoming-", suffix=".tmp")
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise
    return Path(name), digest.hexdigest()


def _write_atomic(path: Path, data: bytes, *, durable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            if durable:
                fh.flush()
                os.fsync(fh.fileno())
        os.replace(name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise


def _write_if_changed(path: Path, content: str | bytes, *, durable: bool = False) -> bool:
    """Write atomically unless the file already holds exactly this content."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    with contextlib.suppress(FileNotFoundError):
        if path.read_bytes() == data:
            return False
    _write_atomic(path, data, durable=durable)
    return True


# --------------------------------------------------------------------------- state


def _iso(ts: datetime | None) -> str | None:
    return ts.astimezone(timezone.utc).isoformat() if ts else None


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class _Entry:
    """One session in `state.json`, keyed by the absolute path of its source log."""

    sha256: str
    project: str
    project_name: str
    cwd: str
    slug: str
    archived: bool
    listed: bool  # False when the log holds no conversation; such logs get no page
    title: str = ""
    start: str | None = None
    end: str | None = None
    branch: str | None = None
    counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: Any) -> _Entry | None:
        if not isinstance(raw, dict):
            return None
        known = {f.name for f in fields(cls)}
        try:
            return cls(**{k: v for k, v in raw.items() if k in known})
        except TypeError:  # a required field is missing
            return None

    @property
    def started(self) -> datetime | None:
        return _from_iso(self.start)

    def info(self) -> SessionInfo:
        return SessionInfo(
            slug=self.slug,
            title=self.title,
            start=self.started,
            end=_from_iso(self.end),
            branch=self.branch,
            counts=self.counts,
        )


def _fingerprint(phrases: Iterable[str]) -> dict[str, Any]:
    """What the rendered output depends on besides the logs. A change re-renders all."""
    return {
        "state": STATE_FORMAT,
        "data": render.DATA_FORMAT,
        "version": __version__,
        "decision_phrases": sorted(set(phrases)),
    }


def _state_json(fingerprint: dict[str, Any], sessions: dict[str, _Entry]) -> str:
    payload = {
        "format": STATE_FORMAT,
        "fingerprint": fingerprint,
        "sessions": {k: asdict(v) for k, v in sorted(sessions.items())},
    }
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


def _load_state(out: Path) -> tuple[dict[str, Any], dict[str, _Entry]] | None:
    """(fingerprint, sessions) from `state.json`, or None if there is no state file."""
    path = out / STATE_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise GenerateError(
            f"cannot read {path}: {exc}. Fix the file, or move the output dir away to "
            "start over (its raw/ dir holds the archived logs)."
        ) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("sessions"), dict):
        raise GenerateError(f"{path} is not a session-doc state file")
    fingerprint = raw.get("fingerprint")
    sessions = {}
    for key, value in raw["sessions"].items():
        entry = _Entry.from_json(value)
        if entry is not None:
            sessions[key] = entry
    return (fingerprint if isinstance(fingerprint, dict) else {}), sessions


# --------------------------------------------------------------------------- the run


def generate(
    source: Path,
    out: Path,
    *,
    archive: bool = True,
    allow_in_git: bool = False,
    decision_phrases: Iterable[str] = (),
) -> GenerateResult:
    """Render every new or changed session log under `source` into `out`.

    Refuses (with `GenerateError`) to write inside a git work tree unless `allow_in_git`,
    into a non-empty directory that session-doc did not create, or when `source` and
    `out` contain each other.
    """
    source = Path(source).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    _check_paths(source, out, allow_in_git)
    return _Run(out, archive=archive, phrases=tuple(decision_phrases)).execute(source)


def _check_paths(source: Path, out: Path, allow_in_git: bool) -> None:
    if not source.is_dir():
        raise GenerateError(f"source directory not found: {source}")
    if out == source or out.is_relative_to(source) or source.is_relative_to(out):
        raise GenerateError(
            f"the output dir ({out}) and the source dir ({source}) must not contain each other"
        )
    if out.exists() and not out.is_dir():
        raise GenerateError(f"output path exists and is not a directory: {out}")
    if not allow_in_git:
        repo = find_git_work_tree(out)
        if repo is not None:
            raise GitWorkTreeError(out, repo)


class _Run:
    def __init__(self, out: Path, *, archive: bool, phrases: tuple[str, ...]) -> None:
        self.out = out
        self.archive = archive
        self.phrases = phrases
        self.sessions: dict[str, _Entry] = {}
        # project slug -> session slug -> source key, to keep session slugs unique
        self.slugs: dict[str, dict[str, str]] = {}
        # project slug -> session slug -> search docs rendered in this run
        self.docs: dict[str, dict[str, list[dict[str, Any]]]] = {}
        # projects whose index page and data file must be written again
        self.touched: set[str] = set()

    def execute(self, source: Path) -> GenerateResult:
        fingerprint = _fingerprint(self.phrases)
        loaded = _load_state(self.out)
        if loaded is None:
            if self.out.is_dir() and any(self.out.iterdir()):
                raise GenerateError(
                    f"{self.out} is not empty and has no {STATE_FILE}, so session-doc did "
                    "not create it. Choose an empty or new directory."
                )
            # Mark the directory as ours before writing anything else into it.
            _write_if_changed(self.out / STATE_FILE, _state_json({}, {}), durable=True)
            old_fingerprint: dict[str, Any] = {}
        else:
            old_fingerprint, self.sessions = loaded
        for key, entry in self.sessions.items():
            self.slugs.setdefault(entry.project, {})[entry.slug] = key
        full = old_fingerprint != fingerprint

        counts: Counter[str] = Counter()
        seen: set[str] = set()
        for path in scan_sources(source):
            key = path.as_posix()
            entry = self.sessions.get(key)
            try:
                digest = _sha256_file(path)
                if entry is None:
                    status = "new"
                elif entry.sha256 != digest:
                    status = "changed"
                else:
                    status = "rerendered" if full else "skipped"
                if status != "skipped":
                    self._render_source(key, path, digest)
            except FileNotFoundError:
                continue  # the agent deleted the log while this run was reading it
            seen.add(key)
            counts[status] += 1

        if full:
            # Sessions whose source log is gone can still be rendered from the archive.
            for key, entry in list(self.sessions.items()):
                if key not in seen and self._render_archived(key, entry):
                    counts["rerendered"] += 1

        self._write_projects()
        self._write_shared(full)
        _write_if_changed(
            self.out / STATE_FILE, _state_json(fingerprint, self.sessions), durable=True
        )
        return GenerateResult(self.out, **counts)

    # ------------------------------------------------------------------ one session

    def _identify(self, key: str, session: Session) -> tuple[ProjectInfo, str]:
        """Project and session slug. A known session keeps its slug, so links stay valid."""
        project = project_for(session.cwd, Path(key).parent.name)
        old = self.sessions.get(key)
        if old is not None and old.project == project.slug:
            return project, old.slug
        taken = self.slugs.get(project.slug, {})
        slug = _slugify(Path(key).stem, 80) or "session"
        if taken.get(slug, key) != key:
            slug = f"{slug}-{_short_hash(key)}"
        return project, slug

    def _render_source(self, key: str, path: Path, digest: str) -> None:
        if not self.archive:
            session = parse_session(path)
            project, slug = self._identify(key, session)
            self._render(key, session, digest, project, slug, archived=False)
            return
        # Copy first and render from the copy, so the page matches the archived bytes.
        tmp, digest = _copy_to_temp(path, self.out / "raw")
        try:
            session = parse_session(tmp)
            project, slug = self._identify(key, session)
            dest = self.out / "raw" / project.slug / f"{slug}.jsonl"
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, dest)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
        self._render(key, session, digest, project, slug, archived=True)

    def _render_archived(self, key: str, entry: _Entry) -> bool:
        path = self._archive_path(entry)
        if path is None:
            return False
        project = ProjectInfo(entry.project, entry.project_name, entry.cwd)
        self._render(key, parse_session(path), entry.sha256, project, entry.slug, archived=True)
        return True

    def _archive_path(self, entry: _Entry) -> Path | None:
        path = self.out / "raw" / entry.project / f"{entry.slug}.jsonl"
        return path if entry.archived and path.is_file() else None

    def _render(
        self,
        key: str,
        session: Session,
        digest: str,
        project: ProjectInfo,
        slug: str,
        *,
        archived: bool,
    ) -> None:
        items = extract_items(session, decision_phrases=self.phrases)
        listed = any(isinstance(r, (UserRecord, AssistantRecord)) for r in session.records)
        entry = _Entry(
            sha256=digest,
            project=project.slug,
            project_name=project.name,
            cwd=project.cwd,
            slug=slug,
            archived=archived,
            listed=listed,
            title=session_title(session),
            start=_iso(session.start_time),
            end=_iso(session.end_time),
            branch=session.git_branch,
            counts=dict(Counter(item.kind for item in items)),
        )
        if listed:
            info = SessionInfo(
                slug=slug,
                title=entry.title,
                start=session.start_time,
                end=session.end_time,
                branch=entry.branch,
                counts=entry.counts,
                skipped_lines=session.skipped_lines,
            )
            page = render.render_session_page(project, info, items)
            _write_if_changed(self._project_dir(project.slug) / "sessions" / f"{slug}.html", page)
            self.docs.setdefault(project.slug, {})[slug] = render.search_docs(slug, items)

        old = self.sessions.get(key)
        if old is not None and (old.project, old.slug) != (project.slug, slug):
            self.slugs.get(old.project, {}).pop(old.slug, None)
            self.touched.add(old.project)
        self.sessions[key] = entry
        self.slugs.setdefault(project.slug, {})[slug] = key
        self.touched.add(project.slug)

    # ------------------------------------------------------------------ projects and index

    def _project_dir(self, slug: str) -> Path:
        return self.out / "projects" / slug

    def _listed(self) -> dict[str, list[tuple[str, _Entry]]]:
        """Listed sessions per project, newest first."""
        found: dict[str, list[tuple[str, _Entry]]] = {}
        for key, entry in self.sessions.items():
            if entry.listed:
                found.setdefault(entry.project, []).append((key, entry))
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        for members in found.values():
            members.sort(key=lambda m: (m[1].started or epoch, m[1].slug), reverse=True)
        return found

    def _write_projects(self) -> None:
        listed = self._listed()
        for slug in sorted(self.touched):
            members = listed.get(slug)
            if not members:
                continue
            first = members[0][1]
            project = ProjectInfo(slug, first.project_name, first.cwd)
            infos = [entry.info() for _, entry in members]
            docs = self._project_docs(project, members)
            directory = self._project_dir(slug)
            _write_if_changed(directory / DATA_FILE, render.project_data_js(project, infos, docs))
            _write_if_changed(directory / "index.html", render.render_project_page(project, infos))

    def _project_docs(
        self, project: ProjectInfo, members: list[tuple[str, _Entry]]
    ) -> list[dict[str, Any]]:
        """Search docs for every listed session of a project.

        Sessions not rendered in this run keep the docs already in the project's data
        file. If that file is missing or unreadable, those sessions are parsed again from
        the archive or the source log.
        """
        fresh = self.docs.setdefault(project.slug, {})
        kept: dict[str, list[dict[str, Any]]] = {}
        if any(entry.slug not in fresh for _, entry in members):
            try:
                text = (self._project_dir(project.slug) / DATA_FILE).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            found = render.read_project_docs(text)
            if found is None:
                for key, entry in members:
                    if entry.slug not in fresh:
                        path = self._archive_path(entry) or Path(key)
                        if path.is_file():
                            items = extract_items(
                                parse_session(path), decision_phrases=self.phrases
                            )
                            fresh[entry.slug] = render.search_docs(entry.slug, items)
            else:
                kept = found
        docs: list[dict[str, Any]] = []
        for _, entry in members:
            docs += fresh.get(entry.slug, kept.get(entry.slug, []))
        return docs

    def _write_shared(self, full: bool) -> None:
        for name, data in render.asset_files().items():
            _write_if_changed(self.out / "assets" / name, data)
        index = self.out / "index.html"
        if self.touched or full or not index.exists():
            listed = self._listed()
            projects = []
            for slug, members in listed.items():
                first = members[0][1]
                info = ProjectInfo(slug, first.project_name, first.cwd)
                projects.append((info, [entry.info() for _, entry in members]))
            _write_if_changed(index, render.render_index_page(projects))
