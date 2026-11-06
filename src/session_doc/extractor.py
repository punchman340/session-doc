"""Turn parsed session records into UI items (prompts, deliverables, decisions, status).

Rule-based and deterministic: no LLM, no network, and no file access. Everything is
derived from the records handed in; paths named in a log are never opened.
See docs/DESIGN.md section 5 and docs/DECISIONS.md for the rules and thresholds.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from session_doc.parser import (
    AnyRecord,
    AssistantRecord,
    AttachmentRecord,
    Session,
    TextBlock,
    ToolUseBlock,
    UserRecord,
)

Kind = Literal["prompt", "deliverable", "decision", "status"]
Confidence = Literal["explicit", "inferred"]

# Thresholds, tuned on real logs (see docs/DECISIONS.md, 2026-11-07 extractor entry).
LONG_ANSWER_CHARS = 1500  # an assistant message this long is a deliverable
STRUCTURED_MIN_CHARS = 300  # headings/table/checklist only count above this size
TITLE_MAX = 80

# English defaults. More phrases come from the caller (the user's config file).
DEFAULT_DECISION_PHRASES: tuple[str, ...] = ("decided", "go with")


@dataclass(frozen=True)
class Item:
    kind: Kind
    timestamp: datetime | None
    title: str
    body: str  # Markdown
    confidence: Confidence
    source_uuids: tuple[str, ...] = ()


# --------------------------------------------------------------------------- text helpers

_FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.M | re.S)
_HEADING = re.compile(r"^#{1,6}[ \t]+(\S.*?)[ \t]*#*[ \t]*$", re.M)
_TABLE = re.compile(
    r"^[^\n]*\|[^\n]*\n[ \t]*\|?(?:[ \t]*:?-{3,}:?[ \t]*\|)+(?:[ \t]*:?-{3,}:?[ \t]*)?$", re.M
)
_CHECKLIST = re.compile(r"^[ \t]*[-*+][ \t]+\[[ xX]\][ \t]", re.M)
_PASTED = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content[^>]*>", re.S)


def _truncate(text: str, limit: int = TITLE_MAX) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _headings(text: str) -> list[str]:
    return _HEADING.findall(_FENCE.sub("", text))


def _is_structured(text: str) -> bool:
    plain = _FENCE.sub("", text)
    return (
        len(_HEADING.findall(plain)) >= 2
        or bool(_TABLE.search(plain))
        or bool(_CHECKLIST.search(plain))
    )


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _prompt_title(text: str, limit: int = TITLE_MAX) -> str:
    return _truncate(_first_line(_PASTED.sub("", text)), limit) or "Pasted content"


def _code_span(text: str) -> str:
    """Wrap text in a Markdown code span that its own backticks cannot close."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _norm_path(path: str) -> str:
    return path.replace("\\", "/")


def _norm_quotes(text: str) -> str:
    return text.replace("\u2019", "'").replace("\u2018", "'")


def _compile_phrases(extra: Iterable[str]) -> re.Pattern[str]:
    seen: dict[str, None] = {}
    for p in (*DEFAULT_DECISION_PHRASES, *extra):
        words = _norm_quotes(p).lower().split()
        if words:
            seen[" ".join(words)] = None
    alts = [r"\s+".join(map(re.escape, p.split())) for p in sorted(seen, key=len, reverse=True)]
    alt = "|".join(alts)
    return re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.I)


# --------------------------------------------------------------------------- tool inputs


def _write_path(inp: dict[str, Any]) -> str | None:
    for key in ("file_path", "path"):
        if isinstance(inp.get(key), str):
            return inp[key]
    return None


def _write_content(inp: dict[str, Any]) -> str | None:
    for key in ("content", "file_text"):
        if isinstance(inp.get(key), str):
            return inp[key]
    return None


def _answer_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


# --------------------------------------------------------------------------- shell commands

_HEREDOC = re.compile(
    r"(?<!<)<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?=\n|\)|$)", re.S
)
_HEREDOC_REF = re.compile(r"\x00(\d+)\x00")
_GIT_COMMIT = re.compile(
    r"[ \t]*git(?:[ \t]+(?:-[cC][ \t]+\S+|--[\w-]+(?:=\S+)?))*[ \t]+commit(?![\w-])"
)
_MESSAGE_ARG = re.compile(
    r"(?<!\S)(?:--message|-[A-Za-z]*m)(?:=|[ \t]+)"
    r"(\"(?:\\.|[^\"\\])*\"|'[^']*'|[^\s\"';&|)]+)"
)


def _strip_heredocs(command: str) -> tuple[str, list[str]]:
    """Replace each heredoc body with a `NUL<n>NUL` marker. Return the command and bodies."""
    bodies: list[str] = []

    def keep(m: re.Match[str]) -> str:
        bodies.append(m.group(4))
        quote, word = m.group(1), m.group(2)
        return f"<<{quote}{word}{quote}{m.group(3)}\x00{len(bodies) - 1}\x00"

    return _HEREDOC.sub(keep, command), bodies


def _segments(command: str) -> list[str]:
    """Split a shell command at top-level `;`, `&`, `|`, and newlines.

    Quotes and `$(...)` are respected, so separators inside them do not split. This is a
    small approximation of shell syntax, good enough to tell commands apart.
    """
    out: list[str] = []
    stack: list[str] = []
    start = i = 0
    n = len(command)
    while i < n:
        c = command[i]
        top = stack[-1] if stack else None
        if c == "\\":
            i += 2
            continue
        if top == '"':
            if c == '"':
                stack.pop()
            elif command.startswith("$(", i):
                stack.append("(")
                i += 1
        elif c == "'":
            end = command.find("'", i + 1)
            i = n if end < 0 else end
        elif c == '"':
            stack.append('"')
        elif command.startswith("$(", i):
            stack.append("(")
            i += 1
        elif c == ")" and top == "(":
            stack.pop()
        elif c in ";&|\n" and not stack:
            out.append(command[start:i])
            start = i + 1
        i += 1
    out.append(command[start:])
    return out


def _git_commit_args(command: str) -> tuple[str, list[str]] | None:
    """Find the first command segment that is a `git commit`.

    Return the arguments after `commit` and the heredoc bodies they may refer to, or None.
    """
    stripped, bodies = _strip_heredocs(command)
    for segment in _segments(stripped):
        m = _GIT_COMMIT.match(segment)
        if m:
            return segment[m.end() :], bodies
    return None


def _unquote(arg: str) -> str:
    if len(arg) >= 2 and arg[0] == arg[-1] == '"':
        return re.sub(r"\\([\"\\$`])", r"\1", arg[1:-1])
    if len(arg) >= 2 and arg[0] == arg[-1] == "'":
        return arg[1:-1]
    return arg


def _commit_message(args: str, bodies: list[str]) -> str | None:
    """Join the `-m` / `--message` values in `args`. A value holding a heredoc marker is
    replaced by that heredoc's body."""
    parts: list[str] = []
    for arg in _MESSAGE_ARG.findall(args):
        ref = _HEREDOC_REF.search(arg)
        if ref and int(ref.group(1)) < len(bodies):
            text = bodies[int(ref.group(1))]
        else:
            text = _unquote(arg)
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts) or None


# --------------------------------------------------------------------------- extraction


@dataclass
class _PlanFile:
    """What the log reveals about a plan file: its last known content, or None if unknown."""

    content: str | None = None
    uuids: tuple[str, ...] = ()


def extract_items(
    session: Session | Iterable[AnyRecord],
    *,
    decision_phrases: Iterable[str] = (),
) -> list[Item]:
    """Extract UI items from a parsed session, in chronological order.

    `decision_phrases` are extra phrases (case-insensitive) that mark a typed prompt as an
    inferred decision, on top of the English defaults.
    """
    source = session.records if isinstance(session, Session) else session
    records = [r for r in source if not r.is_sidechain]
    phrase_re = _compile_phrases(decision_phrases)

    # Pre-pass: index tool calls and results, message groups, and plan files.
    tool_uses: dict[str, tuple[ToolUseBlock, AssistantRecord]] = {}
    results: dict[str, UserRecord] = {}
    failed: set[str] = set()
    groups: dict[str, list[AssistantRecord]] = {}
    plan_paths: set[str] = set()
    for i, rec in enumerate(records):
        if isinstance(rec, AssistantRecord):
            groups.setdefault(rec.message_id or f"_rec{i}", []).append(rec)
            for tu in rec.tool_uses:
                tool_uses[tu.id] = (tu, rec)
        elif isinstance(rec, UserRecord):
            for tr in rec.tool_results:
                results[tr.tool_use_id] = rec
                if tr.is_error:
                    failed.add(tr.tool_use_id)
        elif isinstance(rec, AttachmentRecord) and rec.attachment_type == "plan_mode_exit":
            path = rec.data.get("planFilePath")
            if isinstance(path, str):
                plan_paths.add(_norm_path(path))

    items: list[tuple[int, Item]] = []
    emitted_groups: set[str] = set()
    plan_files: dict[str, _PlanFile] = {}

    for i, rec in enumerate(records):
        found: list[Item] = []
        if isinstance(rec, UserRecord):
            found = _user_items(rec, tool_uses, phrase_re)
        elif isinstance(rec, AssistantRecord):
            key = rec.message_id or f"_rec{i}"
            if key not in emitted_groups:
                emitted_groups.add(key)
                answer = _long_answer(groups[key])
                if answer:
                    found.append(answer)
            for tu in rec.tool_uses:
                if tu.id not in failed:
                    found += _tool_items(rec, tu, plan_paths, plan_files, results)
        elif isinstance(rec, AttachmentRecord) and rec.attachment_type == "plan_mode_exit":
            found = [_plan_item(rec, plan_files)]
        items += ((i, item) for item in found)

    # Chronological: stable sort by timestamp; items without one inherit the previous.
    last = datetime.min.replace(tzinfo=timezone.utc)
    keyed: list[tuple[datetime, int, Item]] = []
    for order, item in items:
        last = item.timestamp or last
        keyed.append((last, order, item))
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [item for _, _, item in keyed]


def _user_items(
    rec: UserRecord,
    tool_uses: dict[str, tuple[ToolUseBlock, AssistantRecord]],
    phrase_re: re.Pattern[str],
) -> list[Item]:
    uuids: tuple[str, ...] = (rec.uuid,) if rec.uuid else ()

    answers = rec.tool_use_result.get("answers")
    asked = [
        tool_uses[tr.tool_use_id][1]
        for tr in rec.tool_results
        if tr.tool_use_id in tool_uses and tool_uses[tr.tool_use_id][0].name == "AskUserQuestion"
    ]
    if isinstance(answers, dict) and answers and asked:
        if asked[0].uuid:
            uuids = (asked[0].uuid, *uuids)
        pairs = [(str(q), _answer_text(a)) for q, a in answers.items()]
        title = f"{pairs[0][0]}: {pairs[0][1]}"
        if len(pairs) > 1:
            title = f"{len(pairs)} answers: {pairs[0][0]}"
        body = "\n".join(f"- **{q}**: {a}" for q, a in pairs)
        return [Item("decision", rec.timestamp, _truncate(title), body, "explicit", uuids)]

    if not rec.is_typed_prompt:
        return []
    text = rec.text
    if phrase_re.search(_norm_quotes(_PASTED.sub("", text))):
        kind: Kind = "decision"
        confidence: Confidence = "inferred"
    else:
        kind, confidence = "prompt", "explicit"
    return [Item(kind, rec.timestamp, _prompt_title(text), text, confidence, uuids)]


def _long_answer(group: list[AssistantRecord]) -> Item | None:
    text = "\n\n".join(
        b.text.strip()
        for r in group
        for b in r.blocks
        if isinstance(b, TextBlock) and b.text.strip()
    )
    n = len(text)
    if n < STRUCTURED_MIN_CHARS:
        return None
    if n < LONG_ANSWER_CHARS and not _is_structured(text):
        return None
    heads = _headings(text)
    title = _truncate(heads[0] if heads else _first_line(text))
    timestamp = next((r.timestamp for r in group if r.timestamp), None)
    uuids = tuple(r.uuid for r in group if r.uuid)
    return Item("deliverable", timestamp, title, text, "inferred", uuids)


def _tool_items(
    rec: AssistantRecord,
    tu: ToolUseBlock,
    plan_paths: set[str],
    plan_files: dict[str, _PlanFile],
    results: dict[str, UserRecord],
) -> list[Item]:
    uuids: tuple[str, ...] = (rec.uuid,) if rec.uuid else ()

    if tu.name == "Write":
        path = _write_path(tu.input)
        content = _write_content(tu.input)
        if path is None or content is None:
            return []
        norm = _norm_path(path)
        if norm in plan_paths:
            plan_files[norm] = _PlanFile(content, uuids)
            return []
        if not norm.lower().endswith(".md"):
            return []
        heads = _headings(content)
        title = _truncate(heads[0] if heads else norm.rsplit("/", 1)[-1])
        return [Item("deliverable", rec.timestamp, title, content, "explicit", uuids)]

    if tu.name in ("Edit", "MultiEdit"):
        _replay_edit(tu, uuids, plan_paths, plan_files, results)
        return []

    if tu.name == "Bash":
        command = tu.input.get("command")
        found = _git_commit_args(command) if isinstance(command, str) else None
        if found is None:
            return []
        message = _commit_message(*found)
        if message:
            title, body = _truncate(_first_line(message)), message
        else:
            title, body = "git commit", "Commit message could not be parsed from the command."
        return [Item("status", rec.timestamp, title, body, "explicit", uuids)]

    return []


def _replay_edit(
    tu: ToolUseBlock,
    uuids: tuple[str, ...],
    plan_paths: set[str],
    plan_files: dict[str, _PlanFile],
    results: dict[str, UserRecord],
) -> None:
    """Apply an `Edit` or `MultiEdit` on a plan file to its known content."""
    path = _write_path(tu.input)
    norm = _norm_path(path) if path is not None else None
    if norm is None or norm not in plan_paths:
        return
    known = plan_files.get(norm)
    content = known.content if known else None
    if content is None:
        result = results.get(tu.id)
        original = result.tool_use_result.get("originalFile") if result else None
        content = original if isinstance(original, str) else None
    edits = tu.input.get("edits") if tu.name == "MultiEdit" else [tu.input]
    for edit in edits if isinstance(edits, list) else [None]:
        old = edit.get("old_string") if isinstance(edit, dict) else None
        new = edit.get("new_string") if isinstance(edit, dict) else None
        if content is None or not isinstance(old, str) or not isinstance(new, str):
            content = None
            break
        count = -1 if edit.get("replace_all") is True else 1
        content = content.replace(old, new, count)
    plan_files[norm] = _PlanFile(content, (*(known.uuids if known else ()), *uuids))


def _plan_item(rec: AttachmentRecord, plan_files: dict[str, _PlanFile]) -> Item:
    raw = rec.data.get("planFilePath")
    path = raw if isinstance(raw, str) else ""
    norm = _norm_path(path)
    name = norm.rsplit("/", 1)[-1] or "plan"
    known = plan_files.get(norm) if path else None
    uuids = (*(known.uuids if known else ()), *((rec.uuid,) if rec.uuid else ()))
    if known and known.content is not None:
        heads = _headings(known.content)
        title = _truncate(heads[0] if heads else f"Plan: {name}")
        return Item("deliverable", rec.timestamp, title, known.content, "explicit", uuids)
    if path:
        body = (
            f"The plan body is not in the log. It was saved to {_code_span(path)}, "
            "which session-doc does not read."
        )
    else:
        body = "The plan body is not in the log, and the log does not say where it was saved."
    return Item("deliverable", rec.timestamp, f"Plan: {name}", body, "explicit", uuids)


# --------------------------------------------------------------------------- titles


def session_title(session: Session, max_len: int = TITLE_MAX) -> str:
    """The AI-generated title, else the first typed prompt, else the last prompt."""
    if session.title:
        return _truncate(session.title, max_len)
    for rec in session.records:
        if isinstance(rec, UserRecord) and rec.is_typed_prompt:
            return _prompt_title(rec.text, max_len)
    if session.last_prompt:
        return _prompt_title(session.last_prompt, max_len)
    return "Untitled session"
