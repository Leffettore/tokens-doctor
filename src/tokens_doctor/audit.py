"""Orchestrates the static phases: preflight, audit and summary rows."""
from __future__ import annotations

import sys
from . import builtin_tools, extensions, mcp, memory, skills
from . import platform as plat
from .config import Context

PHASES = ("mcp", "memory", "skills", "extensions", "builtin")
_MODULES = {"mcp": mcp, "memory": memory, "skills": skills, "extensions": extensions, "builtin": builtin_tools}
# component -> (config key of its target, phases summed into it)
COMPONENTS = {"MCP tools": ("mcp", ("mcp",)), "Skills": ("skills", ("skills",)), "Memory files": ("memory", ("memory",))}


def preflight(ctx: Context) -> dict:
    claude = plat.find_claude()
    version = ""
    if claude:
        r = plat.run([claude, "--version"])
        version = r.stdout.strip() if r else ""
    notices = []
    if plat.is_wsl():
        notices.append(f"WSL detected: auditing the Claude Code config at {ctx.claude_dir} (a Claude Code installed "
                       "natively on Windows is a separate setup and is not touched)")
    if plat.on_windows_drive(ctx.project):
        notices.append("project is on the Windows drive (/mnt/...): slower, odd permissions; keep projects in WSL")
    return {
        "os": plat.os_name(), "python": sys.version.split()[0], "claude": claude, "claude_version": version,
        "claude_dir": str(ctx.claude_dir), "claude_dir_exists": ctx.claude_dir.is_dir(),
        "project": str(ctx.project), "project_exists": ctx.project.is_dir(), "notices": notices,
    }


def run_audit(ctx: Context, only=PHASES) -> dict:
    return {p: _MODULES[p].audit(ctx) for p in PHASES if p in only}


def summarize(ctx: Context, results: dict) -> dict:
    """Rows of component / tokens / % of window / target / status, plus the total."""
    rows = []
    for name, (key, phases) in COMPONENTS.items():
        if not all(p in results for p in phases):
            continue
        tokens = sum(results[p]["tokens"] for p in phases)
        rows.append(_row(ctx, name, tokens, ctx.th[key]))
    measured = sum(r["tokens"] for r in rows) + results.get("extensions", {}).get("tokens", 0)
    rows.append(_row(ctx, "Total fixed overhead", measured, ctx.th["total"]))
    return {"rows": rows, "over": [r["component"] for r in rows if r["status"] == "WARN"]}


def _row(ctx: Context, name: str, tokens: int, target_pct: float) -> dict:
    pct = round(100 * tokens / ctx.window, 1)
    return {"component": name, "tokens": tokens, "pct": pct, "target_pct": target_pct,
            "status": "WARN" if pct > target_pct else "OK"}


def component_pcts(ctx: Context, results: dict) -> dict:
    return {r["component"]: r for r in summarize(ctx, results)["rows"]}
