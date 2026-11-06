# Design

Status: draft. Section 5 was checked against real Claude Code logs while writing the
parser; findings are in `docs/DECISIONS.md` (2026-11-07). Rows marked *unverified* below
could not be confirmed.

## 1. Goals and non-goals

**Goals**

- Find a past prompt, plan, decision, or status without scrolling through a session.
- Browse like a chat app: projects and sessions on the left, items for the selected
  session next to it, the selected item's content on the right.
- Work offline from plain files. No server, no account, no build step for the reader.
- Stay fast on years of logs (incremental generation, split search index).

**Non-goals**

- Not agent memory. We do not inject anything back into a session.
- No LLM calls in the core. Summaries may come later, outside the core.
- No sharing, publishing, or export features.
- Never modify or delete source logs.
- No analytics or cost dashboards.

## 2. Privacy and security principles

Session logs contain file contents, paths, command output, and sometimes secrets.

- Local only: no network calls, no telemetry, no update checks.
- Generated HTML makes no external requests (no CDN scripts, fonts, or images) and carries
  a CSP meta tag that blocks network access.
- Everything taken from logs is escaped before it reaches HTML. Raw HTML in logs is never
  passed through; logs can contain arbitrary text fetched from the web.
- Refuse to write the output directory inside a git work tree unless explicitly forced.
- The archived copies of source logs live only under the output directory.

## 3. UI layout

Three panes plus a search box:

1. **Sidebar**: projects, then sessions (newest first). A session is labelled with its
   first prompt (truncated) and date.
2. **Item list**: the selected session's items in chronological order. Each item has a kind
   badge: prompt, deliverable, decision, status. Items found by weak signals are marked
   "inferred".
3. **Reader**: the selected item rendered as Markdown.

Filtering by kind comes later. Chronological order is the only ordering for the MVP.

## 4. Data model

- **Project**: derived from the session's working directory.
- **Session**: one log file. Id, start and end time, git branch if present.
- **Record**: one parsed line of a log. Typed; unknown fields ignored.
- **Item**: what the UI shows. Kind (prompt | deliverable | decision | status), timestamp,
  title, Markdown body, confidence (explicit | inferred), and the ids of its source records.

Pipeline: log file -> records -> items -> pages and search index.

## 5. Extraction rules

| Kind | Explicit signals | Inferred signals |
|---|---|---|
| Prompt | `user` records with `promptSource == "typed"` (not tool results, not system-injected text) | none |
| Deliverable | Plan-mode exit (`plan_mode_exit` attachment; the plan is in `planFilePath`, `ExitPlanMode` was not observed), Markdown files created with `Write` (`file_path`) | Long assistant answers, answers with several headings, tables, or checklists |
| Decision | Answers to `AskUserQuestion` (`toolUseResult.answers`), plan approval | Phrases such as "let's go with", "decided", Korean equivalents (e.g. "~로 하자", "결정") |
| Status | `git commit` in Bash; *unverified:* `TodoWrite`, `TaskCreate`, `TaskUpdate` (not observed in sample logs) | none |

Thresholds (e.g. minimum length of a "long answer") are tuned against real logs and
recorded in `docs/DECISIONS.md`. Every rule has a fixture and a test.

## 6. Output layout and update flow

Default output directory: `~/session-doc/` (override with `--out`).

```
~/session-doc/
  index.html                 all projects
  assets/                    shared JS and CSS, vendored search library
  projects/<project>/
    index.html               sessions for this project
    search-index.js          search index for this project only
    sessions/<session>.html  one self-contained page per session
  raw/<project>/<session>.jsonl   archived copy of the source log
  state.json                 content hashes for incremental runs
```

`session-doc generate` is idempotent and incremental:

1. Scan the source directory (default: Claude Code's projects directory).
2. Skip sessions whose content hash is unchanged.
3. Archive a copy of each new or changed source log (can be disabled).
4. Parse, extract, and render the changed sessions.
5. Rebuild the affected project index and search index.

Today it is run by hand. A later Claude Code hook (session end) calls the same command.

## 7. Search

Search runs in the browser, so the pages must also work from `file://`, where browsers
block fetching local files. Search indexes are therefore plain `.js` files loaded with
`<script>` tags, split per project so only one project's index is loaded at a time.

The library (MiniSearch, FlexSearch, or similar) is vendored as a single file and chosen
by benchmarking on real logs. If logs outgrow this approach, a local server mode with
Pagefind is the fallback.

## 8. Open questions

- Markdown rendering: at generation time (needs a Python dependency) or in the browser
  (needs a vendored library plus sanitization)?
- Exact CSP policy that still allows inline scripts under `file://`.
- Project slug scheme for working directories that contain unusual characters.
