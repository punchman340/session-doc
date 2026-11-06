# session-doc

Turn coding-agent session logs into searchable HTML docs.

> **Status: early scaffold.** The CLI runs, but `generate` is not implemented yet.

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

### Status

- [ ] Parser
- [ ] Extractor
- [ ] HTML renderer
- [ ] Search
- [ ] Hook / plugin

## Usage

Not available yet. Once `generate` lands it will look like this:

```bash
uvx session-doc generate
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format .
```

See [AGENTS.md](AGENTS.md) for contributor and agent guidelines and
[docs/DECISIONS.md](docs/DECISIONS.md) for design decisions.

## License

[MIT](LICENSE)
