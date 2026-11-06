---
name: repo-review
description: Full read-only review of this repository (style, docs, code, tests, packaging). Use when asked to review or audit the repo as a whole, not a single diff.
---

# Repo review

Read every tracked file (`git ls-files`), then check the five areas below and write one
report. This is read-only: make no edits to the repo.

## Areas

1. **Tone and style.** Non-ASCII characters and emoji anywhere. Text that reads as
   AI-generated: filler words (robust, seamless, comprehensive, leverage, ensure, delve,
   crucial), hedging, excess bold, marketing tone, textbook phrasing, redundant
   summaries. Covers docs, comments, docstrings, and templates.
2. **Docs consistency.** README, AGENTS.md, CLAUDE.md, `docs/DESIGN.md`,
   `docs/DECISIONS.md`, and `docs/LOG_FORMAT.md` agree with each other and with the code.
   Flag stale statements, duplicates, and rules the code does not follow. For AGENTS.md:
   length, content an agent could learn from the code, missing commands or rules.
3. **Architecture and code.** Layer separation (core never imports `cli`), module
   boundaries, naming, dataclass design, error handling, type hints, dead code, needless
   complexity. Parser and extractor robustness against format changes. Memory and speed
   on large logs. For each privacy rule in DESIGN.md section 2, say whether it is
   implemented and tested or only stated.
4. **Tests and fixtures.** Coverage gaps, brittle tests, fixtures that are not fully
   synthetic.
5. **Packaging and git.** `pyproject.toml` metadata, `.gitignore` and `.gitattributes`,
   `uv.lock`, CI workflow (action versions, matrix, Windows coverage, permissions), PR and
   issue templates, recent commit message format.

Run `uv run pytest`, `uv run ruff check .`, and `uv run ruff format --check .` and report
the results.

## Rules

- Make no edits to the repo.
- Before calling a suspected bug must-fix, confirm it with a throwaway script in the
  session scratchpad or another directory outside the repo.
- Checks against real session logs print structure and counts only, never content.
- Never recommend a PR-only workflow. The maintainer commits small changes to main.
- The repo stays English-only with no emoji; flag any violation.
- No praise. Report problems only.

## Report format

Group findings by the five areas. One row per finding:

| Where | Sev | Problem | Change |
|---|---|---|---|
| `path:line` | must-fix / should-fix / nit | What is wrong | The concrete change |

- must-fix: wrong output, crash, data or privacy risk, or docs that contradict the code.
- should-fix: likely to cause a bug or confusion soon.
- nit: style or small cleanup.

End with a short ordered "Fix first" list. Keep the report tight.
