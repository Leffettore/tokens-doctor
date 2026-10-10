"""Defaults, the user config.json, and the per-run context shared by every phase."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import platform as plat

DEFAULTS: dict[str, Any] = {
    "window": 200000,
    "idle_minutes": 5,
    "mode": "suggest",  # suggest | apply
    "allow_clear": False,
    "peak_hours": None,  # e.g. [14, 18]: local hours counted as peak
    "quiet_hours": None,  # e.g. [22, 7]: no notifications
    "sound": True,
    "cooldown_minutes": 15,
    "max_watch_minutes": 120,
    "pane_commands": ["claude"],
    "typing_quiet_seconds": 5,
    "compact_note": "Preserve the current task, files touched and open errors.",
    "thresholds": {
        "base": 70, "floor": 40, "ceiling": 80,
        "away": 15, "msgs_50": 10, "msgs_20": 5, "compaction": 5, "tool_heavy": 5, "peak": 5, "task_done": 10,
        "min_messages_tokens": 20000, "clear_idle_minutes": 60, "clear_after_compactions": 2,
        # component targets, percent of the window
        "mcp": 5, "skills": 2, "memory": 3, "total": 15,
        "mcp_tools": 15, "skill_description_chars": 300, "memory_tokens": 2000, "import_tokens": 1500,
    },
    "mcp_tool_counts": {},  # server name -> tool count, when you know it
}


def load(path: Optional[Path] = None) -> dict[str, Any]:
    """DEFAULTS overlaid with config.json; a missing or broken file silently means defaults."""
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        user = json.loads((path or plat.config_dir() / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if isinstance(user, dict):
        th = user.pop("thresholds", None)
        cfg.update(user)
        if isinstance(th, dict):
            cfg["thresholds"].update(th)
    return cfg


@dataclass
class Context:
    claude_dir: Path
    claude_json: Path
    project: Path
    scopes: tuple = ("user", "project")
    window: int = 200000
    cfg: dict = field(default_factory=lambda: load())

    @property
    def th(self) -> dict:
        return self.cfg["thresholds"]

    def has(self, scope: str) -> bool:
        return scope in self.scopes
