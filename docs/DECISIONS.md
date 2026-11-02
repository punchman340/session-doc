# Decisions

Short, append-only log. Newest first. Format: date, decision, why.

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
