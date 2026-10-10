# PLAN: `tokens-doctor`, a cross-platform tool to cut Claude Code's fixed context overhead

A small open-source command-line tool for **Linux and macOS** that audits and reduces the fixed context overhead of Claude Code (MCP tool definitions, skills, memory files, plugins, agents, built-in tools). It applies the rules from the doc "Come minimizzare l'overhead fisso in Claude Code" and is meant to live in a public GitHub repository that anyone can install and run. **Windows users run it inside WSL** (Windows Subsystem for Linux), so there is no native Windows support to maintain; see section 4.

The tool has two parts: a **static** audit/fix of the config that creates the fixed overhead (section 8), and a **live session monitor** built on Claude Code hooks that tells you when to `/compact` or `/clear` (section 7).

## 1. Goal and scope

**Goal:** keep fixed overhead under about 15% of the context window, in a way that is measurable, repeatable and identical on Linux, macOS and WSL.

**In scope**
- Audit: measure and report each overhead component.
- Fix: apply safe, reversible changes, with confirmation.
- Verify: before/after comparison.
- Monitor: watch live Claude Code sessions through hooks, check each session's context against the thresholds, and notify (or optionally act) with a recommendation: `/compact` or `/clear` (section 7).
- Distribution: installable with one command, tested on Linux and macOS in CI.

**Out of scope**
- Running `/compact` or `/clear` by default. They are interactive commands that hooks cannot execute; the monitor **suggests** by default, and acting is an opt-in, best-effort feature (section 7.7).
- Changing the model, plan or billing.
- Touching anything outside Claude Code's config and the chosen project folder.
- Native Windows (PowerShell, `cmd`, `claude.cmd`). Windows users use WSL; revisit native support only if there is demand.
- Any network access or telemetry (see section 10).

## 2. Key decision: one Python core

| Option | Verdict |
|---|---|
| PowerShell 7 | Needs `pwsh` on Linux and macOS; many users don't have it |
| Bash + `jq` | JSON merging and `@import` recursion get fragile; `jq` is an extra dependency |
| **Python 3, standard library only** | Preinstalled or one install away everywhere; strong path, JSON and file handling; easy tests |

**Decision:** Python 3.9+ with **zero third-party runtime dependencies**. No per-OS scripts to maintain: one codebase, one test suite, one behavior.

## 3. Design principles

1. **Dry-run by default.** Nothing changes unless the `fix` command is used (static cleanup) or monitor `apply` mode is enabled (section 7.7).
2. **Always back up** before modifying a file.
3. **Never delete, only move** to a quarantine folder, so every step is undoable.
4. **Ask per change** unless `--yes` is passed.
5. **Local only:** no network calls, no telemetry.
6. **Verify flags against the installed version.** Claude Code commands and flags change between releases; the tool reads `claude --help` and skips unsupported steps with a clear message.
7. **No OS-specific code outside one module** (`platform.py`).

## 4. Platform handling

**Supported:** Linux, macOS, and Windows through WSL (which is just Linux from the tool's point of view). **Not supported:** native Windows.

All differences live in `platform.py`; the rest of the code uses only `pathlib` and the helpers below.

| Area | Linux (incl. WSL) | macOS | Handling |
|---|---|---|---|
| Home / config root | `~/.claude` | `~/.claude` | `Path.home()`; allow override via `--config-dir` or an env var (verify the official one) |
| Finding the CLI | `claude` | `claude` | `shutil.which("claude")`, run with `shell=False` |
| Case sensitivity | Sensitive | Usually insensitive | Match `CLAUDE.md` exactly, warn on near-duplicates |
| Line endings | LF (CRLF if edited from Windows) | LF | Read and write bytes, preserve the file's existing style |
| Encoding | UTF-8 | UTF-8 | Always `encoding="utf-8"` explicitly |
| Symlinks | Common | Common | Follow, resolve to real path, avoid double-counting |
| Scheduling (optional) | cron / systemd timer | launchd | Documented in the README only, not automated |

### Windows users: WSL

- **Install and run everything inside WSL**, including Claude Code itself. The tool only sees the WSL home (`~/.claude`), so it audits the Claude Code that runs in WSL.
- **A Claude Code installed natively on Windows is a separate setup** with its own config under the Windows user profile. The tool does not touch it. The README must say this clearly, since it is the most likely source of confusion.
- **Detection:** `platform.py` detects WSL (for example by checking `/proc/version` for "microsoft") and prints a one-line notice naming the config path being audited.
- **Project folders on the Windows drive** (`/mnt/c/...`) work but are slower and may have odd permissions. Warn and recommend keeping projects in the WSL filesystem.
- **Path hygiene:** never convert or follow Windows-style paths found in config; report them as "outside WSL, skipped".
- **Setup docs:** a short README section, "Windows (WSL)", with the install steps for WSL, Python and `pipx`.

## 5. Repository layout

```
tokens-doctor/
├─ README.md
├─ PLAN.md
├─ LICENSE
├─ CHANGELOG.md
├─ CONTRIBUTING.md
├─ SECURITY.md
├─ pyproject.toml
├─ .gitignore
├─ .gitattributes                # normalize line endings in the repo
├─ .github/
│  ├─ workflows/ci.yml           # test matrix (see section 9)
│  ├─ workflows/release.yml      # build and publish on tag
│  └─ ISSUE_TEMPLATE/            # bug report asks for --redact output
├─ src/tokens_doctor/
│  ├─ __init__.py
│  ├─ __main__.py                # python -m tokens_doctor
│  ├─ cli.py                     # argparse, orchestration, exit codes
│  ├─ config.py                  # thresholds, defaults
│  ├─ platform.py                # all OS-specific logic
│  ├─ measure.py                 # token estimation, size scanning
│  ├─ mcp.py                     # MCP audit and actions
│  ├─ memory.py                  # CLAUDE.md and @import audit
│  ├─ skills.py                  # skills audit and actions
│  ├─ extensions.py              # plugins, agents, custom commands
│  ├─ builtin_tools.py           # permissions.deny suggestions
│  ├─ backup.py                  # backup, quarantine, manifest, restore
│  ├─ report.py                  # console, Markdown, JSON, redaction
│  ├─ session.py                 # transcript parsing: tokens, messages, compactions
│  ├─ decide.py                  # decision engine (thresholds -> none/compact/clear)
│  ├─ state.py                   # per-session state files (XDG / macOS paths)
│  ├─ hooks.py                   # entry points for Claude Code hook events
│  ├─ watcher.py                 # idle timer (detached background process)
│  ├─ notify.py                  # desktop notification + sound, per platform
│  ├─ act.py                     # optional apply via tmux (opt-in)
│  └─ install_hooks.py           # merge/remove hook entries in settings.json
└─ tests/
   ├─ fixtures/                  # fake .claude trees
   └─ test_*.py
```

## 6. Interface

```
tokens-doctor [audit] [options]       # default command, changes nothing
tokens-doctor fix [options]           # perform static changes (config cleanup)
tokens-doctor restore <backup-id>     # undo a previous `fix` run

# session monitor (section 7)
tokens-doctor check [--session ID | --latest]   # workflow 1: check one session now
tokens-doctor install-hooks [--scope user|project]   # register the hooks
tokens-doctor uninstall-hooks
tokens-doctor hook <event>            # called by Claude Code, reads JSON on stdin
tokens-doctor watch --session ID      # internal: idle timer, started by a hook
tokens-doctor notify-test             # try the notification and sound
```

| Option | Meaning |
|---|---|
| `--scope user\|project\|both` | Default `both` |
| `--project-path PATH` | Default: current directory |
| `--config-dir PATH` | Override the user config location |
| `--window N` | Context window in tokens, default 200000 |
| `--only mcp,memory,skills,extensions,builtin` | Limit the phases |
| `--yes` | Skip per-change prompts |
| `--report-dir PATH` | Where reports are written |
| `--format text\|md\|json` | Output format |
| `--redact` | Replace user names and home paths in reports, safe to paste into issues |
| `--no-color` | Plain output |

Exit codes: `0` ok and under targets, `1` over targets, `2` error.

## 7. Session monitor workflows (hooks)

The monitor is the dynamic half of the tool. It looks at one live session, applies the same rules as the doc (threshold T with its modifiers, compaction count, idle time, peak hours), and recommends `/compact` or `/clear`.

> Assumption to confirm: in the request, the first action was written `/context`. That command only *displays* usage, so this plan reads it as **`/compact`** (compact or clear). `/context` is used as a calibration source, not as an action.

### 7.1 Architecture

```
Claude Code event  ->  tokens-doctor hook <event>  ->  session reader  ->  decision engine  ->  notifier (suggest)
 (Stop, SessionEnd,     reads JSON on stdin            (transcript)       (thresholds)         or act.py (apply, opt-in)
  PreCompact, ...)
```

- Hook entry points are tiny and fast (target under 1 second) and **always exit 0**: a monitoring bug must never block or slow Claude.
- Anything slow (the idle wait, notifications) runs in a **detached background process**.
- Hooks receive JSON on stdin that includes the transcript path and session information (verify exact fields on the installed version).
- **Hooks must print nothing to stdout.** For some events, stdout is injected into Claude's context, which would defeat the purpose.

### 7.2 Reading a session's context sizes

| Quantity | Source | Accuracy |
|---|---|---|
| Total context in use | Token usage of the last assistant message in the transcript (input + cache read + cache creation tokens; verify field names) | Good |
| Fixed overhead per component (MCP, skills, memory, plugins, built-in tools) | Static estimates from the audit phases in section 8 | Estimate |
| Messages portion | Total minus fixed overhead | Derived |
| Tool-output share of messages | Size of tool-result blocks in the transcript | Good |
| Number of messages sent | Count of user entries in the transcript | Good |
| Previous compactions | Count of compaction markers in the transcript, plus a counter incremented by the `PreCompact` hook | Good |
| Idle minutes | Time since the last transcript entry | Good |
| Peak / off-peak | User-defined hours in `config.json` (not readable from the server) | User input |

Per-component sizes are **not** available from the transcript, so they are estimates. If `/context` can be captured non-interactively on the installed version, use it to calibrate the estimates and record the error margin.

The transcript contains conversation text. The monitor reads only structure and token counts, never stores or prints message content, and never sends anything anywhere.

### 7.3 Decision engine (`decide.py`)

A pure function, easy to test: inputs are the numbers above plus config; output is `{action: none|compact|clear|fix-config, reasons[], T, ratio}`.

Rules, taken from the doc:
1. `usable = window - fixed overhead`; `ratio = messages / usable`.
2. Start at **T = 70%**, then apply the modifiers: number of messages, previous compactions, tool-output weight, peak load, task boundary, planned absence. Floor 40%, compact immediately from 80%.
3. **Component checks:** if a fixed component is over its target (MCP 5%, skills 2%, memory 3%, total 15%), recommend `fix-config` (run the `audit` / `fix` commands), because compaction cannot reduce it.
4. **Clear instead of compact:** 2 or more previous compactions; an unrelated next task; or a long idle gap (see the idle table in the doc) with the context large.
5. **Idle rules:** under 20k tokens, ignore idle; 5 to 60 minutes with messages at or above T-15, recommend compacting; over 1 hour or overnight, prefer clear plus a handoff.
6. Every recommendation carries its reasons and numbers, so the notification can say why.

Peak load and "task boundary" are not observable by the tool: peak comes from config, and the task boundary is a user flag (`--task-done`) or is assumed false.

### 7.4 Workflow 1: check a specific session on demand

`tokens-doctor check --session <id>` (or `--latest`): read the session, run the decision engine, then suggest or apply.

It can also run automatically at the end of every turn from the `Stop` hook, with two safeguards against noise: a **cooldown** per session and notifying only when the recommendation **changes level** (for example none to compact).

### 7.5 Workflow 2: inactivity after the `Stop` hook

1. The `Stop` hook fires when Claude finishes responding (it does not fire when the user interrupts).
2. The hook starts a detached `tokens-doctor watch --session <id> --idle 5m` and exits immediately. One watcher per session (pid file); a new one replaces the old.
3. The watcher sleeps. A `UserPromptSubmit` hook (the user is back) cancels it by writing a cancel token.
4. On waking, the watcher re-reads the transcript: if there is **no newer activity**, it runs the decision engine **with idle context** and suggests or applies.
5. Safeguards: maximum watcher lifetime (default 2 hours); exit if the session has ended; one notification per idle period.

Why 5 minutes: it matches the prompt-cache lifetime, so a recommendation at that moment is the last cheap chance to compact before the cache goes cold. The value is configurable (a longer cache may exist in some setups).

### 7.6 Workflow 3: `/exit` (the `SessionEnd` hook)

- The session is already ending, so **compacting is no longer possible**. The useful output is a recommendation for the *next* time: resume with `/compact`, or start fresh with `/clear` plus a handoff.
- The monitor reads the final state, runs the decision engine (idle treated as "leaving"), and notifies: for example "Context 71%: next time compact before leaving" or "Task finished: start fresh with /clear".
- Optional: write a **handoff skeleton** to the state folder (last user request, files touched from tool calls, open errors if detectable). No model is called; it only collects facts from the transcript.
- Clean up: remove the pid file, cancel any watcher, delete stale state.

### 7.7 "Suggest" and "apply"

**Suggest (default)** is a desktop notification with a sound:

| Platform | Notification | Sound |
|---|---|---|
| macOS | `osascript` (`display notification`) | `afplay` on a system sound |
| Linux | `notify-send` | `paplay` or `canberra-gtk-play`, falling back to the terminal bell |
| WSL | A Windows toast through `powershell.exe` (or `wsl-notify-send` if present) | `powershell.exe` system sound or beep |

- All of this is verified at runtime: if a tool is missing, fall back (print to the terminal, bell) and say how to enable the full version. `tokens-doctor notify-test` checks the setup.
- Options: sound on/off, quiet hours, cooldown, and an urgency level for "compact now".
- The notification text includes the action, the session folder name, and the main numbers (for example "Compact: messages 62% of usable, T=45%, idle 5 min").

**Apply (opt-in, best effort).** As far as the hooks documentation shows, a hook cannot type a slash command into the running session. The only portable mechanism is a terminal multiplexer:

- If the session runs inside **tmux**, the monitor can send `/compact <focus note>` to that pane with `tmux send-keys`. The `SessionStart` hook records the pane id so the session can be mapped to a pane.
- **Safety checks before sending:** the pane's foreground process is `claude`; the session is idle (no activity for the configured time); the pane id still matches; the user has not typed in the last seconds.
- **`/clear` is never sent automatically** unless `--allow-clear` is set and a handoff skeleton has been saved first, because it destroys context.
- Without tmux, or if any check fails, fall back to **suggest**.
- Treat this feature as experimental; document its limits clearly in the README.

### 7.8 Installing the hooks

- `tokens-doctor install-hooks` merges entries into `settings.json` (user or project scope) for: `Stop`, `UserPromptSubmit`, `SessionStart`, `SessionEnd`, `PreCompact`. It backs up the file first, preserves other hooks, and is idempotent. `uninstall-hooks` removes only its own entries.
- Hook commands use the absolute path of the installed `tokens-doctor`, so they work regardless of `PATH` in the session.
- Changes to hooks may only apply to **new** sessions (verify), so the command says so after installing.
- Hook definitions are tagged with a marker so the tool can recognize its own entries.

### 7.9 Configuration (`config.json`)

Stored under `$XDG_CONFIG_HOME/tokens-doctor/` on Linux and `~/Library/Application Support/tokens-doctor/` on macOS. JSON, so Python 3.9 needs no extra dependency.

| Key | Default | Meaning |
|---|---|---|
| `window` | 200000 | Context window in tokens |
| `idle_minutes` | 5 | Inactivity before the idle check |
| `mode` | `suggest` | `suggest` or `apply` |
| `allow_clear` | false | Allow automatic `/clear` (only with a saved handoff) |
| `peak_hours` | none | Local hours counted as peak, for the T modifier |
| `thresholds` | doc defaults | Base T, floor, ceiling, component targets |
| `sound` | true | Play a sound with the notification |
| `quiet_hours` | none | No notifications in these hours |
| `cooldown_minutes` | 15 | Minimum time between notifications per session |

## 8. Static audit and fix phases

### Phase 0: Preflight
- Detect OS (Linux, macOS, WSL), Python version; locate `claude` and record `claude --version`.
- Resolve user and project config paths and report which exist.
- Read `claude --help` and `claude mcp --help` to detect supported flags.
- Create a run folder for backups and reports.

### Phase 1: Baseline measurement
- Try to capture real numbers from `/context` in a fresh session, if the installed version allows non-interactive use. Otherwise fall back to file-based estimation (characters / 4), clearly labeled as an estimate.
- Estimate tokens per component and store the baseline as JSON.

### Phase 2: MCP servers (highest impact)
- **Audit:** collect servers from user config, `.mcp.json` and `claude mcp list`; per server: name, scope, tool count if obtainable, estimated token cost; flag servers above a tool-count threshold (default 15).
- **Actions:** offer to disable or remove unused servers (`claude mcp remove <name>`); offer to move user-scope servers into a project's `.mcp.json`; generate a `minimal.mcp.json` from servers the user marks essential, plus a launcher hint using `--mcp-config` and `--strict-mcp-config` when supported.

### Phase 3: Memory files (CLAUDE.md and imports)
- **Audit:** find every applicable `CLAUDE.md` (user, project, parent folders to the filesystem root); resolve `@imports` recursively with depth limit and cycle detection; flag sections that look like changelogs, history or very long examples.
- **Actions:** report per-file tokens against the target (about 2,000 tokens in total; warn above 5% of the window); for large imports, propose a one-line pointer and show a diff. The tool never rewrites prose on its own.

### Phase 4: Skills
- **Audit:** scan the user and project `skills` folders; read `name` and `description` from front matter; estimate tokens; flag descriptions over a threshold (default 300 characters).
- **Actions:** move selected skills to quarantine; offer to move a global skill into the one project that uses it.

### Phase 5: Plugins, agents, custom commands
- Audit agents and commands folders and installed plugins (what each brings in).
- Actions: disable unused plugins only through supported commands or config keys; quarantine old agents and commands.

### Phase 6: Built-in tools
- Read `settings.json` and `settings.local.json`; suggest `permissions.deny` entries for tools the user confirms they never use; merge JSON without overwriting other keys. Low impact, runs last.

### Phase 7: Verify and report
- Re-measure and compare with the baseline; write `report-<timestamp>.md` and `.json`; print the target table with OK / WARN.

## 9. Targets (configurable)

| Component | Target |
|---|---|
| MCP tools | under 5% of the window |
| Skills | under 2% |
| Memory files | under 3% |
| Total fixed overhead | under about 15% |

## 10. Safety, privacy and trust (important for a public repo)

- **Backups:** each modified file is copied to `backup/<id>/` with its relative path, plus a `manifest.json` of every action. `restore` replays it in reverse.
- **Quarantine:** moved items go to `quarantine/<id>/`, never deleted.
- **Validation:** JSON edits are parsed after writing; on failure the original is restored automatically.
- **Local only:** no network, no telemetry, no external dependencies. State this in the README and keep it true.
- **Reports may contain paths and file names.** `--redact` removes usernames and home paths so users can share output in issues.
- **Never read secrets deliberately:** the tool reads config structure and sizes. It does not print env values from MCP configs (mask them in reports).
- **Running state:** if a `claude` process appears to be writing to the config files, stop and explain.
- **Monitor privacy:** transcripts contain conversation text. The monitor reads only structure and token counts, stores no message content in state files or reports, and makes no network calls.
- **Hooks never block Claude:** every hook exits 0 quickly and writes nothing to stdout; errors go to a log file in the state folder.
- **Automatic actions are opt-in and conservative:** no `/clear` without `--allow-clear` and a saved handoff; keystroke injection only with the checks in section 7.7.
- Add `SECURITY.md` with a private contact for vulnerability reports.

## 11. Testing and CI

**Tests (`pytest`)**
- Unit: token estimation, `@import` resolution (including cycles), front-matter parsing, JSON merge, backup and restore, path handling with spaces and non-ASCII characters.
- Fixtures: fake `.claude` trees (big `CLAUDE.md`, many skills, multi-server `.mcp.json`) built in `tmp_path`.
- Dry-run test: running `audit` (without `fix`) leaves the tree byte-identical (hash comparison).
- Round-trip test: `fix`, then `restore`, then compare hashes with the original.
- Line-ending test: CRLF and LF files are written back with their original endings.

**Monitor tests**
- Transcript fixtures (JSONL) with known token usage, tool results, compaction markers and timestamps; the session reader returns the expected numbers.
- Decision engine: table-driven tests of every rule in section 7.3 (base T, each modifier, floor and ceiling, clear-after-2-compactions, idle rules, component over target).
- Hooks: feed sample stdin JSON to `tokens-doctor hook <event>`; assert exit code 0, empty stdout, correct state changes.
- Watcher: use a fake clock; assert it notifies after idle, and does not notify if a newer entry appears or the cancel token is written.
- Notifier: backends tested with stub executables (`notify-send`, `osascript`, `powershell.exe`) on PATH; fallback chain tested when they are missing.
- Install-hooks: merge into an existing `settings.json`, check idempotency, preservation of other hooks, clean uninstall.
- Act (tmux): tested against a stub `tmux`; verifies every safety check and that `/clear` is never sent without `--allow-clear`.

**CI (`.github/workflows/ci.yml`)**
- Matrix: `ubuntu-latest`, `macos-latest` × Python 3.9, 3.11, 3.13.
- WSL is covered by the Linux job. Optionally add a `windows-latest` job using the `Vampire/setup-wsl` action as a later smoke test, once the core is stable.
- Steps: install, lint (`ruff`), type-check (optional `mypy`), `pytest`.
- A smoke test runs `tokens-doctor audit` and `tokens-doctor check` against fixtures on each OS in the matrix.
- Real Claude Code is not required in CI; calls to `claude` are tested through a fake executable stub.

## 12. Distribution and documentation

- **Packaging:** `pyproject.toml` with a console script `tokens-doctor`; works as `python -m tokens_doctor` too.
- **Install paths in the README:** `pipx install tokens-doctor` (after PyPI release), or `pipx install git+https://github.com/<user>/tokens-doctor`, or run from a clone with no install.
- **Releases:** semantic versioning, `CHANGELOG.md`, tagged releases; optional PyPI publishing with trusted publishing from GitHub Actions.
- **README contents:** what it does, a before/after example, install on Linux, macOS and WSL, safety guarantees, how to restore, how estimates compare with `/context`, FAQ (WSL setup, native Windows not supported, projects on `/mnt/c`).
- **Compatibility note:** a "tested with Claude Code version X" line, updated per release, since flags and file locations may change.
- **License:** MIT is the usual choice for a small tool; confirm before publishing.

## 13. Milestones

| # | Milestone | Done when |
|---|---|---|
| M1 | Repo skeleton, packaging, CI matrix green on Linux and macOS | Empty CLI runs on both in CI |
| M2 | `platform.py`, measurement, baseline JSON | Baseline produced on a real setup |
| M3 | Memory and Skills audit | Report matches manual counts within about 10% |
| M4 | MCP audit and actions | Backup, remove and restore verified |
| M5 | Extensions and built-in tools phases, reporting, restore | Round-trip hash test passes on Linux and macOS |
| M6 | Session reader and decision engine (`check`, workflow 1) | Fixture transcripts give the expected recommendations; table-driven rules pass |
| M7 | Notifier with sound on macOS, Linux and WSL | `notify-test` works on each, with fallbacks |
| M8 | Hooks, `install-hooks`, idle watcher (workflows 2 and 3) | End-to-end run with a real session: idle notification after 5 minutes, exit notification on `/exit` |
| M9 | Optional apply via tmux, handoff skeleton | Safety checks tested; documented as experimental |
| M10 | Calibration against `/context`, README, first release | Error margin documented, `v0.1.0` tagged |

## 14. Acceptance criteria

- `audit` and `check` change nothing and produce a clear report, on Linux, macOS and WSL.
- Every applied change is listed, backed up and restorable with one command.
- CI is green on Linux and macOS for every supported Python version.
- A Windows user can follow the README's WSL section and run the tool successfully.
- A new user can install and run it from the README in under five minutes.
- After applying recommended changes on a test setup, each component is at or under its target, or the report explains what remains.
- Workflow 1: `check` on a real session returns a recommendation with reasons, and a notification with sound when the threshold is crossed.
- Workflow 2: after a turn ends and 5 idle minutes pass, exactly one notification appears; sending a new prompt before that cancels it.
- Workflow 3: `/exit` produces a next-time recommendation notification and cleans up its state.
- Hooks never slow or block Claude (exit 0, under about 1 second, no stdout), and `uninstall-hooks` leaves `settings.json` as it was.
- Apply mode never sends `/clear` without `--allow-clear` and a saved handoff, and falls back to suggest whenever a safety check fails.
- No network calls, no secrets and no conversation text in any report or state file.

## 15. Open questions to resolve first

1. Can `/context` output be captured non-interactively on the installed Claude Code version? If not, rely on estimates and calibrate manually.
2. Does `claude mcp list` support JSON output? Otherwise parse config files directly.
3. Exact config locations and any official environment variable for overriding them, on Linux, macOS and WSL.
4. Is there a supported way to disable plugins from the CLI, or only by editing config?
5. License and minimum Python version (3.9 suggested).
6. PyPI release, or git-install only at first?
7. WSL: is it enough to document the setup, or should CI include a real WSL job from the start? (Suggested: document first, add the CI job later.)
8. Confirm that the first action in workflow 1 is `/compact` (not `/context`, which only displays usage).
9. Exact fields in the hook stdin JSON and in the transcript (`session_id`, token usage fields, compaction markers) on the installed Claude Code version.
10. Is there any supported way for a hook to trigger `/compact` or `/clear` directly? If so, prefer it over the tmux approach for apply mode.
11. Does the `Stop` hook environment expose enough to find the tmux pane (for example `TMUX_PANE`)?
12. Does a hook edit in `settings.json` apply to already-running sessions, or only new ones?
13. Should the 5-minute idle value follow a longer cache lifetime if one is available on the user's plan?
