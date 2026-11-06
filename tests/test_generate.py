import json
import shutil
from pathlib import Path

import pytest

from session_doc import generate as gen
from session_doc.generate import (
    GenerateError,
    GitWorkTreeError,
    find_git_work_tree,
    generate,
    project_for,
    scan_sources,
)
from session_doc.render import DATA_PREFIX, DATA_SUFFIX, asset_files

FIXTURES = Path(__file__).parent / "fixtures"


def snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    """Every file under root with its mtime and content."""
    return {
        p.relative_to(root).as_posix(): (p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def project_dir(out: Path, prefix: str) -> Path:
    (found,) = [p for p in (out / "projects").iterdir() if p.name.startswith(prefix)]
    return found


def data(project: Path) -> dict:
    text = (project / "search-index.js").read_text(encoding="utf-8")
    assert text.startswith(DATA_PREFIX) and text.endswith(DATA_SUFFIX)
    return json.loads(text[len(DATA_PREFIX) : -len(DATA_SUFFIX)])


def typed_prompt(text: str, uuid: str, ts: str = "2026-02-01T10:00:00Z") -> str:
    record = {
        "type": "user",
        "uuid": uuid,
        "cwd": "/work/demo-app",
        "timestamp": ts,
        "promptSource": "typed",
        "origin": {"kind": "human"},
        "message": {"role": "user", "content": text},
    }
    return json.dumps(record) + "\n"


# ------------------------------------------------------------------------- end to end


def test_generate_end_to_end(source, tmp_path, run):
    out = tmp_path / "out"
    result = run(source, out)
    assert (result.new, result.changed, result.skipped, result.rerendered) == (4, 0, 0, 0)
    assert result.index == out / "index.html"

    demo = project_dir(out, "demo-app-")
    inj = project_dir(out, "a-b-i-app-")
    files = set(snapshot(out))
    expected = {
        "index.html",
        "state.json",
        *(f"assets/{name}" for name in asset_files()),
        *(f"projects/{p.name}/{f}" for p in (demo, inj) for f in ("index.html", "search-index.js")),
        *(
            f"projects/{demo.name}/sessions/{s}.html"
            for s in ("basic_session", "extractor_session", "malformed")
        ),
        f"projects/{inj.name}/sessions/html_injection.html",
        *(
            f"raw/{demo.name}/{s}.jsonl"
            for s in ("basic_session", "extractor_session", "malformed")
        ),
        f"raw/{inj.name}/html_injection.jsonl",
    }
    assert files == expected

    index = (out / "index.html").read_text(encoding="utf-8")
    assert f"projects/{demo.name}/index.html" in index and "demo-app" in index
    project_page = (demo / "index.html").read_text(encoding="utf-8")
    for title in ("Demo app planning", "Rollout notes", "Still parsed."):
        assert title in project_page
    session_page = (demo / "sessions" / "basic_session.html").read_text(encoding="utf-8")
    assert (
        "Write a short plan for the demo app." in session_page
        and "Which storage?: SQLite" in session_page
    )

    d = data(demo)
    assert d["project"] == {"slug": demo.name, "name": "demo-app", "cwd": "/work/demo-app"}
    # Newest first.
    assert [s["slug"] for s in d["sessions"]] == ["extractor_session", "malformed", "basic_session"]
    assert len(d["docs"]) == sum(s["items"] for s in d["sessions"])
    assert {doc["session"] for doc in d["docs"]} == {
        "extractor_session",
        "malformed",
        "basic_session",
    }


def test_subagent_logs_are_skipped(source):
    names = [p.relative_to(source).as_posix() for p in scan_sources(source)]
    assert names == [
        "proj-a/basic_session.jsonl",
        "proj-a/extractor_session.jsonl",
        "proj-a/malformed.jsonl",
        "proj-b/html_injection.jsonl",
    ]


def test_archive_is_a_byte_copy_and_pages_render_from_it(source, tmp_path, run):
    out = tmp_path / "out"
    run(source, out)
    demo = project_dir(out, "demo-app-")
    for name in ("basic_session", "extractor_session", "malformed"):
        assert (demo.parent.parent / "raw" / demo.name / f"{name}.jsonl").read_bytes() == (
            source / "proj-a" / f"{name}.jsonl"
        ).read_bytes()


def test_no_archive_writes_no_raw_dir(source, tmp_path, run):
    out = tmp_path / "out"
    run(source, out, archive=False)
    assert not (out / "raw").exists()
    assert (project_dir(out, "demo-app-") / "sessions" / "basic_session.html").is_file()


def test_empty_source_still_writes_a_site(tmp_path, run):
    (tmp_path / "empty").mkdir()
    result = run(tmp_path / "empty", tmp_path / "out")
    assert (result.new, result.skipped) == (0, 0)
    assert "No sessions found yet." in (tmp_path / "out" / "index.html").read_text(encoding="utf-8")


def test_log_without_conversation_gets_no_page(tmp_path, run):
    src = tmp_path / "src" / "p"
    src.mkdir(parents=True)
    (src / "empty.jsonl").write_text('{"type": "mode", "mode": "normal"}\n', encoding="utf-8")
    result = run(tmp_path / "src", tmp_path / "out")
    assert result.new == 1
    assert not (tmp_path / "out" / "projects").exists()
    assert "No sessions found yet." in (tmp_path / "out" / "index.html").read_text(encoding="utf-8")


# ------------------------------------------------------------------------- incremental


def test_second_run_skips_and_rewrites_nothing(source, tmp_path, run):
    out = tmp_path / "out"
    run(source, out)
    before = snapshot(out)
    result = run(source, out)
    assert (result.new, result.changed, result.skipped, result.rerendered) == (0, 0, 4, 0)
    assert snapshot(out) == before


def test_changed_session_is_rendered_again(source, tmp_path, run):
    out = tmp_path / "out"
    run(source, out)
    demo = project_dir(out, "demo-app-")
    before = snapshot(out)
    with (source / "proj-a" / "malformed.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(typed_prompt("Appended later prompt.", "late-1"))
    result = run(source, out)
    assert (result.new, result.changed, result.skipped) == (0, 1, 3)
    page = (demo / "sessions" / "malformed.html").read_text(encoding="utf-8")
    assert "Appended later prompt." in page
    assert any(doc["title"] == "Appended later prompt." for doc in data(demo)["docs"])
    after = snapshot(out)
    for name in ("basic_session", "extractor_session"):
        key = f"projects/{demo.name}/sessions/{name}.html"
        assert after[key] == before[key]


def test_new_session_changes_only_project_files(source, tmp_path, run):
    out = tmp_path / "out"
    run(source, out)
    demo = project_dir(out, "demo-app-")
    before = snapshot(out)
    (source / "proj-a" / "newer.jsonl").write_text(
        typed_prompt("A brand new session.", "n-1", "2026-06-01T00:00:00Z"), encoding="utf-8"
    )
    result = run(source, out)
    assert (result.new, result.skipped) == (1, 4)
    changed = {k for k, v in snapshot(out).items() if before.get(k) != v}
    assert changed == {
        "index.html",
        "state.json",
        f"projects/{demo.name}/index.html",
        f"projects/{demo.name}/search-index.js",
        f"projects/{demo.name}/sessions/newer.html",
        f"raw/{demo.name}/newer.jsonl",
    }
    assert data(demo)["sessions"][0]["slug"] == "newer"


def test_pruned_source_keeps_its_page_and_search_docs(source, tmp_path, run):
    out = tmp_path / "out"
    run(source, out)
    demo = project_dir(out, "demo-app-")
    page = demo / "sessions" / "basic_session.html"
    page_bytes = page.read_bytes()
    docs_before = [d for d in data(demo)["docs"] if d["session"] == "basic_session"]
    (source / "proj-a" / "basic_session.jsonl").unlink()
    # Another session changes, so the project data file is written again.
    with (source / "proj-a" / "malformed.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(typed_prompt("Another prompt.", "late-2"))
    result = run(source, out)
    assert (result.changed, result.skipped) == (1, 2)
    assert page.read_bytes() == page_bytes
    d = data(demo)
    assert "basic_session" in [s["slug"] for s in d["sessions"]]
    assert [x for x in d["docs"] if x["session"] == "basic_session"] == docs_before
    assert "Demo app planning" in (demo / "index.html").read_text(encoding="utf-8")


def test_config_change_rerenders_pruned_sessions_from_the_archive(source, tmp_path, run):
    out = tmp_path / "out"
    run(source, out)
    demo = project_dir(out, "demo-app-")
    (source / "proj-a" / "extractor_session.jsonl").unlink()
    result = run(source, out, decision_phrases=["settled on"])
    assert (result.new, result.changed, result.skipped, result.rerendered) == (0, 0, 0, 4)
    docs = [d for d in data(demo)["docs"] if d["title"].startswith("We settled on")]
    assert [d["kind"] for d in docs] == ["decision"]


def test_missing_data_file_is_rebuilt_from_the_archive(source, tmp_path, run):
    out = tmp_path / "out"
    run(source, out)
    demo = project_dir(out, "demo-app-")
    full = data(demo)
    (demo / "search-index.js").unlink()
    (source / "proj-a" / "basic_session.jsonl").unlink()  # only in the archive now
    with (source / "proj-a" / "malformed.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(typed_prompt("One more.", "late-3"))
    run(source, out)
    rebuilt = data(demo)
    assert {d["session"] for d in rebuilt["docs"]} == {d["session"] for d in full["docs"]}
    assert len(rebuilt["docs"]) == len(full["docs"]) + 1


def test_log_deleted_during_the_run_is_skipped(source, tmp_path, run, monkeypatch):
    real = gen._sha256_file

    def vanish(path):
        if path.name == "malformed.jsonl":
            raise FileNotFoundError(path)
        return real(path)

    monkeypatch.setattr(gen, "_sha256_file", vanish)
    result = run(source, tmp_path / "out")
    assert (result.new, result.skipped) == (3, 0)


def test_no_temp_files_are_left_behind(source, tmp_path, run):
    out = tmp_path / "out"
    run(source, out)
    with (source / "proj-a" / "malformed.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(typed_prompt("Later.", "late-4"))
    run(source, out)
    assert not [p for p in out.rglob("*") if p.name.endswith(".tmp") or p.name.startswith(".")]


def test_source_logs_are_never_modified(source, tmp_path, run):
    before = snapshot(source)
    run(source, tmp_path / "out")
    run(source, tmp_path / "out", decision_phrases=["settled on"])
    assert snapshot(source) == before


# ------------------------------------------------------------------------- refusals


def test_output_inside_git_work_tree_is_refused(source, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(GitWorkTreeError) as exc:
        generate(source, repo / "site")
    assert exc.value.repo == repo
    assert not (repo / "site").exists()


def test_git_file_marks_a_work_tree_too(source, tmp_path):
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n", encoding="utf-8")
    assert find_git_work_tree(repo / "a" / "b") == repo
    with pytest.raises(GitWorkTreeError):
        generate(source, repo / "site")


def test_allow_in_git_writes_inside_a_work_tree(source, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    result = generate(source, repo / "site", allow_in_git=True)
    assert result.new == 4 and (repo / "site" / "index.html").is_file()


def test_outside_a_work_tree_no_flag_is_needed(source, tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "find_git_work_tree", lambda path: None)
    assert generate(source, tmp_path / "out").new == 4


def test_non_empty_foreign_directory_is_refused(source, tmp_path, run):
    out = tmp_path / "docs"
    out.mkdir()
    (out / "index.html").write_text("mine", encoding="utf-8")
    with pytest.raises(GenerateError, match="not empty"):
        run(source, out)
    assert (out / "index.html").read_text(encoding="utf-8") == "mine"
    assert sorted(p.name for p in out.iterdir()) == ["index.html"]


@pytest.mark.parametrize("inside", ["out-in-source", "source-in-out"])
def test_overlapping_source_and_output_are_refused(source, tmp_path, run, inside):
    out = source / "site" if inside == "out-in-source" else source.parent
    with pytest.raises(GenerateError, match="must not contain each other"):
        run(source, out)


def test_missing_source_dir_is_an_error(tmp_path, run):
    with pytest.raises(GenerateError, match="source directory not found"):
        run(tmp_path / "nope", tmp_path / "out")


def test_unreadable_state_file_is_an_error(source, tmp_path, run):
    out = tmp_path / "out"
    out.mkdir()
    (out / "state.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(GenerateError, match="cannot read"):
        run(source, out)


# ------------------------------------------------------------------------- names


def test_project_slug_is_safe_and_unique_per_path():
    odd = project_for("/work/a&b <i>app", "x")
    assert odd.name == "a&b <i>app"
    assert odd.slug.startswith("a-b-i-app-") and odd.slug.replace("-", "").isalnum()
    assert project_for("/one/app", "x").slug != project_for("/two/app", "x").slug
    assert project_for("C:\\Dev\\App", "x").slug == project_for("c:/dev/app/", "x").slug
    assert project_for("C:\\Dev\\App", "x").name == "App"


def test_project_without_cwd_uses_the_log_directory():
    project = project_for(None, "C--work-demo")
    assert project.name == "C--work-demo" and project.cwd == ""
    assert project.slug.startswith("c--work-demo-")


def test_session_slugs_stay_unique_within_a_project(tmp_path, run):
    src = tmp_path / "src"
    for sub in ("a", "b"):
        (src / sub).mkdir(parents=True)
        shutil.copy(FIXTURES / "basic_session.jsonl", src / sub / "same.jsonl")
    run(src, tmp_path / "out")
    pages = sorted(
        p.name for p in (project_dir(tmp_path / "out", "demo-app-") / "sessions").iterdir()
    )
    assert len(pages) == 2 and "same.html" in pages
    assert [p for p in pages if p != "same.html"][0].startswith("same-")
