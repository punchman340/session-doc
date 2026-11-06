import json

import pytest

from session_doc.parser import Session, parse_session


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
