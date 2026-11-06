# Decisions

Short, append-only log. Newest first. Format: date, decision, why.

## 2026-11-07

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
