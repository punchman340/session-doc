# Claude Code log format

What the parser and extractor rely on, as observed in real logs. The checks ran locally
on one user's logs (85 to 92 files, Claude Code 2.1.x) and recorded structure and counts
only. Older formats are untested.

## Files and records

- One JSON object per line, no wrapper. `sessionId` equals the file name.
- Every file starts with a `mode` record, so metadata cannot be read from line 1.
- Subagent logs live in `<session-id>/subagents/agent-*.jsonl` and have
  `isSidechain: true`. The parser reads them as ordinary files; `generate` skips
  `subagents/` directories. The extractor skips sidechain records.
- Some files hold no `user` or `assistant` record at all (1 of 85).
- Logs disappear: during one day the projects dir went from 91 to 85 top-level files,
  removed outside session-doc. This is why `generate` archives logs by default.

## Record types

- Modeled: `user`, `assistant`, `system`, `attachment`; `ai-title` and `last-prompt` for
  titles.
- Kept as `OtherRecord` with no payload: `mode`, `permission-mode`, `session-owner`,
  `atis-latch`, `bridge-session`, `file-history-snapshot`, `file-history-delta`,
  `queue-operation`, `cost-state`, `tools-used`, `api-request`, `api-request-shape`,
  `api-request-blob`.

## Common fields

`cwd`, `gitBranch`, `version`, `sessionId`, `uuid`, `parentUuid`, and `timestamp`
(ISO 8601 with `Z`) sit on the top level of `user`, `assistant`, `system`, and
`attachment` records, not on a header record. `cwd` and `gitBranch` can change
mid-session, so the parser reads them per record. A timestamp without an offset is read
as UTC.

## Assistant messages

One `assistant` record per content block (thinking, text, or tool_use), all sharing
`message.id`. Tool names include MCP tools as `mcp__<server>__<tool>`.

## User records

- Tool results arrive as `user` records whose content is `tool_result` blocks, plus a
  top-level `toolUseResult` with a tool-specific shape.
- Typed text: `promptSource == "typed"` with `origin.kind == "human"`. `content` is a
  plain string or a list of `text` blocks. Pasted text is wrapped in `<pasted_content>`
  inside a typed prompt.
- Not typed: `tool_result` records, `isMeta`, `isCompactSummary`, `promptSource` of
  `system` or `queued`, `origin.kind == "task-notification"`, and text starting with
  `<local-command-caveat>`, `<task-notification>`, and similar tags.
- Slash commands are echoed as `<command-name>...</command-name>` records with no
  `promptSource`. 2 of 36 carried a `promptId`.
- Records without `promptSource` fall back to `promptId` plus the tag check. Rare in the
  sample, so the fallback is not well verified.

## Tools

- `Write`: path key is `file_path` (450 calls), a few use `path` (4); body key is
  `content` (453), once `file_text`.
- `AskUserQuestion`: input `questions[]` (`question`, `header`, `multiSelect`,
  `options[]`); the answer is in the result record's `toolUseResult.answers`
  (question text -> chosen label).
- `Edit`: the result's `toolUseResult.originalFile` holds the file before the edit.
- `git commit` appears 3 times, always as
  `git add ... && git commit -m "$(cat <<'EOF' ... EOF)"`.

## Plan mode

`ExitPlanMode` never appears as a `tool_use`. Plan mode shows up as `permission-mode`
records (`plan`) and `attachment` records of type `plan_mode` and `plan_mode_exit`; the
latter carries `planFilePath`. The sample has one `plan_mode_exit` and no `Write` or
other record that writes its plan file, so the plan body is usually not in the log.

## Not observed

`TodoWrite`, `TaskCreate`, and `TaskUpdate` never appear. Background task state appears
as `attachment` type `task_status`.
