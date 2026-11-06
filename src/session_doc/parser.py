"""Parse Claude Code session logs (JSONL) into typed records.

The parser is deliberately tolerant: unknown fields and unknown record types are
ignored, and malformed lines are skipped instead of raising. It never writes anything.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- blocks


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ThinkingBlock:
    text: str


@dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResultBlock:
    tool_use_id: str
    text: str
    is_error: bool = False


Block = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock

# --------------------------------------------------------------------------- records


@dataclass
class Record:
    """Fields shared by every record. `type` is the raw log `type` string."""

    type: str
    line_no: int = 0
    session_id: str | None = None
    uuid: str | None = None
    parent_uuid: str | None = None
    timestamp: datetime | None = None
    cwd: str | None = None
    git_branch: str | None = None
    version: str | None = None
    is_sidechain: bool = False
    agent_id: str | None = None


# Text that Claude Code injects or echoes, wrapped in a leading XML-like tag. Slash
# commands (`<command-name>`) and `!` shell input (`<bash-input>`) are echoes, not prompts.
_SYSTEM_TAGS = (
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<task-notification>",
    "<system-reminder>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
)

# Keys of `toolUseResult` the extractor needs. The rest (file contents from Read, command
# output, patches) is dropped to keep memory low on large logs.
_RESULT_KEYS = ("type", "filePath", "answers", "originalFile")


@dataclass
class UserRecord(Record):
    """A `user` record: typed text, a slash command, injected text, or tool results."""

    is_meta: bool = False
    is_compact_summary: bool = False
    prompt_id: str | None = None
    prompt_source: str | None = None  # "typed" | "system" | "queued" | None
    origin_kind: str | None = None  # "human" | "task-notification" | None
    permission_mode: str | None = None
    text_blocks: list[TextBlock] = field(default_factory=list)
    tool_results: list[ToolResultBlock] = field(default_factory=list)
    # Structured result of the tool call, reduced to the keys in `_RESULT_KEYS`.
    tool_use_result: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.text_blocks)

    @property
    def is_typed_prompt(self) -> bool:
        """True when the user typed this text (not a tool result or injected text)."""
        if self.is_sidechain or self.is_meta or self.is_compact_summary or self.tool_results:
            return False
        if not self.text_blocks or self.origin_kind not in (None, "human"):
            return False
        if self.text.lstrip().startswith(_SYSTEM_TAGS):
            return False
        if self.prompt_source is not None:
            return self.prompt_source == "typed"
        # Fallback for records without `promptSource`.
        return self.prompt_id is not None


@dataclass
class AssistantRecord(Record):
    """An `assistant` record. Claude Code writes one record per content block;
    records of the same API message share `message_id`."""

    message_id: str | None = None
    request_id: str | None = None
    model: str | None = None
    stop_reason: str | None = None
    blocks: list[Block] = field(default_factory=list)

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.blocks if isinstance(b, ToolUseBlock)]

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks if isinstance(b, TextBlock))


@dataclass
class SystemRecord(Record):
    subtype: str | None = None
    content: str | None = None
    is_meta: bool = False


@dataclass
class AttachmentRecord(Record):
    """Harness-injected context. `data` holds the top-level scalar fields of the raw
    `attachment` object (nested values are dropped); its `type` (e.g. `plan_mode_exit`,
    `queued_command`) is exposed as `attachment_type`."""

    attachment_type: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AiTitleRecord(Record):
    title: str = ""


@dataclass
class LastPromptRecord(Record):
    prompt: str = ""


@dataclass
class OtherRecord(Record):
    """Any record type this parser does not model (mode, cost-state, api-request, ...)."""


AnyRecord = (
    UserRecord
    | AssistantRecord
    | SystemRecord
    | AttachmentRecord
    | AiTitleRecord
    | LastPromptRecord
    | OtherRecord
)


@dataclass
class Session:
    """A parsed log file. Summary properties are computed once, on first access."""

    path: Path
    records: list[AnyRecord] = field(default_factory=list)
    skipped_lines: int = 0

    @cached_property
    def session_id(self) -> str | None:
        return next((r.session_id for r in self.records if r.session_id), None)

    @cached_property
    def cwd(self) -> str | None:
        """The first working directory seen. It can change mid-session."""
        return next((r.cwd for r in self.records if r.cwd), None)

    @cached_property
    def git_branch(self) -> str | None:
        return next((r.git_branch for r in self.records if r.git_branch), None)

    @cached_property
    def start_time(self) -> datetime | None:
        return min((r.timestamp for r in self.records if r.timestamp), default=None)

    @cached_property
    def end_time(self) -> datetime | None:
        return max((r.timestamp for r in self.records if r.timestamp), default=None)

    @cached_property
    def title(self) -> str | None:
        """Latest AI-generated title, if any."""
        titles = [r.title for r in self.records if isinstance(r, AiTitleRecord) and r.title]
        return titles[-1] if titles else None

    @cached_property
    def last_prompt(self) -> str | None:
        """Latest `last-prompt` text, if any."""
        found = [r.prompt for r in self.records if isinstance(r, LastPromptRecord) and r.prompt]
        return found[-1] if found else None


# --------------------------------------------------------------------------- parsing


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO 8601. A value without an offset is taken as UTC, so all are comparable."""
    if not isinstance(value, str):
        return None
    try:
        # Python 3.10's fromisoformat does not accept a trailing "Z".
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _common(raw: dict[str, Any], line_no: int) -> dict[str, Any]:
    return {
        "type": raw["type"],
        "line_no": line_no,
        "session_id": _str(raw.get("sessionId")),
        "uuid": _str(raw.get("uuid")),
        "parent_uuid": _str(raw.get("parentUuid")),
        "timestamp": _parse_timestamp(raw.get("timestamp")),
        "cwd": _str(raw.get("cwd")),
        "git_branch": _str(raw.get("gitBranch")),
        "version": _str(raw.get("version")),
        "is_sidechain": raw.get("isSidechain") is True,
        "agent_id": _str(raw.get("agentId")),
    }


def _message(raw: dict[str, Any]) -> dict[str, Any]:
    msg = raw.get("message")
    return msg if isinstance(msg, dict) else {}


def _tool_use_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {k: value[k] for k in _RESULT_KEYS if k in value}


def _scalars(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if v is None or isinstance(v, (str, int, float))}


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
        ]
        return "\n".join(parts)
    return ""


def _parse_user(raw: dict[str, Any], line_no: int) -> UserRecord:
    rec = UserRecord(
        **_common(raw, line_no),
        is_meta=raw.get("isMeta") is True,
        is_compact_summary=raw.get("isCompactSummary") is True,
        prompt_id=_str(raw.get("promptId")),
        prompt_source=_str(raw.get("promptSource")),
        permission_mode=_str(raw.get("permissionMode")),
        tool_use_result=_tool_use_result(raw.get("toolUseResult")),
    )
    origin = raw.get("origin")
    if isinstance(origin, dict):
        rec.origin_kind = _str(origin.get("kind"))
    content = _message(raw).get("content")
    if isinstance(content, str):
        rec.text_blocks.append(TextBlock(content))
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and isinstance(b.get("text"), str):
                rec.text_blocks.append(TextBlock(b["text"]))
            elif b.get("type") == "tool_result" and _str(b.get("tool_use_id")):
                rec.tool_results.append(
                    ToolResultBlock(
                        tool_use_id=b["tool_use_id"],
                        text=_result_text(b.get("content")),
                        is_error=b.get("is_error") is True,
                    )
                )
    return rec


def _parse_assistant(raw: dict[str, Any], line_no: int) -> AssistantRecord:
    msg = _message(raw)
    rec = AssistantRecord(
        **_common(raw, line_no),
        message_id=_str(msg.get("id")),
        request_id=_str(raw.get("requestId")),
        model=_str(msg.get("model")),
        stop_reason=_str(msg.get("stop_reason")),
    )
    content = msg.get("content")
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        if kind == "text" and isinstance(b.get("text"), str):
            rec.blocks.append(TextBlock(b["text"]))
        elif kind == "thinking" and isinstance(b.get("thinking"), str):
            rec.blocks.append(ThinkingBlock(b["thinking"]))
        elif kind == "tool_use" and _str(b.get("id")):
            inp = b.get("input")
            rec.blocks.append(
                ToolUseBlock(
                    id=b["id"],
                    name=_str(b.get("name")) or "",
                    input=inp if isinstance(inp, dict) else {},
                )
            )
    return rec


def _parse_record(raw: dict[str, Any], line_no: int) -> AnyRecord:
    kind = raw["type"]
    if kind == "user":
        return _parse_user(raw, line_no)
    if kind == "assistant":
        return _parse_assistant(raw, line_no)
    if kind == "system":
        return SystemRecord(
            **_common(raw, line_no),
            subtype=_str(raw.get("subtype")),
            content=_str(raw.get("content")),
            is_meta=raw.get("isMeta") is True,
        )
    if kind == "attachment":
        att = _scalars(raw.get("attachment"))
        return AttachmentRecord(
            **_common(raw, line_no), attachment_type=_str(att.get("type")), data=att
        )
    if kind == "ai-title":
        return AiTitleRecord(**_common(raw, line_no), title=_str(raw.get("aiTitle")) or "")
    if kind == "last-prompt":
        return LastPromptRecord(**_common(raw, line_no), prompt=_str(raw.get("lastPrompt")) or "")
    return OtherRecord(**_common(raw, line_no))


def iter_records(path: str | Path) -> Iterator[AnyRecord]:
    """Yield typed records from a JSONL file, skipping malformed lines."""
    yield from _iter(Path(path), None)


def _iter(path: Path, counter: list[int] | None) -> Iterator[AnyRecord]:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                raw = None
            if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
                if counter is not None:
                    counter[0] += 1
                continue
            yield _parse_record(raw, line_no)


def parse_session(path: str | Path) -> Session:
    """Parse a whole session file. Never raises on bad lines; counts them instead."""
    p = Path(path)
    skipped = [0]
    records = list(_iter(p, skipped))
    return Session(path=p, records=records, skipped_lines=skipped[0])
