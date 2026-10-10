# tokens-doctor

Idle-time cache warning for [Claude Code](https://claude.com/claude-code). Prompt caching lasts about 5 minutes, so a
long pause makes the next message re-read the whole context at full price. After 5 idle minutes this plugin sends a
desktop notification with the context size, so you can `/compact` or `/clear` before you continue.

## How it works

- **Stop hook** starts a detached timer for the session (a new one replaces the old one).
- **UserPromptSubmit hook** cancels it.
- **SessionEnd hook** cancels it and removes the session's state file.
- **When the timer fires** it reads the transcript. If the context is at least `CLAUDE_MIN_TOKENS` (default 50k) it
  notifies: "Cache expired: your next message re-reads 120k tokens." Below that a cold pass is cheap, so it stays silent.
- Every timer start and firing is logged to `~/.claude/idle.log`.

No tmux, no threshold formula, nothing typed into your terminal: it only tells you.

## Install

Requires `node`. On Linux the notification uses `notify-send` and `canberra-gtk-play` (or `paplay`).

```bash
claude plugin marketplace add Leffettore/tokens-doctor
claude plugin install tokens-doctor@tokens-doctor
```

Restart running Claude Code sessions. Remove with `claude plugin uninstall tokens-doctor`.

## Configuration

Environment variables, e.g. in the `env` block of `~/.claude/settings.json`:

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_IDLE_MS` | `300000` | idle delay in ms |
| `CLAUDE_MIN_TOKENS` | `50000` | context size below which nothing is shown |

Quick check: set `CLAUDE_IDLE_MS` to `15000`, restart the session, send a prompt, wait 15 seconds and run
`tail ~/.claude/idle.log`.

## Testing

```bash
npm test
```

## Limits

- macOS and Windows notification branches are untested.
- State files of sessions that crashed without a SessionEnd stay in `~/.claude` (a few bytes each).
- The context size is the last reply's usage; right after a `/compact` it reads 0 until the next reply.
