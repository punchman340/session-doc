# AGENTS.md

session-doc turns coding-agent session logs (JSONL) into searchable, static HTML docs.
Claude Code logs come first; other agents are added later as extra parsers.

## Language

All repository content is English: code, comments, docs, commit messages, issues, PRs.
Conversation with the maintainer may be in Korean.
No non-English text anywhere in the repo, including patterns, fixtures, and tests.
No emoji. Language-specific phrases belong in user config.

## Commands

```bash
uv sync                                          # install
uv run pytest                                    # tests
uv run ruff check . && uv run ruff format .      # lint + format
uv run ruff format --check .                     # format check, as CI runs it
uv run session-doc --help                        # run the CLI
```

## Docs

- `docs/DESIGN.md` is the spec. Read it before changing behavior; update it when
  behavior changes.
- `docs/LOG_FORMAT.md` records what real logs contain. Update it when a new check against
  real logs finds something.
- `docs/DECISIONS.md` is the decision log; add new entries at the top.

## Architecture

Keep three layers separate:

1. Core (parse, extract, render) has no CLI or plugin imports.
2. `cli.py` only parses arguments and calls the core.
3. The Claude Code plugin (later) only shells out to the CLI.

Pipeline: JSONL -> parser (typed records) -> extractor (items) -> renderer (static HTML
and search index; not written yet).

## Conventions

- Type hints on public functions. Prefer the standard library.
- Parsers ignore unknown fields: session formats change between agent releases.
- Output is static HTML with no external assets; everything lives under the output dir
  and must open from `file://` with no server.
- Only read the input paths given and write inside the output dir. Never modify source logs.
- Every new parser or extractor rule gets a fixture and a test.

## Privacy and security

Session logs contain file contents, paths, command output, and sometimes secrets.

- Make no network calls: no telemetry, no update checks.
- Escape all log content before it reaches HTML. Never pass raw HTML through.
- Add no external requests to generated HTML (no CDN scripts, fonts, or images); keep the CSP meta tag.
- Refuse an output dir inside a git work tree unless explicitly forced.
- Keep archived source logs only under the output dir.
- Checks against real logs run locally and record structure and counts only. Never copy
  real log content into the repo, a commit, or an issue.

## Git

- Commit messages use Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `ci:`,
  `chore:`. Subject in the imperative, body wrapped at 72 characters.
- The maintainer commits small changes directly to main.

## Boundaries

- Ask before adding a runtime dependency, changing the output layout, or renaming a CLI flag.
- Fixtures must be anonymized: no real paths, names, or secrets. Never commit real session logs.
- Record non-obvious choices in `docs/DECISIONS.md` instead of leaving them in chat.
