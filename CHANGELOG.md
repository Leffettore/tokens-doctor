# Changelog

## Unreleased
- Python rewrite following PLAN.md: static `audit` / `fix` / `restore` for MCP, memory, skills, plugins, agents, commands and
  built-in tools, with backups and quarantine.
- Session monitor: `check`, hooks (`install-hooks`), idle watcher, `SessionEnd` advice, desktop notifications, optional tmux apply mode.
- Replaces the earlier Node plugin; run `claude plugin uninstall tokens-doctor` if you had it.
