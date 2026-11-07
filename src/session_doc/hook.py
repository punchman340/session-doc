"""Core of `session-doc hook`: update the docs for one session when it ends.

Claude Code runs a SessionEnd hook with a JSON object on stdin; `transcript_path` names
the session log. This module never raises and never prints on success: a hook must not
fail or slow a session. Errors are appended to `<out>/hook.log` as one line each, with a
timestamp, the stage, and the exception type and message. Log content is never written
there. Paths can appear in OS error messages; session text cannot.

A lock file in the output dir (`.hook.lock`, created exclusively) keeps two sessions that
end at once from writing the same files. A holder that died leaves a stale lock, which is
ignored after `STALE_LOCK_SECONDS`.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from session_doc.config import ConfigError, load_config
from session_doc.generate import (
    HOOK_FILES,
    GenerateError,
    check_out_dir,
    update_session,
)

LOG_FILE = "hook.log"
LOCK_FILE = ".hook.lock"
assert {LOG_FILE, LOCK_FILE} == HOOK_FILES

LOCK_WAIT_SECONDS = 10.0
STALE_LOCK_SECONDS = 120.0
HOOK_TIMEOUT_SECONDS = 30

_SAFE_ERRORS = (GenerateError, ConfigError, OSError, ValueError)


class LockHeld(Exception):
    """Another process holds the lock and did not release it in time."""


def hook_config() -> dict[str, object]:
    """The `settings.json` snippet that registers the hook."""
    return {
        "hooks": {
            "SessionEnd": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "session-doc hook",
                            "timeout": HOOK_TIMEOUT_SECONDS,
                        }
                    ]
                }
            ]
        }
    }


def print_config_text() -> str:
    return json.dumps(hook_config(), indent=2) + "\n"


def parse_hook_input(raw: bytes | str) -> Path | None:
    """The transcript path from the hook's stdin JSON, or None if the session is ignored.

    Raises `ValueError` if the input is not the expected JSON. Subagent transcripts (a
    `subagents` directory in the path) and missing or empty files are ignored.
    """
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"hook input is not valid JSON ({exc.__class__.__name__})") from None
    if not isinstance(data, dict):
        raise ValueError("hook input is not a JSON object")
    value = data.get("transcript_path")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("hook input has no transcript_path")
    path = Path(value).expanduser()
    if "subagents" in path.parts:
        return None
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
    except OSError:
        return None
    return path


@contextlib.contextmanager
def out_lock(
    out: Path, *, wait: float = LOCK_WAIT_SECONDS, stale: float = STALE_LOCK_SECONDS
) -> Iterator[None]:
    """Hold `<out>/.hook.lock`, waiting up to `wait` seconds. Raises `LockHeld` on timeout."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / LOCK_FILE
    deadline = time.monotonic() + wait
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - path.stat().st_mtime > stale:
                    path.unlink()
                    continue
            if time.monotonic() >= deadline:
                raise LockHeld(f"lock held: {path.name}") from None
            time.sleep(0.05)
            continue
        os.close(fd)
        break
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def _describe(exc: BaseException) -> str:
    """Exception type and, for types whose messages hold no log content, the message."""
    if isinstance(exc, _SAFE_ERRORS):
        return f"{exc.__class__.__name__}: {exc}"
    return exc.__class__.__name__


def _log(out: Path, stage: str, message: str) -> None:
    """Append one line to `<out>/hook.log`; fall back to stderr if that is not allowed."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{stamp} {stage}: {' '.join(message.split())}\n"
    try:
        out.mkdir(parents=True, exist_ok=True)
        with (out / LOG_FILE).open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        with contextlib.suppress(Exception):
            sys.stderr.write(f"session-doc hook: {line}")


def run_hook(
    raw: bytes | str,
    out: Path,
    *,
    config_path: Path,
    allow_in_git: bool = False,
    lock_wait: float = LOCK_WAIT_SECONDS,
) -> None:
    """Update the docs for the session named in the hook input. Never raises."""
    out = Path(out).expanduser().resolve()
    stage = "input"
    try:
        try:
            transcript = parse_hook_input(raw)
        except ValueError as exc:
            _log_checked(out, allow_in_git, stage, _describe(exc))
            return
        if transcript is None:
            return
        stage = "check"
        try:
            check_out_dir(out, allow_in_git)  # same refusals as `generate`, before any write
        except GenerateError as exc:
            _stderr(stage, _describe(exc))  # never write a log into a refused dir
            return
        stage = "config"
        config = load_config(config_path)
        stage = "lock"
        with out_lock(out, wait=lock_wait):
            stage = "update"
            update_session(
                transcript,
                out,
                allow_in_git=allow_in_git,
                decision_phrases=config.decision_phrases,
            )
    except LockHeld:
        _log(out, "skipped", "another session update holds the lock")
    except Exception as exc:  # a hook must never fail the session
        _log(out, stage, _describe(exc))


def _log_checked(out: Path, allow_in_git: bool, stage: str, message: str) -> None:
    """Log to `<out>/hook.log` only if the output dir may be written to."""
    try:
        check_out_dir(out, allow_in_git)
    except GenerateError as exc:
        _stderr(stage, f"{message}; {_describe(exc)}")
        return
    _log(out, stage, message)


def _stderr(stage: str, message: str) -> None:
    with contextlib.suppress(Exception):
        sys.stderr.write(f"session-doc hook: {stage}: {' '.join(message.split())}\n")
