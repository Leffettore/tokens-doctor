# tokens-doctor

Keep [Claude Code](https://claude.com/claude-code)'s **fixed context overhead** small, and get told when to `/compact` or
`/clear`. Linux, macOS and Windows through WSL. Python 3.9+, standard library only, no network, no telemetry.

Two parts:

- **Static audit / fix**: measures what sits in every session's context before you type anything (MCP servers,
  skills, `CLAUDE.md` and its `@imports`, plugins, agents, commands, built-in tools), and trims it with backups.
- **Session monitor** (hooks): after each turn and after 5 idle minutes it checks the live session and sends a desktop
  notification with a recommendation: `/compact`, `/clear`, or trim the config. Optionally it can type `/compact` for you in tmux.

## Install

```bash
pipx install git+https://github.com/Leffettore/tokens-doctor     # or: pipx install tokens-doctor (once on PyPI)
# or from a clone, no install:  PYTHONPATH=src python3 -m tokens_doctor audit
```

**Windows (WSL):** install and run everything inside WSL, Claude Code included:
`wsl --install`, then in the WSL shell `sudo apt install pipx python3-venv && pipx ensurepath`, then the command above.
The tool audits the config of the Claude Code running *inside WSL* (`~/.claude`). A Claude Code installed natively on
Windows has its own config under your Windows profile; this tool does not touch it. Keep projects in the WSL
filesystem; `/mnt/c/...` works but is slower (the tool warns).

## Static audit

```bash
tokens-doctor                      # = tokens-doctor audit: changes nothing, exit 1 if over a target
tokens-doctor audit --use-context  # also runs `claude -p /context` (a tiny real Claude Code run) for the real numbers
tokens-doctor audit --format md --report-dir ./reports --redact
tokens-doctor fix                  # asks per change; --yes applies only the flagged ones
tokens-doctor fix --slim-skills    # sets every skill over 50 tokens to name-only, no questions (reads /context first)
tokens-doctor restore              # list backups;  tokens-doctor restore <id>  undoes a fix
```

Example (a test setup with a 20k window):

```
Component             Tokens  % window  Target
MCP tools             8,000   40.0%     <5%     WARN
Memory files          5,675   28.4%     <3%     WARN
...
2 change(s) applied, undo with: tokens-doctor restore 20261010-141818
  Skills: 111 -> 6 tokens (OK)
  Memory files: 5,675 -> 56 tokens (OK)
```

Targets (share of the window): MCP < 5%, skills < 2%, memory < 3%, total < 15%. What `fix` can do: remove an MCP server
or move it into the project's `.mcp.json`, write a `minimal.mcp.json` (+ the `--strict-mcp-config` launch hint), replace a
big `@import` with an on-demand pointer (diff shown), quarantine or relocate skills, quarantine agents and commands,
disable plugins (`enabledPlugins`), add `permissions.deny` entries for built-in tools you never use.

**Skills.** The folder scan only sees your own and plugin skills; Claude Code also loads built-in and claude.ai-synced
ones (about 6k tokens on the author's setup, 36 skills). `fix --use-context` lists all of them from `/context` and
offers `skillOverrides` in `settings.json`: `name-only` (Claude still knows the skill exists, the description leaves
the context) or `off`. Both were measured to remove the skill's cost from `/context`. `--slim-skills` does it for all of them;
`restore` undoes it.

**claude.ai connectors** (Gmail, Drive, Calendar, Notion, Docs...) have no config file, but `permissions.deny` with
`mcp__claude_ai_<Name>` removes a connector's tools from Claude Code only (your account is untouched). `fix --use-context`
offers it per connector, so you can keep the ones you use. The `settings.json` keys `disabledMcpServers` and
`deniedMcpServers` did not work in testing. Note that these tools are *deferred* (loaded on demand), so denying them
shrinks the tool search space and the ~125k of deferred schemas, not the fresh-session total. `--use-context` runs
`/context` up to 3 times and keeps the fullest result, because connectors and synced skills load asynchronously and a
single run can miss them.

**Safety:** dry-run by default; every modified file is copied to `~/.local/state/tokens-doctor/backup/<id>/`
(`~/Library/Application Support/tokens-doctor/state` on macOS) with a manifest; nothing is deleted, only moved to
`quarantine/<id>/`; JSON edits keep the file's line endings and indent and are re-parsed after writing; env values and
auth headers are masked in reports; `--redact` strips your user name and home path so output can be pasted in an issue.

## Session monitor

```bash
tokens-doctor install-hooks        # merges Stop, UserPromptSubmit, SessionStart, SessionEnd, PreCompact into ~/.claude/settings.json
tokens-doctor uninstall-hooks      # removes only its own entries
tokens-doctor notify-test          # checks the notification + sound setup
tokens-doctor check --latest       # check one session now (--session ID, --task-done, --notify)
```

Restart running sessions after installing; hooks may only apply to new ones. Hooks exit 0 in about 50 ms, print nothing
to stdout and log errors to `monitor.log` in the state folder.

| Event | What happens |
|---|---|
| `Stop` | start a detached watcher (replacing the old one); it first runs a **level check** (notifies only when the recommendation changes level, with a cooldown), then sleeps `idle_minutes` |
| `UserPromptSubmit` | cancels the watcher |
| watcher wakes | if nothing new happened in the transcript: decide with idle context, **one** notification per idle period |
| `PreCompact` / `SessionStart` | counts compactions / records the tmux pane |
| `SessionEnd` | advice for *next* time ("compact before leaving", "start fresh with /clear"), handoff skeleton, state cleaned |

**Decision rule** (`decide.py`, defaults in `config.py`, all overridable): `ratio = messages / (window - fixed overhead)`.
Start at T = 70% and subtract: 10 (>50 messages) or 5 (20-50), 5 (an earlier compaction), 5 (tool output > half of messages),
5 (peak hours), 10 (task finished); idle 5+ minutes or leaving subtracts another 15. These are percentage points of T, not minutes. T never goes below 40; from 80% compact immediately.
Under 20k tokens of messages: nothing. 2+ compactions, a finished task, or over 60 idle minutes: `/clear` instead (a
handoff skeleton is saved). If a fixed component is over its target the advice includes "trim the config" (`fix-config`).
The modifier sizes marked as peak/task-finished are this tool's choice; peak hours and "task finished" cannot be read
from a transcript, so they come from `config.json` (`peak_hours`) and `check --task-done`.

**Notifications:** macOS `osascript` + `afplay`; Linux `notify-send` + `canberra-gtk-play`/`paplay`; WSL a Windows toast
through `wsl-notify-send.exe` or `powershell.exe`, then the Linux tools. Missing tools fall back to the log, and
`notify-test` says how to get the full version.

**Apply mode (experimental, opt-in):** `"mode": "apply"` makes the watcher type `/compact <note>` into the session's tmux
pane, only if: the pane still has the recorded id, its foreground process is `claude`, the session has been idle, and the
pane had no input in the last 5 seconds. `/clear` is sent only with `"allow_clear": true` *and* a saved handoff. Any failed
check falls back to a notification that says why. Claude Code does not pass `TMUX_PANE` to hooks, so the pane is read from
its `~/.claude/sessions/*.json` entry; sessions started with `claude attach` are not found.

**Privacy:** the monitor reads token counts and structure from the transcript, never stores or prints message text
and makes no network calls. The one exception is the handoff skeleton written on `/clear` advice: it holds the transcript
path, the files touched, an error count and your **last request** (first 500 characters), in a local file under
`handoff/` in the state folder, so the next session can pick up. State files and the log never contain message text.

### config.json

`$XDG_CONFIG_HOME/tokens-doctor/config.json` (`~/.config/...`) or `~/Library/Application Support/tokens-doctor/config.json`.
Keys: `window` (`"auto"` by default: 200k, or 1m when `settings.json` picks a `[1m]` model or a session already used more than 200k;
a number overrides; `audit --use-context` reads the real one), `idle_minutes`,
`mode`, `allow_clear`, `peak_hours` (`[14, 18]`), `quiet_hours` (`[22, 7]`), `sound`, `cooldown_minutes`,
`max_watch_minutes`, `pane_commands`, `compact_note`, `mcp_tool_counts` (`{"server": 12}`), and `thresholds` (every number above).

## How accurate are the estimates?

Estimates are characters / 4 over the files Claude Code reads. Measured against `claude -p /context` on one real setup
(Claude Code 2.1.296): `CLAUDE.md` memory 173 estimated vs 254 real (-32%); skills 91 vs 6,000, because most skills come
from plugins and built-ins that the folder scan does not see; MCP tools 0 vs 622 loaded plus ~127k deferred, because
claude.ai connectors are not in any config file. So: use the audit to find what *you* control, use `--use-context` for the
truth, and treat the static numbers as a lower bound. MCP servers with an unknown tool count are estimated at 10 tools x 400 tokens;
set `mcp_tool_counts` for better numbers.

## FAQ

- **Native Windows?** Not supported; use WSL (above).
- **Why does it only suggest `/compact`?** Hooks cannot run slash commands. tmux apply mode is the only portable workaround.
- **Does it change the model, plan or billing?** No. Nothing outside Claude Code's config and the chosen project folder.

Tested with Claude Code 2.1.296 on Linux (WSL2) with Python 3.9, 3.12 and 3.13. macOS is covered by CI only.
See [PLAN.md](PLAN.md) for the design and [CONTRIBUTING.md](CONTRIBUTING.md) to hack on it.
