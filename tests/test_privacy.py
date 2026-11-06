"""Privacy rules from docs/DESIGN.md section 2."""

import builtins
import io
import os
import socket
from html.parser import HTMLParser
from pathlib import Path

import pytest

from session_doc.extractor import extract_items, session_title
from session_doc.parser import parse_session
from session_doc.render import CSP

FIXTURES = Path(__file__).parent / "fixtures"


def _deny(*args, **kwargs):
    raise AssertionError("unexpected network or file access")


@pytest.fixture
def no_network(monkeypatch):
    for name in ("socket", "create_connection", "getaddrinfo", "gethostbyname"):
        monkeypatch.setattr(socket, name, _deny)


def test_parser_and_extractor_make_no_network_calls(no_network):
    for path in FIXTURES.glob("*.jsonl"):
        extract_items(parse_session(path))


def test_extractor_opens_no_files(monkeypatch):
    sessions = [parse_session(p) for p in FIXTURES.glob("*.jsonl")]
    with monkeypatch.context() as m:
        for target, name in ((builtins, "open"), (io, "open"), (os, "open"), (Path, "open")):
            m.setattr(target, name, _deny)
        for session in sessions:
            extract_items(session)
            session_title(session)


def test_generate_makes_no_network_calls(source, tmp_path, run, no_network):
    run(source, tmp_path / "out")
    run(source, tmp_path / "out", decision_phrases=["settled on"])


class _Head(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {k: v or "" for k, v in attrs}))


def _pages(out: Path) -> list[Path]:
    pages = sorted(out.rglob("*.html"))
    assert len(pages) >= 7
    return pages


def test_every_page_carries_the_csp_before_anything_loads(source, tmp_path, run):
    out = tmp_path / "out"
    run(source, out)
    for page in _pages(out):
        parser = _Head()
        parser.feed(page.read_text(encoding="utf-8"))
        tags = parser.tags
        # <html>, <head>, <meta charset>, then the CSP.
        assert tags[2] == ("meta", {"charset": "utf-8"}), page
        assert tags[3] == ("meta", {"http-equiv": "Content-Security-Policy", "content": CSP}), page
        loads = [i for i, (t, _) in enumerate(tags) if t in ("link", "script")]
        assert all(i > 3 for i in loads), page


def test_csp_blocks_everything_but_local_scripts_and_styles():
    directives = dict(d.strip().split(" ", 1) for d in CSP.split(";"))
    assert directives["default-src"] == "'none'"
    assert directives["script-src"] == "'self' file:"
    assert directives["style-src"] == "'self' file:"
    assert "unsafe-inline" not in CSP and "unsafe-eval" not in CSP


def test_pages_load_only_relative_local_files(source, tmp_path, run):
    out = tmp_path / "out"
    run(source, out)
    for page in _pages(out):
        parser = _Head()
        parser.feed(page.read_text(encoding="utf-8"))
        for tag, attrs in parser.tags:
            if tag in ("script", "link"):
                url = attrs.get("src") or attrs.get("href") or ""
                assert url and ":" not in url and not url.startswith("/"), (page, url)
                assert (page.parent / url).resolve().is_file(), (page, url)
            assert tag not in ("img", "iframe", "object", "embed", "style"), (page, tag)
            assert "style" not in attrs, (page, tag)
