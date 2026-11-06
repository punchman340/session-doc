import json
import subprocess
import sys

import pytest

from session_doc import __version__
from session_doc.cli import main
from session_doc.config import default_config_path
from session_doc.render import DATA_PREFIX, DATA_SUFFIX


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "generate" in capsys.readouterr().out


def test_generate_help_lists_the_flags(capsys):
    with pytest.raises(SystemExit):
        main(["generate", "--help"])
    out = capsys.readouterr().out
    for flag in ("--source", "--out", "--no-archive", "--allow-in-git"):
        assert flag in out


def test_generate_prints_a_summary(source, tmp_path, capsys):
    out = tmp_path / "out"
    args = ["generate", "--source", str(source), "--out", str(out), "--allow-in-git"]
    assert main(args) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["Sessions: 4 new, 0 changed, 0 skipped", f"Output: {out / 'index.html'}"]
    assert main(args) == 0
    assert capsys.readouterr().out.splitlines()[0] == "Sessions: 0 new, 0 changed, 4 skipped"


def test_generate_no_archive_flag(source, tmp_path):
    out = tmp_path / "out"
    assert (
        main(
            [
                "generate",
                "--source",
                str(source),
                "--out",
                str(out),
                "--allow-in-git",
                "--no-archive",
            ]
        )
        == 0
    )
    assert not (out / "raw").exists()


def test_generate_refuses_git_work_tree_and_names_the_flag(source, tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert main(["generate", "--source", str(source), "--out", str(repo / "site")]) == 1
    err = capsys.readouterr().err
    assert "git work tree" in err and "--allow-in-git" in err
    assert (
        main(["generate", "--source", str(source), "--out", str(repo / "site"), "--allow-in-git"])
        == 0
    )


@pytest.mark.parametrize("use_env", [False, True])
def test_generate_defaults_to_claude_projects_and_home_output(
    source, isolated_home, monkeypatch, use_env
):
    claude = source.parent / "claude-config" if use_env else isolated_home / ".claude"
    claude.mkdir()
    source.rename(claude / "projects")
    if use_env:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    assert main(["generate", "--allow-in-git"]) == 0
    assert (isolated_home / "session-doc" / "index.html").is_file()


def test_generate_missing_default_source_is_an_error(capsys):
    assert main(["generate", "--allow-in-git"]) == 1
    assert "source directory not found" in capsys.readouterr().err


def test_generate_reads_decision_phrases_from_config(source, tmp_path):
    config = default_config_path()
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"decision_phrases": ["settled on"]}), encoding="utf-8")
    out = tmp_path / "out"
    assert main(["generate", "--source", str(source), "--out", str(out), "--allow-in-git"]) == 0
    (demo,) = [p for p in (out / "projects").iterdir() if p.name.startswith("demo-app-")]
    text = (demo / "search-index.js").read_text(encoding="utf-8")
    docs = json.loads(text[len(DATA_PREFIX) : -len(DATA_SUFFIX)])["docs"]
    settled = [d for d in docs if d["title"].startswith("We settled on")]
    assert [(d["kind"], d["inferred"]) for d in settled] == [("decision", True)]


def test_generate_bad_config_is_a_clear_error(source, tmp_path, capsys):
    config = default_config_path()
    config.parent.mkdir(parents=True)
    config.write_text("{oops", encoding="utf-8")
    assert main(["generate", "--source", str(source), "--out", str(tmp_path / "out")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("session-doc: error: config file") and "not valid JSON" in err
    assert not (tmp_path / "out").exists()


def test_python_dash_m_entry_point():
    out = subprocess.run(
        [sys.executable, "-m", "session_doc", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert __version__ in out.stdout
