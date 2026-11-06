# Decisions

Short, append-only log. Newest first. Format: date, decision, why.

## 2026-11-07

- **Parser checked against real Claude Code logs (88 files, version 2.1.x, structure only).**
  - Top-level record types: `user`, `assistant`, `system`, `attachment` (modeled);
    `ai-title`, `last-prompt` (modeled, for titles); `mode`, `permission-mode`,
    `session-owner`, `atis-latch`, `bridge-session`, `file-history-snapshot`,
    `file-history-delta`, `queue-operation`, `cost-state`, `tools-used`, `api-request`,
    `api-request-shape`, `api-request-blob` (kept as `OtherRecord`, no payload).
  - Each line is one record; no wrapper. `sessionId` equals the file name. Every file starts
    with a `mode` record, so metadata cannot be read from line 1.
  - Where things live: `cwd`, `gitBranch`, `version`, `sessionId`, `uuid`, `parentUuid`,
    `timestamp` (ISO 8601 with `Z`) sit on the top level of `user`, `assistant`, `system`,
    and `attachment` records, not on a header record. `cwd` and `gitBranch` can change
    mid-session, so the parser reads them per record.
  - Assistant messages are split: one `assistant` record per content block (thinking, text,
    or tool_use), all sharing `message.id`. Tool results arrive as `user` records whose
    content is `tool_result` blocks, plus a top-level `toolUseResult` with a tool-specific
    shape.
  - Subagent logs live in a nested `<session-id>/subagents/agent-*.jsonl` and have
    `isSidechain: true`. The parser reads them as ordinary files; the future scanner must
    decide whether to list them.
  - User-typed text: `promptSource == "typed"` (with `origin.kind == "human"`) is the
    reliable signal. `content` is a plain string or a list of `text` blocks. Not typed:
    `tool_result` records, `isMeta`, `isCompactSummary`, `promptSource` of `system` or
    `queued`, `origin.kind == "task-notification"`, and strings starting with
    `<local-command-caveat>`, `<task-notification>`, and similar tags. Pasted text is wrapped
    in `<pasted_content>` inside a typed prompt. Records without `promptSource` fall back to
    a `promptId` plus tag check (rare in the sample, so unverified).
  - **Wrong or unverified in DESIGN.md section 5:**
    - `ExitPlanMode` never appears as a `tool_use`. Plan mode shows up as `permission-mode`
      records (`plan`) and `attachment` records of type `plan_mode` and `plan_mode_exit`,
      the latter carrying `planFilePath`. The plan text lives in that file, not in the log.
    - `TodoWrite`, `TaskCreate`, `TaskUpdate` never appear in the sample. Background task
      state appears as `attachment` type `task_status`. Status rules need other evidence,
      or more logs, before they can be written.
    - `AskUserQuestion` matches: input `questions[]` (`question`, `header`, `multiSelect`,
      `options[]`), and the answer is in the `user` record's `toolUseResult.answers`
      (question text -> chosen label).
    - `Write` matches, but the path key is `file_path`; a few calls use `path` or
      `file_text`. The parser keeps the raw input dict, so the extractor must accept
      both.
    - Tool names are not limited to the built-ins: MCP tools appear as `mcp__<server>__<tool>`.
  - The sample comes from one user's recent logs (all 2.1.x). Older formats are untested.

- **Default output dir is `~/session-doc/`.** Override with `--out`. Refuse to write inside
  a git work tree unless explicitly forced, because logs hold paths and secrets that must
  not end up in a repository by accident.
- **Rule-based extraction, no LLM in the core.** Rules are deterministic, testable with
  fixtures, free, and need no network. Summaries may come later, outside the core.
- **Static HTML that works from `file://`; search index as `.js` files.** Browsers block
  fetching local files, so indexes load via `<script>` tags, split per project so only one
  is loaded at a time.
- **`generate` is idempotent and incremental.** Content hashes in `state.json` skip
  unchanged sessions. A Claude Code session-end hook will call the same command later.
- **Local only: no network, no sharing features.** Logs can contain secrets, so there are
  no telemetry, update checks, external requests in generated HTML, or publish/export
  features.
- **Archive raw source logs by default.** Copies go under `raw/` in the output dir, so docs
  survive the agent pruning old logs. Can be disabled. Source logs are never modified.

## 2026-11-03

- **Claude Code first.** Session log locations and formats differ per agent, so the MVP
  parses Claude Code logs only. Other agents are added as separate parsers.
- **Static HTML output.** No server or database. Output opens from `file://`, is easy to
  archive, and works with any static host.
- **Three layers: core, CLI, plugin.** The core stays free of CLI and plugin code so it can
  be used from the terminal, a Claude Code hook, or any other agent.
- **Python with uv.** `uvx session-doc` gives one-line use, and similar tools in this space
  are Python. Stdlib first; no runtime dependencies until one is justified here.
- **`src/` layout, hatchling, ruff, pytest.** Standard modern Python packaging.
- **`AGENTS.md` is the single source of agent instructions.** Copilot and Antigravity read it
  directly; `CLAUDE.md` imports it with `@AGENTS.md`. Tool-specific files only when needed.
- **English everywhere in the repo.** The tool is meant to be usable by anyone.
