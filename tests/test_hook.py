import io
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from session_doc import hook as hook_mod
from session_doc.cli import main
from session_doc.hook import LockHeld, hook_config, out_lock, parse_hook_input, run_hook

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def transcript(tmp_path) -> Path:
    path = tmp_path / "logs" / "proj" / "basic_session.jsonl"
    path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "basic_session.jsonl", path)
    return path


def payload(path: Path) -> str:
    return json.dumps(
        {"session_id": "s1", "hook_event_name": "SessionEnd", "transcript_path": str(path)}
    )


def call(raw, out, **kwargs):
    kwargs.setdefault("allow_in_git", True)
    kwargs.setdefault("lock_wait", 0.2)
    run_hook(raw, out, config_path=Path("missing-config.json"), **kwargs)


def log_text(out: Path) -> str:
    path = out / "hook.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ------------------------------------------------------------------------- stdin parsing


def test_parse_hook_input_returns_the_transcript_path(transcript):
    assert parse_hook_input(payload(transcript)) == transcript
    assert parse_hook_input(payload(transcript).encode("utf-8")) == transcript
    assert parse_hook_input(b"\xef\xbb\xbf" + payload(transcript).encode()) == transcript


@pytest.mark.parametrize("raw", ["", "not json", "[]", "{}", '{"transcript_path": 3}'])
def test_parse_hook_input_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        parse_hook_input(raw)


def test_parse_hook_input_ignores_subagent_missing_and_empty(tmp_path, transcript):
    sub = tmp_path / "logs" / "proj" / "s" / "subagents" / "agent-1.jsonl"
    sub.parent.mkdir(parents=True)
    shutil.copy(transcript, sub)
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    for path in (sub, empty, tmp_path / "missing.jsonl"):
        assert parse_hook_input(payload(path)) is None


# ------------------------------------------------------------------------- one session


def test_hook_updates_only_its_session(transcript, tmp_path, capsys):
    out = tmp_path / "out"
    other = transcript.with_name("extractor_session.jsonl")
    shutil.copy(FIXTURES / "extractor_session.jsonl", other)

    call(payload(transcript), out)
    assert capsys.readouterr() == ("", "")
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert list(state["sessions"]) == [transcript.resolve().as_posix()]
    (project,) = (out / "projects").iterdir()
    assert (project / "index.html").is_file()
    assert (project / "search-index.js").is_file()
    assert [p.name for p in (project / "sessions").iterdir()] == ["basic_session.html"]
    assert (out / "raw" / project.name / "basic_session.jsonl").is_file()
    assert (out / "index.html").is_file()
    assert not (out / "hook.log").exists()
    assert not (out / ".hook.lock").exists()

    # A second session of the same project joins the same index and data file.
    call(payload(other), out)
    names = sorted(p.name for p in (project / "sessions").iterdir())
    assert names == ["basic_session.html", "extractor_session.html"]
    data = (project / "search-index.js").read_text(encoding="utf-8")
    assert "basic_session" in data and "extractor_session" in data


def test_hook_skips_an_unchanged_log_and_picks_up_a_grown_one(transcript, tmp_path):
    out = tmp_path / "out"
    call(payload(transcript), out)
    before = (out / "state.json").read_bytes()
    call(payload(transcript), out)
    assert (out / "state.json").read_bytes() == before

    record = {
        "type": "user",
        "uuid": "u-extra",
        "cwd": "/work/demo-app",
        "timestamp": "2026-02-01T11:00:00Z",
        "promptSource": "typed",
        "origin": {"kind": "human"},
        "message": {"role": "user", "content": "one more question"},
    }
    with transcript.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    call(payload(transcript), out)
    assert (out / "state.json").read_bytes() != before
    pages = "".join(p.read_text(encoding="utf-8") for p in (out / "projects").rglob("*.html"))
    assert "one more question" in pages


def test_hook_keeps_an_old_fingerprint_so_generate_renders_the_rest(
    source, transcript, tmp_path, run
):
    out = tmp_path / "out"
    run(source, out)
    state_path = out / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["fingerprint"]["version"] = "0.0.0-old"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    call(payload(transcript), out)
    kept = json.loads(state_path.read_text(encoding="utf-8"))["fingerprint"]
    assert kept["version"] == "0.0.0-old"
    # The four source logs plus the hook's session, rendered again from its archive.
    assert run(source, out).rerendered == 5


def test_hook_ignored_sessions_create_nothing(tmp_path):
    out = tmp_path / "out"
    call(payload(tmp_path / "missing.jsonl"), out)
    assert not out.exists()


# ------------------------------------------------------------------------- failure modes


@pytest.mark.parametrize("raw", ["", "not json", "{}", b"\xff\xfe\x00"])
def test_bad_input_is_logged_and_does_not_raise(raw, tmp_path, capsys):
    out = tmp_path / "out"
    call(raw, out)
    assert capsys.readouterr() == ("", "")
    (line,) = log_text(out).splitlines()
    assert line.split()[0].endswith("Z")
    assert " input: " in line


def test_main_exits_zero_on_bad_input(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"garbage")))
    assert main(["hook", "--out", str(out), "--allow-in-git"]) == 0
    assert capsys.readouterr() == ("", "")
    assert " input: " in log_text(out)


def test_errors_are_logged_without_log_content(transcript, tmp_path):
    out = tmp_path / "out"
    config = tmp_path / "config.json"
    config.write_text("{not json", encoding="utf-8")
    run_hook(payload(transcript), out, config_path=config, allow_in_git=True)
    text = log_text(out)
    assert " config: ConfigError" in text
    assert "demo-app" not in text


def test_unexpected_errors_log_only_the_type(transcript, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret session text")

    monkeypatch.setattr(hook_mod, "update_session", boom)
    out = tmp_path / "out"
    call(payload(transcript), out)
    text = log_text(out)
    assert "RuntimeError" in text and "secret" not in text
    assert not (out / ".hook.lock").exists()


def test_git_work_tree_is_refused_without_writing(transcript, tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    out = repo / "docs-out"
    call(payload(transcript), out, allow_in_git=False)
    assert not out.exists()
    assert "git work tree" in capsys.readouterr().err


def test_foreign_directory_is_refused_without_writing(transcript, tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "notes.txt").write_text("mine", encoding="utf-8")
    call(payload(transcript), out)
    assert [p.name for p in out.iterdir()] == ["notes.txt"]
    assert "not create it" in capsys.readouterr().err


# ------------------------------------------------------------------------- lock


def test_lock_contention_skips_and_logs(transcript, tmp_path):
    out = tmp_path / "out"
    with out_lock(out):
        start = time.monotonic()
        call(payload(transcript), out, lock_wait=0.3)
        assert time.monotonic() - start >= 0.25
        assert (out / ".hook.lock").exists()  # still the holder's
    assert "skipped: another session update holds the lock" in log_text(out)
    assert not (out / "state.json").exists()
    assert not (out / ".hook.lock").exists()

    # Nothing was corrupted: the next hook run works.
    call(payload(transcript), out)
    assert (out / "state.json").is_file()


def test_lock_waits_for_a_holder_that_releases(tmp_path):
    out = tmp_path / "out"
    acquired = threading.Event()

    def hold():
        with out_lock(out):
            acquired.set()
            time.sleep(0.3)

    thread = threading.Thread(target=hold)
    thread.start()
    assert acquired.wait(5)
    with out_lock(out, wait=5):
        pass
    thread.join()


def test_stale_lock_is_ignored(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    lock = out / ".hook.lock"
    lock.write_bytes(b"")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    with out_lock(out, wait=0.1):
        pass
    assert not lock.exists()


def test_lock_held_raises(tmp_path):
    with out_lock(tmp_path / "out"), pytest.raises(LockHeld), out_lock(tmp_path / "out", wait=0.1):
        pass


# ------------------------------------------------------------------------- print-config


def test_print_config_is_valid_json(capsys):
    assert main(["hook", "--print-config"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == hook_config()
    (group,) = printed["hooks"]["SessionEnd"]
    (command,) = group["hooks"]
    assert command == {"type": "command", "command": "session-doc hook", "timeout": 30}


def test_print_config_writes_nothing(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    assert main(["hook", "--print-config"]) == 0
    assert list(empty.iterdir()) == []
