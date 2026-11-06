import json
import shutil
from pathlib import Path

import pytest

from session_doc.generate import GenerateResult, generate
from session_doc.parser import Session, parse_session

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point every default location (config, Claude dir, output) into tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


@pytest.fixture
def make_session(tmp_path):
    """Write the given record dicts to a JSONL file and parse it."""
    count = 0

    def make(*records: dict) -> Session:
        nonlocal count
        count += 1
        path = tmp_path / f"session-{count}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return parse_session(path)

    return make


@pytest.fixture
def source(tmp_path) -> Path:
    """A source tree shaped like Claude Code's projects dir, built from the fixtures.

    proj-a holds three sessions of /work/demo-app plus a subagent log that must be
    skipped; proj-b holds the HTML injection session.
    """
    root = tmp_path / "source"
    a = root / "proj-a"
    (a / "basic_session" / "subagents").mkdir(parents=True)
    for name in ("basic_session", "extractor_session", "malformed"):
        shutil.copy(FIXTURES / f"{name}.jsonl", a / f"{name}.jsonl")
    shutil.copy(
        FIXTURES / "basic_session.jsonl", a / "basic_session" / "subagents" / "agent-1.jsonl"
    )
    (root / "proj-b").mkdir()
    shutil.copy(FIXTURES / "html_injection.jsonl", root / "proj-b" / "html_injection.jsonl")
    return root


@pytest.fixture
def run():
    """Call `generate`, allowing git work trees by default.

    The machine's temp dir may sit inside a git work tree (a home directory tracked as
    a dotfiles repo, for example), so tests that are not about that check opt out of it.
    """

    def call(source: Path, out: Path, **kwargs) -> GenerateResult:
        kwargs.setdefault("allow_in_git", True)
        return generate(source, out, **kwargs)

    return call
