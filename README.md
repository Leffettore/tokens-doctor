# tokens-doctor

Idle-time context doctor for [Claude Code](https://claude.com/claude-code). Prompt caching lasts about 5 minutes, so a
long pause makes the next message re-read the whole context at full price. This tool watches each session and, after
5 idle minutes, either types `/compact` for you (while the cache is still warm) or sounds an alarm telling you to
`/clear`.

## What it does

- **Stop hook** starts a detached 5-minute timer per session (a new one replaces the old one).
- **UserPromptSubmit hook** cancels it.
- **When the timer expires** it reads the session transcript and decides:
  - `compact`: types `/compact` into the session's tmux pane (silent).
  - `alarm`: sound + desktop notification ("consider /clear").
  - `skip`: nothing.
- **SessionEnd hook** (`/exit`): alarms if the context should have been compacted before you left.
- Everything is logged to `~/.claude/idle.log`, tagged with the session name.

Sessions outside tmux are ignored (no timer, no alarm): the tool needs tmux to type `/compact` for you.

## Decision rule

`messages` = context tokens minus the fixed overhead (system prompt, tools, skills, memory; measured from the first reply
of the session), as a percentage of `window - overhead`. Compact when it reaches T:

```
T = 70
  - 15   away 5+ minutes on the same task
  - 10   more than 50 messages  (or - 5 for 20-50)
  - 5    one earlier compaction
  - 5    tool output is more than half of the messages
T never goes below 40
```

- Fewer than 20k tokens of messages: skip (a cold pass is cheap).
- 2 or more compactions: alarm only (`/clear` with a handoff note beats a third summary of summaries).

Peak-hour load and "just finished a task" are not modelled; they can't be read from a transcript.

## Install (Ubuntu / Linux)

Requires `node` and `tmux`. For the alarm: `notify-send` and `canberra-gtk-play` (or `paplay`), usually already present on
desktop Ubuntu.

```bash
claude plugin marketplace add Leffettore/tokens-doctor
claude plugin install tokens-doctor@tokens-doctor
# or, inside Claude Code (2.1.275+): /plugin install tokens-doctor --marketplace Leffettore/tokens-doctor
```

Restart running Claude Code sessions, and run `claude` inside tmux. Remove with `claude plugin uninstall tokens-doctor`.

## Configuration (environment variables)

Set them in the `env` block of `~/.claude/settings.json` (applied to every session; not tested through the hooks'
daemon yet), e.g. `{ "env": { "CLAUDE_IDLE_MS": "60000", "CLAUDE_WINDOW": "1000000" } }`.

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_IDLE_MS` | `300000` | idle delay in ms |
| `CLAUDE_WINDOW` | `200000` | context window in tokens (set it for 1M models) |
| `CLAUDE_IDLE_COMPACT` | on | `0` = never type `/compact`, alarm/log only |

## Shortcuts

Add to `~/.bashrc` or `~/.zshrc`:

```bash
# start Claude Code inside a tmux session named after the folder (re-attaches if it already exists)
cc() { tmux new-session -A -s "cc-$(basename "$PWD")" claude "$@"; }

alias idlelog='tail -f ~/.claude/idle.log'                    # watch decisions live
alias idlelast='grep -E "decision|sent /compact" ~/.claude/idle.log | tail -10'
alias idletimers='pgrep -af "idle-timer.js --wait"'           # running timers
alias idlestop='pkill -f "idle-timer.js --wait"'              # cancel all timers
```

Reading a log line:

```
decision: compact (56% messages, T 55%)          messages / usable window vs the threshold
idle 300000ms elapsed, compact [My session]      what was done, for which session name
sent /compact to tmux pane %3                    where it was typed
```

Quick manual check without waiting 5 minutes: set `"CLAUDE_IDLE_MS": "15000"` in the `env` block of
`~/.claude/settings.json`, restart the session, send a prompt, wait 15 seconds, run `idlelast`. Remove it afterwards.

## Testing

```bash
npm test                               # decision rules + transcript parsing
tail -f ~/.claude/idle.log             # watch a real session
```

## Limits

- Linux only (reads `/proc` to find the tmux pane of resumed sessions). The alarm has macOS/Windows branches, untested,
  and `/compact` needs tmux.
- The fixed-overhead estimate comes from the first reply, so a very large first prompt makes compaction rarer.
- A session closed more than 7 days after its last turn skips the exit alarm (stale state files are deleted after a week).
- `/compact` is typed into the pane: if you are typing in that pane at that moment the text is mixed.
