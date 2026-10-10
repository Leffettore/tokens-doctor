"""Agents, custom commands and plugins."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import Context
from .measure import estimate_tokens, read_text
from .skills import parse_frontmatter


@dataclass
class Item:
    kind: str  # agent | command | plugin
    name: str
    scope: str
    path: Path
    tokens: int
    enabled: bool = True


def _md_items(base: Path, kind: str, scope: str) -> list:
    out = []
    if base.is_dir():
        for p in sorted(base.rglob("*.md")):
            fm = parse_frontmatter(read_text(p))
            name = fm.get("name") or p.relative_to(base).with_suffix("").as_posix()
            desc = fm.get("description") or (read_text(p).strip().splitlines() or [""])[0]
            out.append(Item(kind, name, scope, p, estimate_tokens(f"{name}\n{desc}")))
    return out


def _plugin_tokens(install: Path) -> int:
    n = 0
    for sub, kind in (("agents", "agent"), ("commands", "command")):
        n += sum(i.tokens for i in _md_items(install / sub, kind, "plugin"))
    return n


def _plugins(ctx: Context) -> list:
    try:
        installed = json.loads((ctx.claude_dir / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
        enabled = json.loads((ctx.claude_dir / "settings.json").read_text(encoding="utf-8")).get("enabledPlugins", {})
    except (OSError, ValueError):
        installed, enabled = {}, {}
    out = []
    for name, entries in (installed.get("plugins") or {}).items():
        entry = entries[0] if isinstance(entries, list) and entries else {}
        path = Path(entry.get("installPath", "")) if isinstance(entry, dict) else Path("")
        on = bool(enabled.get(name, True))
        out.append(Item("plugin", name, "user", path, _plugin_tokens(path) if path.is_dir() else 0, on))
    return out


def audit(ctx: Context) -> dict:
    items = []
    if ctx.has("user"):
        items += _md_items(ctx.claude_dir / "agents", "agent", "user")
        items += _md_items(ctx.claude_dir / "commands", "command", "user")
        items += _plugins(ctx)
    if ctx.has("project"):
        items += _md_items(ctx.project / ".claude" / "agents", "agent", "project")
        items += _md_items(ctx.project / ".claude" / "commands", "command", "project")
    return {"tokens": sum(i.tokens for i in items if i.enabled), "items": items, "findings": []}


def disable_plugin(run, ctx: Context, name: str) -> None:
    """Disable through the supported config key; other keys in settings.json are kept."""
    path = ctx.claude_dir / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("enabledPlugins", {})[name] = False
    run.write_json(path, data)
