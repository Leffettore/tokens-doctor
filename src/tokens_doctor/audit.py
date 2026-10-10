"""Orchestrates the static phases: preflight, audit, summary rows and the list of possible fixes."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import builtin_tools, extensions, mcp, memory, skills
from . import platform as plat
from .config import Context

PHASES = ("mcp", "memory", "skills", "extensions", "builtin")
_MODULES = {"mcp": mcp, "memory": memory, "skills": skills, "extensions": extensions, "builtin": builtin_tools}
# component -> (config key of its target, phases summed into it)
COMPONENTS = {"MCP tools": ("mcp", ("mcp",)), "Skills": ("skills", ("skills",)), "Memory files": ("memory", ("memory",))}


@dataclass
class Action:
    phase: str
    text: str
    apply: Callable
    diff: str = ""
    flagged: bool = False  # triggered by a finding: the only ones `--yes` applies
    alt: Optional[tuple] = None  # (key, label, fn): a second choice at the prompt
    tokens: int = 0
    extra: dict = field(default_factory=dict)


def preflight(ctx: Context) -> dict:
    claude = plat.find_claude()
    version, flags = "", set()
    if claude:
        r = plat.run([claude, "--version"])
        version = r.stdout.strip() if r else ""
        h = plat.run([claude, "--help"])
        flags = {f for f in ("--strict-mcp-config", "--mcp-config") if h and f in h.stdout}
    notices = []
    if plat.is_wsl():
        notices.append(f"WSL detected: auditing the Claude Code config at {ctx.claude_dir} (a Claude Code installed "
                       "natively on Windows is a separate setup and is not touched)")
    if plat.on_windows_drive(ctx.project):
        notices.append("project is on the Windows drive (/mnt/...): slower, odd permissions; keep projects in WSL")
    return {
        "os": plat.os_name(), "python": sys.version.split()[0], "claude": claude, "claude_version": version,
        "supports": sorted(flags), "claude_dir": str(ctx.claude_dir), "claude_dir_exists": ctx.claude_dir.is_dir(),
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


def plan_actions(ctx: Context, results: dict, run_ref: Callable) -> list:
    """Every possible change. `run_ref()` returns the Run used when an action is applied."""
    actions = []
    if "mcp" in results:
        for s in sorted(results["mcp"]["servers"], key=lambda s: -s.tokens):
            flagged = bool(s.tools and s.tools > ctx.th["mcp_tools"])
            a = Action("mcp", f"Remove MCP server '{s.name}' ({s.scope}, ~{s.tokens} tokens est.)",
                       lambda s=s: mcp.remove_server(run_ref(), s, ctx), flagged=flagged, tokens=s.tokens)
            if s.scope in ("user", "local") and ctx.has("project"):
                a.alt = ("p", "move to this project's .mcp.json", lambda s=s: mcp.move_to_project(run_ref(), s, ctx))
            actions.append(a)
    if "memory" in results:
        for path, old, new, why in memory.propose(ctx, results["memory"]):
            diff = f"--- {path}\n+++ {path}\n- {old}\n+ {new}"
            actions.append(Action("memory", f"Replace a large import in {path}: {why}",
                                  lambda p=path, o=old, n=new: _edit_line(run_ref(), p, o, n), diff=diff, flagged=True))
    if "skills" in results:
        for s in sorted(results["skills"]["skills"], key=lambda s: -s.tokens)[:10]:
            flagged = len(s.description) > ctx.th["skill_description_chars"]
            a = Action("skills", f"Quarantine skill '{s.name}' ({s.scope}, ~{s.tokens} tokens)",
                       lambda s=s: run_ref().quarantine(s.path), flagged=flagged, tokens=s.tokens)
            if s.scope == "user" and ctx.has("project"):
                a.alt = ("p", "move into this project's .claude/skills", lambda s=s: _move_skill(run_ref(), s, ctx))
            actions.append(a)
    if "extensions" in results:
        for i in sorted(results["extensions"]["items"], key=lambda i: -i.tokens):
            if i.kind == "plugin" and i.enabled:
                actions.append(Action("extensions", f"Disable plugin '{i.name}' (~{i.tokens} tokens)",
                                      lambda i=i: extensions.disable_plugin(run_ref(), ctx, i.name), tokens=i.tokens))
            elif i.kind in ("agent", "command"):
                actions.append(Action("extensions", f"Quarantine {i.kind} '{i.name}' ({i.scope}, ~{i.tokens} tokens)",
                                      lambda i=i: run_ref().quarantine(i.path), tokens=i.tokens))
    if "builtin" in results:
        for tool, tokens in results["builtin"]["suggestions"].items():
            actions.append(Action("builtin", f"Add '{tool}' to permissions.deny (only if you never use it)",
                                  lambda t=tool: builtin_tools.deny(run_ref(), ctx, t), tokens=tokens))
    return actions


def _edit_line(run, path: Path, old: str, new: str) -> None:
    text = path.read_bytes().decode("utf-8", errors="replace")  # old/new hold no line ending, so CRLF survives
    if old not in text:
        raise RuntimeError(f"{path} changed since the audit, nothing edited")
    run.write_bytes(path, text.replace(old, new, 1).encode("utf-8"))


def _move_skill(run, skill, ctx: Context) -> None:
    import shutil
    dest = ctx.project / ".claude" / "skills" / skill.path.name
    if dest.exists():
        raise RuntimeError(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill.path, dest)
    run.actions.append({"op": "create", "path": str(dest.resolve())})
    run.quarantine(skill.path)


def minimal_mcp(ctx: Context, results: dict, names: list, out: Path, run) -> list:
    chosen = [s for s in results["mcp"]["servers"] if s.name in names]
    mcp.write_minimal(run, chosen, ctx, out)
    return chosen
