"""CLI glue for the session monitor commands."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from . import config, hooks, install_hooks, monitor, notify, session, state, watcher
from . import platform as plat
from .backup import FixError


def parse_minutes(s) -> float:
    if s is None:
        return 0.0
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", str(s))
    if not m:
        raise FixError(f"cannot read duration {s!r}, use e.g. 5m, 90s, 1h")
    return float(m.group(1)) * {"s": 1 / 60, "m": 1, "h": 60, "": 1}[m.group(2)]


def cmd_check(a) -> int:
    cfg = config.load()
    if a.window:
        cfg["window"] = a.window  # an explicit number beats auto-detection
    claude_dir = plat.claude_dir(a.config_dir)
    path = session.find_transcript(claude_dir, a.session)
    if not path:
        raise FixError("no session transcript found" + (f" for {a.session}" if a.session else ""))
    st = state.State(path.stem)
    stats, d, idle = monitor.evaluate(cfg, claude_dir, path, st=st, task_done=a.task_done)
    if a.format == "json":
        print(json.dumps({"session": stats.session_id, "action": d.action, "reasons": d.reasons, "T": d.T,
                          "ratio": round(d.ratio, 3), "tokens": stats.tokens, "idle_minutes": round(idle, 1)}))
    else:
        print(f"{stats.session_id[:8]} [{monitor.folder(stats)}] {stats.tokens:,} tokens\n" + monitor.text(d, idle))
    if a.notify and d.action != "none":
        monitor.emit(cfg, st, stats, d, idle, time.time())
    return 0 if d.action == "none" else 1


def cmd_hooks(a) -> int:
    claude_dir = plat.claude_dir(a.config_dir)
    path = install_hooks.settings_path(a.scope, claude_dir, Path(a.project_path).expanduser().resolve())
    if a.cmd == "install-hooks":
        changed = install_hooks.install(path, plat.state_dir())
        print(f"{'installed in' if changed else 'already installed in'} {path}")
        print("Hooks may only apply to new Claude Code sessions: restart running ones.")
    else:
        print(f"{'removed from' if install_hooks.uninstall(path, plat.state_dir()) else 'nothing to remove in'} {path}")
    return 0


def dispatch(a) -> int:
    if a.cmd == "check":
        return cmd_check(a)
    if a.cmd in ("install-hooks", "uninstall-hooks"):
        return cmd_hooks(a)
    if a.cmd == "hook":
        hooks.run_stdin(a.event)
        return 0  # always, and nothing on stdout
    if a.cmd == "watch":
        cfg = config.load()
        claude_dir = plat.claude_dir()
        try:
            if a.exit_check:
                watcher.exit_check(a.session, cfg, claude_dir)
            else:
                state.State(a.session).update(pid=__import__("os").getpid())
                watcher.watch(a.session, parse_minutes(a.idle) or cfg["idle_minutes"], cfg, claude_dir)
        except Exception as e:  # noqa: BLE001
            state.log(f"watcher failed: {type(e).__name__}: {e}")
        return 0
    if a.cmd == "notify-test":
        print(notify.test())
        return 0
    print(f"unknown command {a.cmd}", file=sys.stderr)
    return 2
