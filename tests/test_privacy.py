"""Privacy rules from docs/DESIGN.md section 2 that the core can already check."""

import builtins
import io
import os
import socket
from pathlib import Path

from session_doc.extractor import extract_items, session_title
from session_doc.parser import parse_session

FIXTURES = Path(__file__).parent / "fixtures"


def _deny(*args, **kwargs):
    raise AssertionError("unexpected network or file access")


def test_parser_and_extractor_make_no_network_calls(monkeypatch):
    monkeypatch.setattr(socket, "socket", _deny)
    monkeypatch.setattr(socket, "create_connection", _deny)
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
