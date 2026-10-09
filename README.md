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
git clone https://github.com/Leffettore/tokens-doctor.git
cd tokens-doctor
./install.sh              # copies idle-timer.js to ~/.claude and registers the 3 hooks
./install.sh --uninstall  # removes both
```

The installer keeps your existing hooks, is safe to re-run, and saves your original `settings.json` as
`settings.json.bak`. Restart running Claude Code sessions afterwards, and run `claude` inside tmux.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_IDLE_MS` | `300000` | idle delay in ms |
| `CLAUDE_WINDOW` | `200000` | context window in tokens (set it for 1M models) |
| `CLAUDE_IDLE_COMPACT` | on | `0` = never type `/compact`, alarm/log only |

## Testing

```bash
node idle-timer.js --selftest          # decision rules
tail -f ~/.claude/idle.log             # watch a real session
```

## Limits

- Linux only (reads `/proc` to find the tmux pane of resumed sessions). The alarm has macOS/Windows branches, untested,
  and `/compact` needs tmux.
- The fixed-overhead estimate comes from the first reply, so a very large first prompt makes compaction rarer.
- A session closed more than 7 days after its last turn skips the exit alarm (stale state files are deleted after a week).
- `/compact` is typed into the pane: if you are typing in that pane at that moment the text is mixed.
