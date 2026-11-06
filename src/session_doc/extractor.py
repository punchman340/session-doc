"""Turn parsed session records into UI items (prompts, deliverables, decisions, status).

Rule-based and deterministic: no LLM, no network, and no file access. Everything is
derived from the records handed in; paths named in a log are never opened.
See docs/DESIGN.md section 5 and docs/DECISIONS.md for the rules and thresholds.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from session_doc.parser import (
    AnyRecord,
    AssistantRecord,
    AttachmentRecord,
    Session,
    TextBlock,
    ToolUseBlock,
    UserRecord,
)

Kind = Literal["prompt", "deliverable", "decision", "status"]
Confidence = Literal["explicit", "inferred"]

# Thresholds, tuned on real logs (see docs/DECISIONS.md, 2026-11-07 extractor entry).
LONG_ANSWER_CHARS = 1500  # an assistant message this long is a deliverable
STRUCTURED_MIN_CHARS = 300  # headings/table/checklist only count above this size
TITLE_MAX = 80

# English defaults. More phrases come from the caller (later: the user's config file).
DEFAULT_DECISION_PHRASES: tuple[str, ...] = ("let's go with", "decided", "go with")


@dataclass(frozen=True)
class Item:
    kind: Kind
    timestamp: datetime | None
    title: str
    body: str  # Markdown
    confidence: Confidence
    source_uuids: tuple[str, ...] = ()


# --------------------------------------------------------------------------- text helpers

_FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.M | re.S)
_HEADING = re.compile(r"^#{1,6}[ \t]+(\S.*?)[ \t]*#*[ \t]*$", re.M)
_TABLE = re.compile(
    r"^[^\n]*\|[^\n]*\n[ \t]*\|?(?:[ \t]*:?-{3,}:?[ \t]*\|)+(?:[ \t]*:?-{3,}:?[ \t]*)?$", re.M
)
_CHECKLIST = re.compile(r"^[ \t]*[-*+][ \t]+\[[ xX]\][ \t]", re.M)
_PASTED = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content[^>]*>", re.S)


def _truncate(text: str, limit: int = TITLE_MAX) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _headings(text: str) -> list[str]:
    return _HEADING.findall(_FENCE.sub("", text))


def _is_structured(text: str) -> bool:
    plain = _FENCE.sub("", text)
    return (
        len(_HEADING.findall(plain)) >= 2
        or bool(_TABLE.search(plain))
        or bool(_CHECKLIST.search(plain))
    )


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _norm_path(path: str) -> str:
    return path.replace("\\", "/")


def _norm_quotes(text: str) -> str:
    return text.replace("\u2019", "'").replace("\u2018", "'")


def _compile_phrases(extra: Iterable[str]) -> re.Pattern[str] | None:
    seen: dict[str, None] = {}
    for p in (*DEFAULT_DECISION_PHRASES, *extra):
        p = " ".join(_norm_quotes(p).lower().split())
        if p:
            seen[p] = None
    if not seen:
        return None
    alt = "|".join(re.escape(p) for p in sorted(seen, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.I)


# --------------------------------------------------------------------------- tool inputs


def _write_path(inp: dict[str, Any]) -> str | None:
    for key in ("file_path", "path"):
        if isinstance(inp.get(key), str):
            return inp[key]
    return None


def _write_content(inp: dict[str, Any]) -> str | None:
    for key in ("content", "file_text"):
        if isinstance(inp.get(key), str):
            return inp[key]
    return None


_GIT_COMMIT = re.compile(
    r"(?<![\w./-])git(?:[ \t]+(?:-[cC][ \t]+\S+|--[\w-]+(?:=\S+)?))*[ \t]+commit(?![\w-])"
)
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1[ \t]*\n(.*?)\n[ \t]*\2[ \t]*(?:\n|\)|$)", re.S)
_MESSAGE_ARG = re.compile(
    r"(?<!\S)(?:--message|-[A-Za-z]*m)(?:=|[ \t]+)"
    r"(\"(?:\\.|[^\"\\])*\"|'[^']*'|[^\s\"';&|)]+)"
)


def _unquote(arg: str) -> str:
    if len(arg) >= 2 and arg[0] == arg[-1] == '"':
        return re.sub(r"\\([\"\\$`])", r"\1", arg[1:-1])
    if len(arg) >= 2 and arg[0] == arg[-1] == "'":
        return arg[1:-1]
    return arg


def _commit_message(command: str) -> str | None:
    """Return the commit message of the first `git commit` in `command`, if parseable."""
    m = _GIT_COMMIT.search(command)
    if not m:
        return None
    rest = command[m.end() :]
    if not _MESSAGE_ARG.search(rest):
        return None
    doc = _HEREDOC.search(rest)
    if doc:
        return doc.group(3).strip() or None
    parts = [_unquote(a).strip() for a in _MESSAGE_ARG.findall(rest)]
    parts = [p for p in parts if p]
    return "\n\n".join(parts) or None


def _answer_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


# --------------------------------------------------------------------------- extraction


@dataclass
class _PlanFile:
    """What the log reveals about a plan file: its last known content, or None if unknown."""

    content: str | None = None
    uuids: tuple[str, ...] = ()


def extract_items(
    session: Session | Iterable[AnyRecord],
    *,
    decision_phrases: Iterable[str] = (),
) -> list[Item]:
    """Extract UI items from a parsed session, in chronological order.

    `decision_phrases` are extra phrases (case-insensitive) that mark a typed prompt as an
    inferred decision, on top of the English defaults.
    """
    records = list(session.records if isinstance(session, Session) else session)
    records = [r for r in records if not r.is_sidechain]
    phrase_re = _compile_phrases(decision_phrases)

    # Pre-pass: index tool calls and results, message groups, and plan files.
    tool_uses: dict[str, tuple[ToolUseBlock, AssistantRecord]] = {}
    results: dict[str, UserRecord] = {}
    failed: set[str] = set()
    groups: dict[str, list[AssistantRecord]] = {}
    plan_paths: set[str] = set()
    for i, rec in enumerate(records):
        if isinstance(rec, AssistantRecord):
            groups.setdefault(rec.message_id or f"_rec{i}", []).append(rec)
            for tu in rec.tool_uses:
                tool_uses[tu.id] = (tu, rec)
        elif isinstance(rec, UserRecord):
            for tr in rec.tool_results:
                results[tr.tool_use_id] = rec
                if tr.is_error:
                    failed.add(tr.tool_use_id)
        elif isinstance(rec, AttachmentRecord) and rec.attachment_type == "plan_mode_exit":
            path = rec.data.get("planFilePath")
            if isinstance(path, str):
                plan_paths.add(_norm_path(path))

    items: list[tuple[int, Item]] = []
    emitted_groups: set[str] = set()
    plan_files: dict[str, _PlanFile] = {}

    def add(order: int, item: Item) -> None:
        items.append((order, item))

    for i, rec in enumerate(records):
        if isinstance(rec, UserRecord):
            _user_items(rec, i, tool_uses, phrase_re, add)
        elif isinstance(rec, AssistantRecord):
            key = rec.message_id or f"_rec{i}"
            if key not in emitted_groups:
                emitted_groups.add(key)
                inferred = _long_answer(groups[key])
                if inferred:
                    add(i, inferred)
            for tu in rec.tool_uses:
                if tu.id in failed:
                    continue
                _tool_items(rec, tu, i, plan_paths, plan_files, results, add)
        elif isinstance(rec, AttachmentRecord) and rec.attachment_type == "plan_mode_exit":
            add(i, _plan_item(rec, plan_files))

    # Chronological: stable sort by timestamp; records without one inherit the previous.
    floor = datetime.min.replace(tzinfo=timezone.utc)
    last = floor
    keyed: list[tuple[datetime, int, Item]] = []
    for order, item in sorted(items, key=lambda t: t[0]):
        last = item.timestamp or last
        keyed.append((item.timestamp or last, order, item))
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [item for _, _, item in keyed]


def _user_items(
    rec: UserRecord,
    order: int,
    tool_uses: dict[str, tuple[ToolUseBlock, AssistantRecord]],
    phrase_re: re.Pattern[str] | None,
    add: Any,
) -> None:
    uuids = (rec.uuid,) if rec.uuid else ()

    answers = rec.tool_use_result.get("answers") if isinstance(rec.tool_use_result, dict) else None
    if isinstance(answers, dict) and answers:
        for tr in rec.tool_results:
            if tr.tool_use_id in tool_uses and tool_uses[tr.tool_use_id][1].uuid:
                uuids = (tool_uses[tr.tool_use_id][1].uuid, *uuids)  # type: ignore[assignment]
                break
        pairs = [(str(q), _answer_text(a)) for q, a in answers.items()]
        title = f"{pairs[0][0]}: {pairs[0][1]}"
        if len(pairs) > 1:
            title = f"{len(pairs)} answers: {pairs[0][0]}"
        body = "\n".join(f"- **{q}**: {a}" for q, a in pairs)
        add(order, Item("decision", rec.timestamp, _truncate(title), body, "explicit", uuids))
        return

    if not rec.is_typed_prompt:
        return
    text = rec.text
    outside_paste = _PASTED.sub("", text)
    if phrase_re and phrase_re.search(_norm_quotes(outside_paste)):
        kind: Kind = "decision"
        confidence: Confidence = "inferred"
    else:
        kind, confidence = "prompt", "explicit"
    title = _truncate(_first_line(outside_paste)) or "Pasted content"
    add(order, Item(kind, rec.timestamp, title, text, confidence, uuids))


def _long_answer(group: list[AssistantRecord]) -> Item | None:
    text = "\n\n".join(
        b.text.strip()
        for r in group
        for b in r.blocks
        if isinstance(b, TextBlock) and b.text.strip()
    )
    n = len(text)
    if n < STRUCTURED_MIN_CHARS:
        return None
    if n < LONG_ANSWER_CHARS and not _is_structured(text):
        return None
    heads = _headings(text)
    title = _truncate(heads[0] if heads else _first_line(text))
    timestamp = next((r.timestamp for r in group if r.timestamp), None)
    uuids = tuple(r.uuid for r in group if r.uuid)
    return Item("deliverable", timestamp, title, text, "inferred", uuids)


def _tool_items(
    rec: AssistantRecord,
    tu: ToolUseBlock,
    order: int,
    plan_paths: set[str],
    plan_files: dict[str, _PlanFile],
    results: dict[str, UserRecord],
    add: Any,
) -> None:
    uuids = (rec.uuid,) if rec.uuid else ()

    if tu.name == "Write":
        path = _write_path(tu.input)
        content = _write_content(tu.input)
        if path is None or content is None:
            return
        norm = _norm_path(path)
        if norm in plan_paths:
            plan_files[norm] = _PlanFile(content, uuids)
        elif norm.lower().endswith(".md"):
            heads = _headings(content)
            title = _truncate(heads[0] if heads else norm.rsplit("/", 1)[-1])
            add(order, Item("deliverable", rec.timestamp, title, content, "explicit", uuids))

    elif tu.name == "Edit":
        path = _write_path(tu.input)
        if path is None or _norm_path(path) not in plan_paths:
            return
        norm = _norm_path(path)
        known = plan_files.get(norm)
        base = known.content if known else None
        if base is None:
            original = results.get(tu.id)
            original = original.tool_use_result if original else None
            base = original.get("originalFile") if isinstance(original, dict) else None
        old, new = tu.input.get("old_string"), tu.input.get("new_string")
        if isinstance(base, str) and isinstance(old, str) and isinstance(new, str):
            count = -1 if tu.input.get("replace_all") is True else 1
            plan_files[norm] = _PlanFile(
                base.replace(old, new, count), (*(known.uuids if known else ()), *uuids)
            )
        else:
            plan_files[norm] = _PlanFile(None, uuids)

    elif tu.name == "Bash":
        command = tu.input.get("command")
        if not isinstance(command, str) or not _GIT_COMMIT.search(command):
            return
        message = _commit_message(command)
        if message:
            title, body = _truncate(_first_line(message)), message
        else:
            title, body = "git commit", "Commit message could not be parsed from the command."
        add(order, Item("status", rec.timestamp, title, body, "explicit", uuids))


def _plan_item(rec: AttachmentRecord, plan_files: dict[str, _PlanFile]) -> Item:
    path = rec.data.get("planFilePath")
    path = path if isinstance(path, str) else ""
    name = _norm_path(path).rsplit("/", 1)[-1] or "plan"
    known = plan_files.get(_norm_path(path))
    uuids = (*(known.uuids if known else ()), *((rec.uuid,) if rec.uuid else ()))
    if known and known.content is not None:
        heads = _headings(known.content)
        title = _truncate(heads[0] if heads else f"Plan: {name}")
        return Item("deliverable", rec.timestamp, title, known.content, "explicit", uuids)
    body = (
        f"The plan body is not in the log. It was saved to `{path}`, "
        "which session-doc does not read."
    )
    return Item("deliverable", rec.timestamp, f"Plan: {name}", body, "explicit", uuids)


# --------------------------------------------------------------------------- titles


def session_title(session: Session, max_len: int = TITLE_MAX) -> str:
    """The AI-generated title if present, else the first typed prompt, truncated."""
    if session.title:
        return _truncate(session.title, max_len)
    for rec in session.records:
        if isinstance(rec, UserRecord) and rec.is_typed_prompt:
            text = _PASTED.sub("", rec.text)
            title = _truncate(_first_line(text) or "Pasted content", max_len)
            return title
    return "Untitled session"
