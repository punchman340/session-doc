from pathlib import Path

import pytest

from session_doc.extractor import (
    LONG_ANSWER_CHARS,
    Item,
    extract_items,
    session_title,
)
from session_doc.parser import parse_session

FIXTURES = Path(__file__).parent / "fixtures"
BASIC = FIXTURES / "basic_session.jsonl"
EXTRACT = FIXTURES / "extractor_session.jsonl"


@pytest.fixture(scope="module")
def items() -> list[Item]:
    return extract_items(parse_session(EXTRACT))


def by_title(items, prefix):
    return [i for i in items if i.title.startswith(prefix)]


def one(items, prefix) -> Item:
    found = by_title(items, prefix)
    assert len(found) == 1, [i.title for i in items]
    return found[0]


# ------------------------------------------------------------------------- prompts


def test_typed_prompt_is_explicit_prompt(items):
    p = one(items, "Please draft the rollout notes")
    assert (p.kind, p.confidence) == ("prompt", "explicit")
    assert p.source_uuids == ("x-0001",)


def test_prompt_without_prompt_source_uses_fallback(items):
    assert one(items, "Next, update the docs").kind == "prompt"


def test_pasted_content_is_kept_and_does_not_trigger_decision(items):
    p = one(items, "Review this")
    assert p.kind == "prompt"
    assert '<pasted_content id="c1">' in p.body and "decided to wait" in p.body


def test_basic_session_prompts_and_noise():
    got = extract_items(parse_session(BASIC))
    prompts = [i.title for i in got if i.kind == "prompt"]
    # meta, task notification, compact summary and tool results are not prompts
    assert prompts == ["Write a short plan for the demo app.", "Thanks, now add tests."]


def test_sidechain_records_are_skipped(items):
    assert not by_title(items, "Sidechain")
    assert not [i for i in items if "Side" in i.body and "# Side" in i.body]


# ------------------------------------------------------------------------- deliverables


def test_write_with_path_and_file_text(items):
    d = one(items, "Rollout notes")
    assert (d.kind, d.confidence) == ("deliverable", "explicit")
    assert d.body == "# Rollout notes\n\nBe careful."


def test_failed_non_markdown_and_mcp_writes_are_ignored(items):
    bodies = "\n".join(i.body for i in items)
    assert "Never written" not in bodies  # failed Write
    assert "print('hi')" not in bodies  # not a .md file
    assert "From MCP" not in bodies  # MCP tool, not the Write tool


def test_plan_exit_uses_write_from_the_log():
    got = extract_items(parse_session(BASIC))
    plan = [i for i in got if i.kind == "deliverable"]
    # The Write to the plan file is not emitted twice.
    assert len(plan) == 1
    assert plan[0].confidence == "explicit" and plan[0].body == "# Plan\n- step"
    assert plan[0].source_uuids == ("u-0004", "u-0009")


def test_plan_exit_replays_edit_on_written_plan(items):
    plan = one(items, "Edited plan")
    assert plan.body == "# Edited plan\n\n- step one\n- step 2 (revised)\n"
    assert plan.source_uuids == ("x-0033", "x-0035", "x-0037")


def test_plan_exit_without_body_in_log_gets_a_note(items):
    plan = one(items, "Plan: missing-plan.md")
    assert plan.kind == "deliverable" and plan.confidence == "explicit"
    assert "not in the log" in plan.body and "/plans/missing-plan.md" in plan.body


def test_long_structured_answer_is_merged_per_message(items):
    d = one(items, "Rollout plan")
    assert (d.kind, d.confidence) == ("deliverable", "inferred")
    assert d.source_uuids == ("x-0002", "x-0003")
    assert "## Goals" in d.body and "## Steps" in d.body and "| Build | Alice |" in d.body


def test_long_plain_answer_is_deliverable_but_medium_is_not(items):
    inferred = [i for i in items if i.kind == "deliverable" and i.confidence == "inferred"]
    assert any(len(i.body) >= LONG_ANSWER_CHARS for i in inferred)
    assert len(inferred) == 2  # structured + long plain; not the medium or tiny headings one
    assert not any(i.body.startswith("A medium answer") for i in inferred)
    assert not any(i.body.startswith("## A") for i in inferred)


# ------------------------------------------------------------------------- decisions


def test_ask_user_question_answers(items):
    d = one(items, "2 answers")
    assert (d.kind, d.confidence) == ("decision", "explicit")
    assert d.body == "- **Which storage?**: SQLite\n- **Which color?**: Blue"
    assert d.source_uuids == ("x-0021", "x-0022")


def test_ask_user_question_in_basic_session():
    d = [i for i in extract_items(parse_session(BASIC)) if i.kind == "decision"]
    assert [i.title for i in d] == ["Which storage?: SQLite"]


def test_default_english_phrase_makes_inferred_decision(items):
    d = one(items, "Let")
    assert (d.kind, d.confidence) == ("decision", "inferred")
    assert "go with the second option" in d.body


def test_custom_phrase_only_applies_when_passed():
    session = parse_session(EXTRACT)
    without = extract_items(session)
    with_phrase = extract_items(session, decision_phrases=["settled on"])
    assert one(without, "We settled on").kind == "prompt"
    assert one(with_phrase, "We settled on").kind == "decision"


def test_decision_phrases_match_whole_words_case_insensitively():
    session = parse_session(EXTRACT)
    got = extract_items(session, decision_phrases=["SETTLED  ON"])
    assert one(got, "We settled on").kind == "decision"
    got = extract_items(session, decision_phrases=["settle"])
    assert one(got, "We settled on").kind == "prompt"


# ------------------------------------------------------------------------- status


def test_git_commit_heredoc_message(items):
    s = one(items, "feat: add rollout notes")
    assert (s.kind, s.confidence) == ("status", "explicit")
    assert s.body == "feat: add rollout notes\n\nExplain the rollout steps."


def test_git_commit_multiple_m_flags(items):
    s = one(items, "fix: typo")
    assert s.body == "fix: typo\n\nSecond paragraph."


def test_failed_commit_and_other_git_commands_are_not_status(items):
    statuses = [i for i in items if i.kind == "status"]
    assert not by_title(items, "chore: fails")
    assert len(statuses) == 3  # heredoc, two -m, and the unparsed --amend


def test_unparseable_commit_still_reported(items):
    s = one(items, "git commit")
    assert s.kind == "status" and "could not be parsed" in s.body


# ------------------------------------------------------------------------- order, titles


def test_items_are_chronological(items):
    stamps = [i.timestamp for i in items if i.timestamp]
    assert stamps == sorted(stamps)


def test_every_item_has_source_records(items):
    assert all(i.source_uuids for i in items)


def test_session_title_prefers_ai_title():
    assert session_title(parse_session(BASIC)) == "Demo app planning"


def test_session_title_falls_back_to_first_prompt(tmp_path):
    lines = [
        ln for ln in EXTRACT.read_text(encoding="utf-8").splitlines() if '"ai-title"' not in ln
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert session_title(parse_session(p)) == "Please draft the rollout notes."
    assert session_title(parse_session(p), max_len=10) == "Please..."


def test_session_title_for_empty_session(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text("", encoding="utf-8")
    assert session_title(parse_session(p)) == "Untitled session"


def test_extractor_accepts_a_plain_record_iterable():
    session = parse_session(BASIC)
    assert extract_items(session.records) == extract_items(session)


def test_extractor_does_not_modify_source(tmp_path):
    copy = tmp_path / "s.jsonl"
    copy.write_bytes(EXTRACT.read_bytes())
    before = copy.read_bytes()
    extract_items(parse_session(copy))
    assert copy.read_bytes() == before
