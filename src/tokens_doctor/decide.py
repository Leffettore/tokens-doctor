"""The decision engine: a pure function from numbers to none | compact | clear | fix-config."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Facts:
    window: int
    overhead: int
    tokens: int
    msgs: int = 0
    compactions: int = 0
    tool_share: float = 0.0
    idle_minutes: float = 0.0
    peak: bool = False
    task_done: bool = False
    leaving: bool = False  # the session is ending (SessionEnd): advice is for next time
    over_targets: list = field(default_factory=list)  # fixed components above their target


@dataclass
class Decision:
    action: str  # none | compact | clear | fix-config
    reasons: list
    T: int
    ratio: float  # messages / usable window, 0..1
    urgent: bool = False  # over the ceiling: apply mode may compact without waiting for idle


def threshold(f: Facts, th: dict) -> tuple:
    """T = base - modifiers (floored); the away modifier is added on top when idle or leaving."""
    t, why = th["base"], []
    if f.msgs > 50:
        t -= th["msgs_50"]
        why.append(f"{f.msgs} messages")
    elif f.msgs >= 20:
        t -= th["msgs_20"]
        why.append(f"{f.msgs} messages")
    if f.compactions:
        t -= th["compaction"]
        why.append(f"{f.compactions} earlier compaction(s)")
    if f.tool_share > 0.5:
        t -= th["tool_heavy"]
        why.append(f"tool output is {round(100 * f.tool_share)}% of messages")
    if f.peak:
        t -= th["peak"]
        why.append("peak hours")
    if f.task_done:
        t -= th["task_done"]
        why.append("task finished")
    t = max(t, th["floor"])
    away = f.leaving or f.idle_minutes >= th["away_minutes"]
    if away:
        t = max(t - th["away"], th["floor"])
        why.append("away" if f.leaving else f"idle {round(f.idle_minutes)} min")
    return t, why


def decide(f: Facts, th: dict) -> Decision:
    usable = max(1, f.window - f.overhead)
    msg_tokens = max(0, f.tokens - f.overhead)
    ratio = msg_tokens / usable
    urgent = False
    t, why = threshold(f, th)
    pct = round(100 * ratio)
    numbers = f"messages {pct}% of usable, T={t}%"
    action, reasons = "none", []
    if msg_tokens < th["min_messages_tokens"]:
        reasons.append(f"only {msg_tokens // 1000}k tokens of messages: a cold pass is cheap")
    elif f.compactions >= th["clear_after_compactions"]:
        action = "clear"
        reasons.append(f"{f.compactions} compactions already: /clear with a handoff beats a summary of summaries")
    elif f.task_done:
        action = "clear"
        reasons.append("task finished: start fresh with /clear")
    elif f.idle_minutes > th["clear_idle_minutes"]:
        action = "clear"
        reasons.append(f"idle {round(f.idle_minutes)} min, the cache is cold: /clear plus a handoff")
    elif ratio * 100 >= th["ceiling"]:
        action, urgent = "compact", True
        reasons.append(f"messages {pct}% of usable, over the {th['ceiling']}% ceiling: compact now")
    elif ratio * 100 >= t:
        action = "compact"
        reasons.append(numbers + (f" ({', '.join(why)})" if why else ""))
    else:
        reasons.append(numbers + ": not worth it yet")
    if f.over_targets:
        reasons.append("fixed overhead over target (" + ", ".join(f.over_targets) + "): compaction cannot reduce it")
        if action == "none":
            action = "fix-config"
    return Decision(action, reasons, t, ratio, urgent)


def level(action: Optional[str]) -> int:
    return {"none": 0, "fix-config": 1, "compact": 2, "clear": 3}.get(action or "none", 0)
