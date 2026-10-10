"""Skills: name + description are what sits in the context."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import Context
from .measure import estimate_tokens, read_text


@dataclass
class Skill:
    name: str
    scope: str
    path: Path  # the skill folder
    description: str
    tokens: int


def parse_frontmatter(text: str) -> dict:
    """Minimal YAML front matter: `key: value`, plus indented continuation lines and `>`/`|` blocks."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out, key = {}, None
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            key, val = m.group(1), m.group(2).strip()
            out[key] = "" if val in (">", "|", ">-", "|-") else val.strip("\"'")
        elif key and line.strip():
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def _scan(base: Path, scope: str) -> list:
    out = []
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        md = d / "SKILL.md"
        if d.is_dir() and md.is_file():
            fm = parse_frontmatter(read_text(md))
            name = fm.get("name") or d.name
            desc = fm.get("description", "")
            out.append(Skill(name, scope, d, desc, estimate_tokens(f"{name}\n{desc}")))
    return out


def audit(ctx: Context) -> dict:
    skills = []
    if ctx.has("user"):
        skills += _scan(ctx.claude_dir / "skills", "user")
    if ctx.has("project"):
        skills += _scan(ctx.project / ".claude" / "skills", "project")
    limit = ctx.th["skill_description_chars"]
    return {
        "tokens": sum(s.tokens for s in skills),
        "skills": skills,
        "findings": [f"skill {s.name}: description is {len(s.description)} chars (limit {limit})"
                     for s in skills if len(s.description) > limit],
    }
