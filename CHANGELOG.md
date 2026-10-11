# Changelog

## Unreleased
- Python rewrite following PLAN.md: static `audit` (read-only) for MCP, memory, skills, plugins, agents, commands and built-in tools.
  There is no `fix` / `restore`: trim your config by hand.
- Session monitor: `check`, hooks (`install-hooks`), idle watcher, `SessionEnd` advice, desktop notifications, optional tmux apply mode.
- Replaces the earlier Node plugin; run `claude plugin uninstall tokens-doctor` if you had it.
