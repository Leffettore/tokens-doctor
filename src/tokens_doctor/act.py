"""Opt-in apply mode: type /compact into the tmux pane of the session. Best effort, every check must pass."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import platform as plat


@dataclass
class Applied:
    ok: bool
    reason: str


def find_pane(session_id: str, claude_dir: Path) -> str:
    """Hooks do not get TMUX_PANE from Claude Code, so also look at the live session entries it writes.
    shortcut: resumed/attached sessions (`claude attach <id>`) are not found; add a /proc scan if that is needed."""
    if os.environ.get("TMUX_PANE"):
        return os.environ["TMUX_PANE"]
    try:
        for f in (claude_dir / "sessions").glob("*.json"):
            j = json.loads(f.read_text(encoding="utf-8"))
            if j.get("sessionId") == session_id and j.get("tmux") and plat.process_alive(int(j.get("pid", 0))):
                return str(j["tmux"]).split(".")[-1]
    except (OSError, ValueError, TypeError):
        pass
    return ""


def try_apply(action: str, pane: str, cfg: dict, *, last_ts: Optional[float], now: float, handoff_saved: bool,
              allow_clear: bool, run: Callable = plat.run) -> Applied:
    if action not in ("compact", "clear"):
        return Applied(False, "nothing to apply")
    if action == "clear" and not (allow_clear and handoff_saved):
        return Applied(False, "/clear needs --allow-clear and a saved handoff")
    if not pane:
        return Applied(False, "session is not in a known tmux pane")
    if last_ts is None or now - last_ts < cfg["idle_minutes"] * 60 - 1:
        return Applied(False, "session was active too recently")
    r = run(["tmux", "display-message", "-p", "-t", pane, "#{pane_id}|#{pane_current_command}|#{window_activity}"])
    if not r or r.returncode != 0:
        return Applied(False, "tmux not available or pane gone")
    try:
        pane_id, cmd, activity = r.stdout.strip().split("|")
    except ValueError:
        return Applied(False, "unexpected tmux output")
    if pane_id != pane:
        return Applied(False, "pane id no longer matches")
    if cmd not in cfg["pane_commands"]:
        return Applied(False, f"foreground process is {cmd!r}, not Claude Code")
    if activity.isdigit() and now - int(activity) < cfg["typing_quiet_seconds"]:
        return Applied(False, "the pane had input or output in the last seconds")
    text = f"/{action}" + (f" {cfg['compact_note']}" if action == "compact" and cfg.get("compact_note") else "")
    sent = run(["tmux", "send-keys", "-t", pane, "-l", text]) and run(["tmux", "send-keys", "-t", pane, "Enter"])
    return Applied(bool(sent and sent.returncode == 0), "sent" if sent else "tmux send-keys failed")


def now() -> float:
    return time.time()
