"""Console / Markdown / JSON output, with redaction for pasting into issues."""
from __future__ import annotations

import getpass
import json
from pathlib import Path

from . import mcp as mcp_mod

BARS = {"OK": "OK  ", "WARN": "WARN"}


def build(pre: dict, results: dict, summary: dict, window: int, real: dict = None) -> dict:
    d = {"preflight": pre, "window": window, "components": summary["rows"], "over": summary["over"],
         "findings": [f for r in results.values() for f in r.get("findings", [])], "details": {}}
    if "mcp" in results:
        d["details"]["mcp"] = [{"name": s.name, "scope": s.scope, "tools": s.tools, "tokens": s.tokens,
                                "spec": mcp_mod.mask(s.spec)} for s in results["mcp"]["servers"]]
    if "memory" in results:
        d["details"]["memory"] = [{"file": str(f.path), "tokens": f.tokens,
                                   "imports": [{"import": i[0], "tokens": i[2], "note": i[3]} for i in f.imports]}
                                  for f in results["memory"]["files"]]
        d["findings"] += results["memory"]["warnings"]
    if "skills" in results:
        d["details"]["skills"] = [{"name": s.name, "scope": s.scope, "tokens": s.tokens,
                                   "description_chars": len(s.description)} for s in results["skills"]["skills"]]
    if "extensions" in results:
        d["details"]["extensions"] = [{"kind": i.kind, "name": i.name, "scope": i.scope, "tokens": i.tokens,
                                       "enabled": i.enabled} for i in results["extensions"]["items"]]
    if "builtin" in results:
        d["details"]["builtin_deny_suggestions"] = sorted(results["builtin"]["suggestions"])
    if real:
        d["real_context"] = real
    return d


def render(d: dict, fmt: str = "text") -> str:
    if fmt == "json":
        return json.dumps(d, indent=2, ensure_ascii=False)
    md = fmt == "md"
    pre = d["preflight"]
    out = ["# tokens-doctor report" if md else "tokens-doctor report", ""]
    out.append(f"{pre['os']} · Python {pre['python']} · Claude Code {pre['claude_version'] or 'not found'} · "
               f"window {d['window']:,} tokens")
    out += [f"note: {n}" for n in pre["notices"]]
    out += ["", "Estimates (characters / 4); run with --use-context for the real numbers." if "real_context" not in d
            else "Estimates (characters / 4), compared with the real /context numbers below.", ""]
    head = ("Component", "Tokens", "% window", "Target", "")
    rows = [(r["component"], f"{r['tokens']:,}", f"{r['pct']}%", f"<{r['target_pct']}%", BARS[r["status"]])
            for r in d["components"]]
    out += _table(head, rows, md)
    if d.get("real_context"):
        rc = d["real_context"]
        out += ["", f"Real /context ({rc['total']:,} of {rc['window']:,} tokens):"]
        out += _table(("Category", "Tokens"), [(k, f"{v:,}") for k, v in rc["categories"].items()], md)
    if d["findings"]:
        out += ["", "Findings:"] + [f"- {f}" for f in d["findings"]]
    return "\n".join(out) + "\n"


def _table(head, rows, md: bool) -> list:
    if md:
        return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(r) + " |" for r in rows]
    w = [max(len(str(x)) for x in col) for col in zip(head, *rows)]
    return ["  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)).rstrip() for r in [head, *rows]]


def redact(text: str) -> str:
    """Replace the home path and the user name so the output is safe to paste in an issue."""
    home = str(Path.home())
    text = text.replace(home, "~")
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    return text.replace(user, "<user>") if len(user) > 2 else text
