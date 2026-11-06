# session-doc

Turn coding-agent session logs into searchable HTML docs.

> **Status:** `generate` works for Claude Code logs. The session-end hook does not exist yet.

## Why

Long agent sessions bury useful things: the prompt that worked, the plan or manual the
agent wrote, the decision you made an hour ago. Scrolling up to find them is slow, and
built-in search usually only matches titles.

session-doc reads the session logs your agent already writes to disk and builds a static,
searchable HTML site from them, organized by project instead of by scroll position.

## What it does

- Reads Claude Code session logs (`~/.claude/projects/**/*.jsonl`).
- Extracts prompts, decisions, deliverables (plans, manuals, long answers), and status with
  plain rules, no LLM.
- Writes static HTML with full-text search to `~/session-doc/`. It opens from disk and
  makes no network requests.
- Runs incrementally, so re-running only processes new or changed sessions.
- Other coding agents later, one parser at a time.

See [docs/DESIGN.md](docs/DESIGN.md) for the full design.

### Progress

- [x] Parser
- [x] Extractor
- [x] HTML renderer
- [x] Search
- [ ] Hook / plugin

## Usage

Not on PyPI yet. From a clone:

```bash
uv sync
uv run session-doc generate        # ~/.claude/projects -> ~/session-doc/
```

Then open `~/session-doc/index.html` in a browser. Run the same command again to pick up
new and changed sessions; unchanged ones are skipped.

| Flag | Meaning |
|---|---|
| `--source DIR` | Logs to read, searched recursively. Default: `$CLAUDE_CONFIG_DIR/projects` or `~/.claude/projects`. Subagent logs are skipped. |
| `--out DIR` | Output directory. Default: `~/session-doc/`. |
| `--no-archive` | Do not keep copies of the logs in `<out>/raw/`. By default they are kept, so sessions stay in the docs after Claude Code deletes old logs. |
| `--allow-in-git` | Write even if the output directory is inside a git work tree. Without it, session-doc refuses, because logs hold file contents and secrets. |

The pages open from `file://` without a server and make no network requests. Items open
without JavaScript; the session sidebar and search need it.

### Config

Optional: `~/.config/session-doc/config.json` on Linux and macOS,
`%APPDATA%\session-doc\config.json` on Windows.

```json
{"decision_phrases": ["settled on", "let's do"]}
```

A typed prompt that contains one of these phrases (whole words, any case) is listed as an
inferred decision. The built-in phrases are `decided` and `go with`. After a change to
the phrases, the next run renders every session again.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format .
uv run session-doc --help
```

See [AGENTS.md](AGENTS.md) for contributor and agent guidelines and
[docs/DECISIONS.md](docs/DECISIONS.md) for design decisions.

Suggested roles when working with coding agents:

- Claude Code: core logic, multi-file changes, tests, refactors.
- Copilot: inline edits, small fixes, PR review.
- Antigravity: HTML/CSS templates and visual checks in the browser.

## License

[MIT](LICENSE)
