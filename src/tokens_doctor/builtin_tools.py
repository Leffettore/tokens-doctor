"""Built-in tools: suggest permissions.deny entries for tools you never use (they leave the context)."""
from __future__ import annotations

import json

from .config import Context

# Rarely used built-ins. Denying one removes its definition from the context; the user confirms each.
CANDIDATES = {"NotebookEdit": 300, "WebFetch": 300, "WebSearch": 300}


def _settings(ctx: Context) -> list:
    files = [ctx.claude_dir / "settings.json", ctx.claude_dir / "settings.local.json"] if ctx.has("user") else []
    if ctx.has("project"):
        files += [ctx.project / ".claude" / "settings.json", ctx.project / ".claude" / "settings.local.json"]
    return files


def denied(ctx: Context) -> set:
    out = set()
    for f in _settings(ctx):
        try:
            out |= set(json.loads(f.read_text(encoding="utf-8")).get("permissions", {}).get("deny", []))
        except (OSError, ValueError, AttributeError):
            pass
    return out


def audit(ctx: Context) -> dict:
    d = denied(ctx)
    suggestions = {k: v for k, v in CANDIDATES.items() if k not in d}
    return {"tokens": 0, "suggestions": suggestions, "findings": []}
