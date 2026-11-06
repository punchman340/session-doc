import sys
from pathlib import Path

import pytest

from session_doc.config import Config, ConfigError, default_config_path, load_config


def write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)
    return path


def test_missing_config_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope" / "config.json") == Config()


def test_decision_phrases_are_read(tmp_path):
    path = write(tmp_path / "c.json", '{"decision_phrases": ["settled on", "agreed"]}')
    assert load_config(path).decision_phrases == ("settled on", "agreed")


def test_unknown_keys_are_ignored(tmp_path):
    path = write(tmp_path / "c.json", '{"future_option": true}')
    assert load_config(path) == Config()


def test_byte_order_mark_is_accepted(tmp_path):
    path = write(tmp_path / "c.json", '{"decision_phrases": ["agreed"]}', encoding="utf-8-sig")
    assert load_config(path).decision_phrases == ("agreed",)


def test_bad_json_is_a_clear_error(tmp_path):
    path = write(tmp_path / "c.json", '{"decision_phrases": ["a",]}')
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    message = str(exc.value)
    assert str(path) in message and "not valid JSON" in message and "line 1" in message


@pytest.mark.parametrize(
    "text",
    ['["a"]', '{"decision_phrases": "a"}', '{"decision_phrases": ["a", 1]}'],
)
def test_wrong_shapes_are_errors(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path / "c.json", text))


def test_default_path_on_windows_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert default_config_path() == tmp_path / "Roaming" / "session-doc" / "config.json"


def test_default_path_elsewhere_uses_dot_config(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_config_path() == tmp_path / ".config" / "session-doc" / "config.json"
