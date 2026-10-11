"""CLAUDE.md files and their @imports."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import platform as plat
from .config import Context
from .measure import estimate_tokens, read_text

MAX_DEPTH = 5
IMPORT_RE = re.compile(r"(?<![\w`])@(\S+)")
HISTORY_RE = re.compile(r"^#+\s*(changelog|history|release notes|changes)\b", re.I | re.M)


@dataclass
class MemFile:
    path: Path
    tokens: int
    own_tokens: int
    imports: list = field(default_factory=list)  # (raw, resolved Path or None, tokens, note)
    flags: list = field(default_factory=list)


def _prose_lines(text: str):
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            yield line


def _walk(path: Path, depth: int, seen: set, f: MemFile) -> int:
    real = path.resolve()
    if real in seen:
        f.flags.append(f"import cycle at {path.name}")
        return 0
    seen.add(real)
    text = read_text(path)
    total = estimate_tokens(text)
    for line in _prose_lines(text):
        for raw in IMPORT_RE.findall(line):
            raw = raw.rstrip(".,;)")
            if plat.is_windows_path(raw):
                f.imports.append((raw, None, 0, "outside WSL, skipped"))
                continue
            target = (path.parent / raw).expanduser() if not raw.startswith("~") else Path(raw).expanduser()
            if not target.is_file():
                continue  # an @mention that is not an import
            if depth >= MAX_DEPTH:
                f.imports.append((raw, target, 0, "depth limit"))
                continue
            sub = _walk(target, depth + 1, seen, f)
            f.imports.append((raw, target, sub, ""))
            total += sub
    return total


def find_files(ctx: Context) -> list:
    paths = []
    if ctx.has("user"):
        paths.append(ctx.claude_dir / "CLAUDE.md")
    if ctx.has("project"):
        paths += [d / "CLAUDE.md" for d in [ctx.project, *ctx.project.parents]]
        paths.append(ctx.project / ".claude" / "CLAUDE.md")
    out, seen = [], set()
    for p in paths:
        if p.is_file() and p.resolve() not in seen:
            seen.add(p.resolve())
            out.append(p)
    return out


def audit(ctx: Context) -> dict:
    files, warnings = [], []
    for p in find_files(ctx):
        f = MemFile(p, 0, estimate_tokens(read_text(p)))
        f.tokens = _walk(p, 0, set(), f)
        if HISTORY_RE.search(read_text(p)):
            f.flags.append("has a changelog/history section")
        files.append(f)
        near = [q for q in p.parent.iterdir() if q.name.lower() == "claude.md" and q.name != "CLAUDE.md"]
        warnings += [f"{q} differs from CLAUDE.md only by case" for q in near]
    total = sum(f.tokens for f in files)
    return {
        "tokens": total,
        "files": files,
        "warnings": warnings,
        "findings": [f"{f.path}: {fl}" for f in files for fl in f.flags]
        + ([f"memory is {total} tokens, target ~{ctx.th['memory_tokens']}"] if total > ctx.th["memory_tokens"] else []),
    }
