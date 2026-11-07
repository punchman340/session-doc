# Design

Status: draft. Section 5 was checked against real Claude Code logs while writing the
parser and extractor; what the logs contain is in `docs/LOG_FORMAT.md`. Sections 3, 6,
and 7 describe the renderer, `generate`, and `hook` as built.

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
- In rendered Markdown, only `http:`, `https:`, and `mailto:` links are live. Images are
  never loaded; they become plain links.
- Refuse to write the output directory inside a git work tree unless explicitly forced.
- The archived copies of source logs live only under the output directory.

## 3. UI layout

Three panes plus a search box:

1. **Sidebar**: a link to all projects, the current project, then its sessions (newest
   first). A session is labelled with its AI-generated title if the log has one, else
   its first typed prompt (truncated), and its date. The session list is built by
   `app.js` from the project's data file (section 6), so a new session never requires
   rebuilding older pages. Without JS the sidebar shows a link to the project index,
   which lists every session. The top-level index lists all projects.
2. **Item list**: the selected session's items in chronological order. Each item has a kind
   badge: prompt, deliverable, decision, status. Items found by weak signals are marked
   "inferred".
3. **Reader**: the selected item rendered as Markdown. With no item selected it shows a
   summary of the session.

Items are selected with `#item-N` links and the CSS `:target` selector, so reading works
without JS. JS adds the sidebar session list, search, and a highlight on the selected
item in the list.

The search box searches the current project (section 7). Results replace the item list
and link to the item on its session page. Without JS the search box is hidden.

Filtering by kind comes later. Chronological order is the only ordering for the MVP.

## 4. Data model

- **Project**: derived from the session's working directory (the first `cwd` in the log).
  Its slug is the directory name made filesystem-safe plus 8 hex digits of a hash of the
  full path. Without a `cwd`, the name of the directory holding the log stands in.
- **Session**: one log file. Id, start and end time, git branch if present. Its slug is
  the log file name; a clash within a project gets a hash suffix.
- **Record**: one parsed line of a log. Typed; unknown fields ignored.
- **Item**: what the UI shows. Kind (prompt | deliverable | decision | status), timestamp,
  title, Markdown body, confidence (explicit | inferred), and the ids of its source records.

Pipeline: log file -> records -> items -> pages and search index.

## 5. Extraction rules

| Kind | Explicit signals | Inferred signals |
|---|---|---|
| Prompt | `user` records with `promptSource == "typed"` and a human origin (not tool results, not system-injected or echoed text) | none |
| Deliverable | Plan-mode exit (`plan_mode_exit` attachment with `planFilePath`), Markdown files created with `Write` | Long assistant answers, answers with several headings, tables, or checklists |
| Decision | Answers to `AskUserQuestion` (`toolUseResult.answers`) | Typed prompts containing a decision phrase |
| Status | A `git commit` command in `Bash` | none |

Details of the extractor (`src/session_doc/extractor.py`):

- Records with `isSidechain: true` are skipped.
- A typed prompt that matches a decision phrase becomes a *decision* item instead of a
  *prompt* item. Text inside `<pasted_content>` is not matched, but stays in the body.
- Decision phrases: English defaults `decided` and `go with`, matched as whole words,
  case-insensitive, across any whitespace. More phrases come from an optional user config
  file outside the repo and outside the output dir:
  `~/.config/session-doc/config.json` on Linux and macOS,
  `%APPDATA%\session-doc\config.json` on Windows (`{"decision_phrases": [...]}`).
  Language-specific phrases never enter the repo.
- `AskUserQuestion` answers count only when the matching tool call is in the log.
- `Write` is accepted with `file_path` or `path` for the target and `content` or
  `file_text` for the body. Other tools, including MCP tools, never produce deliverables.
- Failed tool calls (error results) produce no deliverable or status item.
- Plan-mode exit: the plan body is rebuilt from the log (last `Write` to `planFilePath`
  before the exit, with later `Edit` and `MultiEdit` calls replayed). If the log holds no
  such write, the item says the plan body is not in the log. Files outside the log are
  never read.
- Status from `git commit`: the command is split into segments at top-level `;`, `&`, `|`,
  and newlines, quote- and heredoc-aware. A segment counts if it starts with `git`
  (options allowed) followed by `commit`. The title is the first line of that segment's
  message (`-m`, `--message`, heredoc); otherwise it is `git commit`.

Thresholds (e.g. minimum length of a "long answer") are tuned against real logs and
recorded in `docs/DECISIONS.md`. Every rule has a fixture and a test.

## 6. Output layout and update flow

Default output directory: `~/session-doc/` (override with `--out`).

```
~/session-doc/
  index.html                 all projects
  assets/                    style.css, app.js, vendored MiniSearch and its license
  projects/<project>/
    index.html               sessions for this project
    search-index.js          session list (sidebar) and search docs for this project
    sessions/<session>.html  one page per session
  raw/<project>/<session>.jsonl   archived copy of the source log
  state.json                 content hashes and session metadata for incremental runs
```

`session-doc generate [--source DIR] [--out DIR] [--no-archive] [--allow-in-git]` is
idempotent and incremental:

1. Refuse to run if the output dir is inside a git work tree (a `.git` dir or file in it
   or any parent) unless `--allow-in-git` is given; if it is not empty and has no
   `state.json`, so session-doc did not create it; or if it and the source dir contain
   each other. Read the optional config (section 5); invalid JSON is an error.
2. Scan the source directory recursively for `*.jsonl` files. The default is Claude
   Code's projects directory, `$CLAUDE_CONFIG_DIR/projects` or `~/.claude/projects`.
   Directories named `subagents` are skipped.
3. Skip sessions whose sha256 is unchanged.
4. Copy each new or changed log to `raw/` (unless `--no-archive`) and parse the copy, so
   the page matches the archived bytes and the session stays after the agent prunes
   its logs.
5. Extract and render the changed sessions. A log with no user or assistant records gets
   no page.
6. Rewrite the affected projects' `index.html` and `search-index.js`, and the top-level
   index. Search docs of sessions not rendered in this run are carried over from the
   existing `search-index.js`. If that file is missing or unreadable, those sessions are
   parsed again from `raw/` or the source.
7. Write `state.json` last.

Pages of sessions whose log disappeared are never deleted and stay listed. Every file is
written atomically (temp file, then rename), and files whose content is unchanged are not
rewritten, so a run without changes writes nothing. If the session-doc version, the
output format, or the decision phrases change, every session is rendered again, from
`raw/` when its log is gone.

### Hook

`session-doc hook [--out DIR] [--allow-in-git]` is the Claude Code `SessionEnd` hook. It
runs steps 3 to 7 for one log instead of a scan:

1. Read the hook input JSON from stdin and take `transcript_path`. Input that is not a
   JSON object with a string `transcript_path` is an error. A path with a `subagents`
   directory, a missing file, or an empty file is ignored: nothing is written, not even
   the output dir.
2. Apply the same refusals as `generate` to the output dir (git work tree, not created
   by session-doc, not a directory). A refused dir is never written to, so the reason
   goes to stderr instead of the log. Read the config; invalid JSON is an error.
3. Take `<out>/.hook.lock`, created exclusively. If it is held, retry for up to 10
   seconds, then skip the update and log that. A lock older than 120 seconds is stale
   and is replaced.
4. Run the `generate` steps for that log only: skip if its sha256 is unchanged,
   otherwise archive, render, rewrite its project's `index.html` and `search-index.js`
   (other sessions' search docs are carried over), update the top-level index, and write
   `state.json` last. The state key is the resolved transcript path, as in `generate`.
   If the fingerprint (version, format, decision phrases) differs from `state.json`, the
   old one is kept so the next `generate` renders every other session again.
5. Release the lock.

It always exits 0 and prints nothing on success. Failures are appended to
`<out>/hook.log` as `<UTC timestamp> <stage>: <message>`, where the stage is `input`,
`check`, `config`, `lock`, `update`, or `skipped`. The message is the exception type,
plus its text for file-system, config, and session-doc errors; any other exception logs
only its type. Session content is never logged.

`session-doc hook --print-config` prints the `settings.json` snippet (a `SessionEnd`
command hook running `session-doc hook`, timeout 30) and exits. session-doc never edits
the user's settings file.

## 7. Search

Search runs in the browser, so the pages must also work from `file://`, where browsers
block fetching local files. Each project's `search-index.js` is therefore a plain script
(`window.sessionDocData = {...}`) loaded with a `<script>` tag. It holds the project's
session list and one document per item (kind, title, Markdown text, anchor). Only the
current project's file is loaded.

The library is MiniSearch 7.2.0, vendored minified with its MIT license in `assets/`.
The index is built in the browser the first time the search box gets focus. Queries use
prefix matching on title and text, require every term, and boost title matches. On the
real logs (11 projects, 345 items) the largest project data file is 0.8 MB. If logs
outgrow this approach, a local server mode with Pagefind is the fallback.

## 8. Open questions

- Search across projects. With one index per project, the top-level index has no
  search box.
- More status signals. `TodoWrite`, `TaskCreate`, and `TaskUpdate` were not observed in
  the sample logs; background tasks show up as `task_status` attachments instead.
