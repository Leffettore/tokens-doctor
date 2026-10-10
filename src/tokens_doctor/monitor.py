"""Glue between the transcript reader, the decision engine, the state and the notifier."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import audit, notify, state
from . import platform as plat
from .config import Context
from .decide import Decision, Facts, decide, level
from .session import SessionStats, read_session

LABELS = {"compact": "Compact", "clear": "Clear", "fix-config": "Trim the fixed overhead"}


def in_hours(rng, when: datetime) -> bool:
    """rng = [start_hour, end_hour], may wrap midnight; None = never."""
    if not rng or len(rng) != 2:
        return False
    s, e, h = rng[0], rng[1], when.hour
    return s <= h < e if s <= e else (h >= s or h < e)


def evaluate(cfg: dict, claude_dir: Path, path: Path, *, st: Optional[state.State] = None, task_done: bool = False,
             leaving: bool = False, now: Optional[float] = None, components: bool = True):
    """Read the session and decide. Returns (stats, decision, idle_minutes)."""
    now = now if now is not None else time.time()
    stats = read_session(path)
    idle = max(0.0, (now - stats.last_ts) / 60) if stats.last_ts else 0.0
    saved = (st.get() if st else {})
    over = []
    if components:
        ctx = Context(claude_dir=claude_dir, claude_json=plat.claude_json(str(claude_dir) if claude_dir != plat.claude_dir() else None),
                      project=Path(stats.cwd or ".").expanduser(), window=cfg["window"], cfg=cfg)
        try:
            over = audit.summarize(ctx, audit.run_audit(ctx))["over"]
        except OSError as e:
            state.log(f"component audit failed: {e}")
    facts = Facts(window=cfg["window"], overhead=stats.overhead, tokens=stats.tokens, msgs=stats.msgs,
                  compactions=max(stats.compactions, saved.get("compactions", 0)), tool_share=stats.tool_share,
                  idle_minutes=idle, peak=in_hours(cfg["peak_hours"], datetime.fromtimestamp(now)),
                  task_done=task_done, leaving=leaving, over_targets=over)
    return stats, decide(facts, cfg["thresholds"]), idle


def folder(stats: SessionStats) -> str:
    return Path(stats.cwd).name if stats.cwd else stats.session_id[:8]


def text(d: Decision, idle: float = 0) -> str:
    head = LABELS.get(d.action, "OK")
    return f"{head}: " + "; ".join(d.reasons)


def gate(cfg: dict, st: state.State, action: str, now: float) -> str:
    """'' when a notification may go out, else why not (quiet hours, or the same advice was just given)."""
    if in_hours(cfg["quiet_hours"], datetime.fromtimestamp(now)):
        return "quiet hours"
    s = st.get()
    if s.get("last_notified_action") == action and now - s.get("last_notified", 0) < cfg["cooldown_minutes"] * 60:
        return "cooldown"
    return ""


def emit(cfg: dict, st: state.State, stats: SessionStats, d: Decision, idle: float, now: float, extra: str = "") -> str:
    """Notify unless gated; returns the backend used or ''."""
    why_not = gate(cfg, st, d.action, now)
    if why_not:
        state.log(f"[{st.id[:8]}] {d.action} not notified: {why_not}")
        return ""
    used = notify.notify(f"Claude Code · {folder(stats)}", text(d, idle) + extra, sound=cfg["sound"],
                         urgent=d.action in ("compact", "clear"))
    st.update(last_notified=now, last_notified_action=d.action)
    state.log(f"[{st.id[:8]}] notified ({used}): {d.action}")
    return used


def level_check(cfg: dict, claude_dir: Path, st: state.State, path: Path, now: float) -> None:
    """Workflow 1 at the end of a turn: notify only when the recommendation changes level."""
    stats, d, idle = evaluate(cfg, claude_dir, path, st=st, now=now)
    previous = st.get().get("last_action", "none")
    st.update(last_action=d.action)
    if level(d.action) > 0 and d.action != previous:
        emit(cfg, st, stats, d, idle, now)


def write_handoff(stats: SessionStats, dest_dir: Optional[Path] = None) -> Path:
    """Facts only, no model call and no message text: where the transcript is and what was touched."""
    out = (dest_dir or state.root() / "handoff") / f"{stats.session_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Handoff skeleton", "", f"- transcript: {stats.path}", f"- project: {stats.cwd}",
             f"- context: {stats.tokens:,} tokens, {stats.compactions} compaction(s), {stats.msgs} prompt(s) since the last",
             f"- tool errors seen: {stats.errors}", "", "## Files touched"] + [f"- {f}" for f in stats.files[-30:]]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
