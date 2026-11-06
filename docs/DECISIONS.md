# Decisions

Newest first. Add a new entry instead of rewriting an old one. Format: date, decision,
why. Observed log structure lives in `docs/LOG_FORMAT.md`.

## 2026-11-07 (renderer)

- **Runtime dependencies: markdown-it-py and Jinja2, nothing else.** Approved by the
  maintainer. MarkupSafe and mdurl come with them.
- **Markdown is rendered at generation time with markdown-it-py, preset `js-default`.**
  That preset has raw HTML off; it must never be turned on. Links stay live only for
  `http:`, `https:`, and `mailto:`; relative paths, fragments, `javascript:`, `data:`, and
  `file:` stay as plain text. Images are never loaded and render as a link to the image.
  Table alignment becomes a class, because the CSP blocks `style` attributes. Items other
  than deliverables keep single line breaks, since people type them line by line and
  commit messages are wrapped on purpose.
- **Jinja2 with `autoescape=True`.** Rendered Markdown is the only value marked safe:
  one `|safe` in `session.html`, checked by a test.
- **The sidebar session list lives in the per-project `search-index.js` and is built by
  JS.** Without JS the sidebar shows a link to the project index, which lists every
  session. Only that project's JS file and index page change when a session is added,
  so no older session page is rebuilt. The maintainer chose this over a `nav.html` per
  project embedded in an iframe and over rewriting the sidebar block of every page on
  each run: search already needs JS, the sidebar can later be filtered by search from
  the same data, and it avoids iframe sizing quirks and a `frame-src` exception in the
  CSP.
- **CSP: `default-src 'none'; script-src 'self' file:; style-src 'self' file:;
  base-uri 'none'; form-action 'none'`.** All scripts and styles are external files, so
  no `'unsafe-inline'` is needed. In headless Chrome 156 from `file://`, `'self'` alone
  and `file:` alone each allowed the local scripts and styles, and `default-src 'none'`
  with neither blocked them, so the policy is enforced. Both sources are kept: browsers
  have differed on whether `'self'` matches other `file://` URLs (Firefox gives each
  file its own origin), and `'self'` keeps the pages working over http. Firefox is not
  tested yet. Pages also set `referrer` to `no-referrer` and turn off DNS prefetch.
- **Search: MiniSearch 7.2.0, vendored in `src/session_doc/assets/` with its MIT
  license.** The npm package ships no minified build, so `dist/umd/index.js` was
  minified with `npx terser@5.51.2 index.js --compress --mangle --comments false` and a
  one-line license header was added. Prefix search on title and text, every term
  required, titles boosted. One data file per project; the index is built in the
  browser the first time the search box gets focus. Full item text is indexed: on the
  real logs the largest project file is 0.8 MB.
- **Force flag: `--allow-in-git`.** The check looks for a `.git` directory or file in
  the output dir and every parent, without running git. On the maintainer's machine the
  home directory is a git work tree, so the default `~/session-doc` needs the flag there.
- **Search docs are carried over from the existing `search-index.js`.** When a session
  changes, the docs of the project's other sessions are read back from the file
  session-doc wrote last time instead of from a separate cache, so the output layout
  gains no files. If the file is missing or unreadable, those sessions are parsed
  again from `raw/` or the source.
- **`state.json` is keyed by the absolute path of the source log** and holds its sha256
  plus the metadata the index pages need (title, times, branch, item counts). It also
  holds a fingerprint (output format, session-doc version, decision phrases); when that
  changes, every session is rendered again, from `raw/` if its log is gone.
- **Project slug: directory name plus 8 hex digits of a sha256 of the full path.**
  Readable, filesystem-safe, and two projects with the same directory name stay apart.
  Windows drive paths are compared case-insensitively. A log without `cwd` uses the
  name of the directory that holds it. The session slug is the log file name, with a
  hash suffix on a clash.
- **Refuse a non-empty output dir without `state.json`.** This stops `--out ~` or a
  typo from overwriting someone's `index.html`. `state.json` is written first, so an
  interrupted first run can be resumed. Source and output dirs that contain each other
  are refused too.
- **Logs without user or assistant records get no page.** 1 of 85 real logs had none;
  it would only add an empty entry to the sidebar.
- **Subagent logs are skipped by directory name (`subagents/`).** The default source
  honors `CLAUDE_CONFIG_DIR`, as Claude Code does.
- **Atomic writes; only archives and `state.json` are fsynced.** Pages can be rendered
  again, archived logs may be the last copy. Unchanged files are not rewritten.
- **Times are shown in the local time zone of the machine that runs `generate`.**

## 2026-11-07 (review)

- **Config file lives outside the output dir.** `~/.config/session-doc/config.json` on
  Linux and macOS, `%APPDATA%\session-doc\config.json` on Windows. The output dir can be
  wiped and regenerated, and writing it can be refused inside a git work tree; neither
  should touch user settings. This replaces the `~/session-doc/config.json` path below.
- **Timestamps without an offset are read as UTC.** Mixing them with `Z` timestamps
  crashed sorting.
- **`git commit` is matched per command segment.** Heredoc bodies are set aside, then the
  command is split at top-level `;`, `&`, `|`, and newlines. Only a segment that starts
  with `git ... commit` counts, and only its own `-m` values (or the heredoc one of them
  refers to) form the message. Before, `&& python -m pytest` and a later `gh pr create`
  heredoc leaked into the message, and `echo "... git commit"` counted as a commit.
- **The parser keeps only what the extractor uses from large payloads.** `toolUseResult`
  is cut to `type`, `filePath`, `answers`, and `originalFile`; attachments keep top-level
  scalar fields only. Read results and command output made memory grow with log size.
- **Echoed commands and non-human origins are never typed prompts.** Text starting with
  `<command-*>` or `<bash-*>` tags is skipped, and so is any record whose `origin.kind`
  is not `human`, even with `promptSource == "typed"`. In the sample, 2 slash-command
  records had a `promptId` and no `promptSource`, so the fallback took them as prompts.
- **Default decision phrases are `decided` and `go with`.** `let's go with` was redundant.
  The defaults match none of the 334 typed prompts in the sample, because they are
  mostly not in English; user config phrases do the real work. Phrases match across any
  whitespace.
- **`AskUserQuestion` answers need the matching tool call.** An `answers` field from any
  other tool is not a decision.
- **`MultiEdit` on a plan file is replayed like `Edit`.**
- **Log format findings moved to `docs/LOG_FORMAT.md`.** This file keeps decisions only.

## 2026-11-07

- **Extractor thresholds (88-file sample, counts only).** Assistant text is merged per
  `message.id`. Of 1423 such messages the median is 89 characters, p75 263, p90 2329.
  A long answer is 1500 characters or more (about 15% of messages). Messages with 2+
  headings, a table, or a checklist qualify from 300 characters; every structured
  message in the sample was already above 500, so the floor only guards against tiny
  answers. Headings inside code fences do not count.
- **Plan bodies come from the log only.** The body is rebuilt from a `Write` to the plan
  file, with later edits replayed (using `originalFile` from the edit result when no
  `Write` was seen). Without that, the item says the body is not in the log; the file is
  never read. The `Write` to a plan file is not a second deliverable.
- **Decision phrases come from user config, not the repo.** The repo ships English
  defaults; the extractor takes extra phrases as a parameter. This keeps the repository
  English-only.
- **Parser checked against real Claude Code logs.** Findings, including where DESIGN.md
  section 5 was wrong, are in `docs/LOG_FORMAT.md`.
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
- **Local only.** Logs can contain secrets, so nothing leaves the machine and there are no
  publish or export features.
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
- **`src/` layout, hatchling, ruff, pytest.**
- **`AGENTS.md` is the single source of agent instructions.** Copilot and Antigravity read it
  directly; `CLAUDE.md` imports it with `@AGENTS.md`. Tool-specific files only when needed.
- **English everywhere in the repo.** The tool is meant to be usable by anyone.
