"""MCP servers: collect them from the config files and estimate what their tools cost."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import Context

TOKENS_PER_TOOL = 400  # rough average of a tool definition; use `--use-context` for real numbers
DEFAULT_TOOLS = 10  # assumed when the tool count is unknown


@dataclass
class Server:
    name: str
    scope: str  # user | local | project
    file: Path
    tools: Optional[int]
    tokens: int
    spec: dict = field(default_factory=dict)


def read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def mask(spec: dict) -> dict:
    """Never show env values or auth headers in reports."""
    out = dict(spec)
    for k in ("env", "headers"):
        if isinstance(out.get(k), dict):
            out[k] = {key: "***" for key in out[k]}
    return out


def collect(ctx: Context) -> list:
    found = []

    def add(servers: Any, scope: str, file: Path) -> None:
        for name, spec in (servers or {}).items():
            n = ctx.cfg["mcp_tool_counts"].get(name)
            found.append(Server(name, scope, file, n, (n or DEFAULT_TOOLS) * TOKENS_PER_TOOL,
                                spec if isinstance(spec, dict) else {}))

    data = read_json(ctx.claude_json) or {}
    if ctx.has("user"):
        add(data.get("mcpServers"), "user", ctx.claude_json)
    if ctx.has("project"):
        add(((data.get("projects") or {}).get(str(ctx.project)) or {}).get("mcpServers"), "local", ctx.claude_json)
        mcp_json = ctx.project / ".mcp.json"
        add((read_json(mcp_json) or {}).get("mcpServers"), "project", mcp_json)
    return found


def audit(ctx: Context) -> dict:
    servers = collect(ctx)
    limit = ctx.th["mcp_tools"]
    findings = [f"server {s.name}: {s.tools} tools (limit {limit})" for s in servers if s.tools and s.tools > limit]
    return {"tokens": sum(s.tokens for s in servers), "servers": servers, "findings": findings}


def remove_server(run, server: Server, ctx: Context) -> None:
    """Delete one server entry from its config file (backed up first)."""
    data = read_json(server.file) or {}
    if server.scope == "local":
        holder = data["projects"][str(ctx.project)]["mcpServers"]
    else:
        holder = data["mcpServers"]
    holder.pop(server.name, None)
    run.write_json(server.file, data)


def move_to_project(run, server: Server, ctx: Context) -> None:
    """Move a user-scope server into the project's .mcp.json."""
    target = ctx.project / ".mcp.json"
    proj = read_json(target) or {}
    proj.setdefault("mcpServers", {})[server.name] = server.spec
    run.write_json(target, proj)
    remove_server(run, server, ctx)


def write_minimal(run, essential: list, ctx: Context, out: Path) -> None:
    run.write_json(out, {"mcpServers": {s.name: s.spec for s in essential}})
