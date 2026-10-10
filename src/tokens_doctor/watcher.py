"""The idle timer: one detached process per session, started by the Stop hook."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from . import act, monitor, state


class Clock:
    def now(self) -> float:
        return time.time()

    def sleep(self, s: float) -> None:
        time.sleep(s)


def _cancelled(st: state.State, token) -> bool:
    s = st.get()
    return not s or s.get("cancel") != token or s.get("pid") != os.getpid()


def watch(session_id: str, idle_minutes: float, cfg: dict, claude_dir: Path, clock: Optional[Clock] = None,
          pid: Optional[int] = None, poll: float = 2.0) -> str:
    """Returns what happened: cancelled | active | notified | applied | skipped | gone."""
    clock = clock or Clock()
    pid = pid if pid is not None else os.getpid()
    st = state.State(session_id)
    s = st.get()
    path = Path(s["transcript"]) if s.get("transcript") else None
    if not path or not path.exists():
        return "gone"
    token = s.get("cancel")
    try:
        monitor.level_check(cfg, claude_dir, st, path, clock.now())  # workflow 1, off the hook's critical path
    except Exception as e:  # noqa: BLE001
        state.log(f"[{session_id[:8]}] level check failed: {e}")
    last_ts0 = monitor.read_session(path).last_ts
    wait = min(idle_minutes, cfg["max_watch_minutes"]) * 60
    end = clock.now() + wait
    while clock.now() < end:
        clock.sleep(min(poll, max(0.0, end - clock.now())))
        s = st.get()
        if not s or s.get("cancel") != token or s.get("pid") != pid:
            return "cancelled"
    now = clock.now()
    stats, d, idle = monitor.evaluate(cfg, claude_dir, path, st=st, now=now)
    if stats.last_ts != last_ts0:
        return "active"  # something happened in the session meanwhile
    if s.get("idle_notified_for") == last_ts0:
        return "skipped"  # one notification per idle period
    st.update(idle_notified_for=last_ts0)
    if d.action == "none":
        return "skipped"
    if cfg["mode"] == "apply":
        saved = monitor.write_handoff(stats) if d.action == "clear" else None
        res = act.try_apply(d.action, s.get("pane", ""), cfg, last_ts=stats.last_ts, now=now,
                            handoff_saved=bool(saved), allow_clear=cfg["allow_clear"])
        state.log(f"[{session_id[:8]}] apply {d.action}: {res.reason}")
        if res.ok:
            return "applied"
        monitor.emit(cfg, st, stats, d, idle, now, extra=f" (auto-apply skipped: {res.reason})")
        return "notified"
    monitor.emit(cfg, st, stats, d, idle, now)
    return "notified"


def exit_check(session_id: str, cfg: dict, claude_dir: Path, now: Optional[float] = None) -> str:
    """SessionEnd: compacting is no longer possible, so advise for next time, then clean up."""
    st = state.State(session_id)
    s = st.get()
    path = Path(s["transcript"]) if s.get("transcript") else None
    try:
        if not path or not path.exists():
            return "gone"
        now = now if now is not None else time.time()
        stats, d, idle = monitor.evaluate(cfg, claude_dir, path, st=st, leaving=True, now=now)
        if d.action == "clear":
            monitor.write_handoff(stats)
        if d.action != "none":
            d.reasons = [r.replace("compact now", "compact earlier next time") for r in d.reasons]
            monitor.emit(cfg, st, stats, d, 0, now, extra=" Next time: " + ("/compact before leaving."
                         if d.action == "compact" else "start fresh with /clear." if d.action == "clear" else "run audit."))
        return d.action
    finally:
        st.delete()
