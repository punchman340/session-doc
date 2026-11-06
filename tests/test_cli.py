import subprocess
import sys

import pytest

from session_doc import __version__
from session_doc.cli import main


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "generate" in capsys.readouterr().out


def test_generate_is_a_stub(capsys):
    assert main(["generate"]) == 1
    assert "not implemented" in capsys.readouterr().err


def test_python_dash_m_entry_point():
    out = subprocess.run(
        [sys.executable, "-m", "session_doc", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert __version__ in out.stdout
