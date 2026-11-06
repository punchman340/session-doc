from datetime import timezone
from pathlib import Path

from session_doc.parser import (
    AiTitleRecord,
    AssistantRecord,
    AttachmentRecord,
    LastPromptRecord,
    OtherRecord,
    SystemRecord,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    UserRecord,
    iter_records,
    parse_session,
)

FIXTURES = Path(__file__).parent / "fixtures"
BASIC = FIXTURES / "basic_session.jsonl"
MALFORMED = FIXTURES / "malformed.jsonl"


def users(session):
    return [r for r in session.records if isinstance(r, UserRecord)]


def assistants(session):
    return [r for r in session.records if isinstance(r, AssistantRecord)]


def test_session_metadata():
    s = parse_session(BASIC)
    assert s.session_id == "00000000-0000-4000-8000-000000000001"
    assert s.cwd == "/work/demo-app"
    assert s.git_branch == "feature/demo"
    assert s.title == "Demo app planning"
    assert s.skipped_lines == 0
    assert s.start_time is not None and s.start_time.tzinfo is timezone.utc
    assert s.end_time > s.start_time


def test_record_types():
    kinds = {type(r) for r in parse_session(BASIC).records}
    assert {
        UserRecord,
        AssistantRecord,
        SystemRecord,
        AttachmentRecord,
        AiTitleRecord,
        LastPromptRecord,
        OtherRecord,
    } <= kinds


def test_unknown_record_type_and_fields_are_ignored():
    future = [r for r in iter_records(BASIC) if r.type == "some-future-type"]
    assert len(future) == 1 and isinstance(future[0], OtherRecord)
    assert users(parse_session(BASIC))[0].text.startswith("Write a short plan")


def test_typed_prompts_vs_everything_else():
    typed = [u.text for u in users(parse_session(BASIC)) if u.is_typed_prompt]
    assert typed == ["Write a short plan for the demo app.", "Thanks, now add tests."]


def test_non_typed_user_records():
    by_text = {u.text: u for u in users(parse_session(BASIC)) if u.text}
    assert by_text["<local-command-caveat>Caveat: demo.</local-command-caveat>"].is_meta
    notif = by_text["<task-notification>demo task done</task-notification>"]
    assert notif.origin_kind == "task-notification" and not notif.is_typed_prompt
    compact = by_text["This session is being continued from a previous one."]
    assert compact.is_compact_summary and not compact.is_typed_prompt


def test_tool_results_are_not_prompts():
    results = [u for u in users(parse_session(BASIC)) if u.tool_results]
    assert len(results) == 2
    assert all(not u.is_typed_prompt for u in results)
    assert results[0].tool_results[0].tool_use_id == "toolu_w1"
    assert results[0].tool_results[0].text == "File created"
    # list-shaped result content is flattened to text
    assert results[1].tool_results[0].text == "User chose SQLite"
    assert results[1].tool_use_result["answers"] == {"Which storage?": "SQLite"}


def test_assistant_blocks_share_message_id():
    asst = assistants(parse_session(BASIC))
    first_three = asst[:3]
    assert {a.message_id for a in first_three} == {"msg_demo_1"}
    assert isinstance(first_three[0].blocks[0], ThinkingBlock)
    assert isinstance(first_three[1].blocks[0], TextBlock)
    write = first_three[2].tool_uses[0]
    assert isinstance(write, ToolUseBlock) and write.name == "Write"
    assert write.input["file_path"] == "/work/demo-app/PLAN.md"
    assert asst[-1].text == "Done. Using SQLite." and asst[-1].stop_reason == "end_turn"


def test_ask_user_question_input():
    q = next(
        t
        for a in assistants(parse_session(BASIC))
        for t in a.tool_uses
        if t.name == "AskUserQuestion"
    )
    assert q.input["questions"][0]["options"][0]["label"] == "SQLite"


def test_attachment_and_system():
    recs = parse_session(BASIC).records
    att = next(r for r in recs if isinstance(r, AttachmentRecord))
    assert att.attachment_type == "plan_mode_exit"
    assert att.data["planFilePath"] == "/work/demo-app/PLAN.md"
    sysrec = next(r for r in recs if isinstance(r, SystemRecord))
    assert sysrec.subtype == "turn_duration"


def test_malformed_lines_are_skipped_without_crashing():
    s = parse_session(MALFORMED)
    # not json, array, no type, non-string type, truncated json
    assert s.skipped_lines == 5
    assert any(isinstance(r, UserRecord) and r.text == "Still parsed." for r in s.records)
    # wrongly-shaped but typed records survive with empty/None fields
    bad_user = next(r for r in users(s) if r.line_no == 6)
    assert bad_user.text == "" and bad_user.timestamp is None
    assert assistants(s)[0].blocks == []


def test_line_numbers_are_one_based_and_count_blank_lines():
    s = parse_session(MALFORMED)
    ok = next(r for r in users(s) if r.uuid == "u-ok")
    assert ok.line_no == 9
    assert [r.line_no for r in s.records] == sorted(r.line_no for r in s.records)


def test_timestamps_without_offset_are_utc(make_session):
    s = make_session(
        {"type": "system", "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "system", "timestamp": "2026-01-01T00:00:01"},
    )
    assert all(r.timestamp.tzinfo == timezone.utc for r in s.records)
    assert s.end_time > s.start_time  # aware and formerly naive values compare


def _user(text: str, **extra) -> dict:
    return {"type": "user", "message": {"content": text}, **extra}


def test_echoed_commands_are_not_typed_prompts(make_session):
    s = make_session(
        _user("<command-name>/model</command-name>", promptId="p-1"),
        _user("<bash-input>ls</bash-input>", promptId="p-2"),
        _user("<command-name>/clear</command-name>", promptSource="typed"),
        _user("From a task.", promptSource="typed", origin={"kind": "task-notification"}),
        _user("Real prompt.", promptSource="typed", origin={"kind": "human"}),
    )
    assert [u.text for u in users(s) if u.is_typed_prompt] == ["Real prompt."]


def test_large_payloads_are_reduced(make_session):
    s = make_session(
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]},
            "toolUseResult": {"file": {"content": "big"}, "stdout": "big", "answers": {"q": "a"}},
        },
        {"type": "attachment", "attachment": {"type": "file", "path": "/p", "content": {"x": 1}}},
    )
    user, att = s.records
    assert user.tool_use_result == {"answers": {"q": "a"}}
    assert att.data == {"type": "file", "path": "/p"}


def test_blocks_without_ids_are_dropped(make_session):
    s = make_session(
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Write", "input": {}}]},
        },
    )
    assert users(s)[0].tool_results == [] and assistants(s)[0].blocks == []


def test_last_prompt(make_session):
    s = make_session(
        {"type": "last-prompt", "lastPrompt": "first"},
        {"type": "last-prompt", "lastPrompt": "second"},
    )
    assert s.last_prompt == "second"


def test_parser_does_not_modify_source(tmp_path):
    copy = tmp_path / "s.jsonl"
    copy.write_bytes(BASIC.read_bytes())
    before = copy.read_bytes()
    parse_session(copy)
    assert copy.read_bytes() == before
