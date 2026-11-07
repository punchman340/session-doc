# session-doc

Turn coding-agent session logs into searchable HTML docs.

> **Status:** `generate` works for Claude Code logs. `session-doc hook` updates one session when it ends; the plugin does not exist yet.

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
- [x] SessionEnd hook
- [ ] Plugin

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

### Hook setup

`session-doc hook` updates the docs for the one session that just ended, so you do not
have to run `generate` by hand. Print the snippet and merge it into
`~/.claude/settings.json` yourself (session-doc never edits that file):

```bash
session-doc hook --print-config
```

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {"type": "command", "command": "session-doc hook", "timeout": 30}
        ]
      }
    ]
  }
}
```

`session-doc` must be on the `PATH` that Claude Code uses. From a clone, use a command
such as `uv run --project /path/to/session-doc session-doc hook` instead. `hook` takes
`--out DIR` and `--allow-in-git` like `generate`, and reads the same config file.

- It reads the hook input JSON from stdin and processes only `transcript_path`: it
  archives that log, renders its page, and updates its project's index and
  `search-index.js`. Subagent transcripts and missing or empty files are ignored.
- It always exits 0 and prints nothing on success. Errors go to `<out>/hook.log`, one
  line each with a UTC timestamp, the stage, and the error; session text is never logged.
  If the output dir is refused (inside a git work tree, or not created by session-doc),
  nothing is written there and the reason goes to stderr.
- Two sessions ending at once are serialized with `<out>/.hook.lock`. A session waits up
  to 10 seconds, then skips its update and logs that; the next `generate` or hook run
  picks it up.
- If session-doc or the config changed since the last `generate`, the hook updates only
  its session; run `generate` to render the rest again.
- On the maintainer's machine (87 logs, 83 MB), one session took about 0.8 s (0.9 s for
  the largest, 5 MB), mostly Python startup and imports.

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
