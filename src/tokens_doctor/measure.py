"""Token estimation, and the opt-in capture of the real numbers from `claude -p /context`."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from . import platform as plat


def estimate_tokens(text: str) -> int:
    """characters / 4, rounded up. An estimate; compare with `tokens-doctor audit --use-context`."""
    return (len(text) + 3) // 4


def read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return ""


def file_tokens(path: Path) -> int:
    return estimate_tokens(read_text(path))


def parse_count(s: str) -> int:
    """'2.4k' -> 2400, '1m' -> 1000000, '622' -> 622."""
    m = re.fullmatch(r"\s*([\d.,]+)\s*([kKmM]?)\s*", s)
    if not m:
        return 0
    n = float(m.group(1).replace(",", ""))
    return int(n * {"": 1, "k": 1000, "m": 1000000}[m.group(2).lower()])


def parse_context(text: str) -> dict:
    """Parse the markdown output of /context: {'window': int, 'total': int, 'categories': {name: tokens}}."""
    out: dict = {"window": 0, "total": 0, "categories": {}}
    m = re.search(r"\*\*Tokens:\*\*\s*([\d.,]+[kKmM]?)\s*/\s*([\d.,]+[kKmM]?)", text)
    if m:
        out["total"], out["window"] = parse_count(m.group(1)), parse_count(m.group(2))
    in_categories = False
    for line in text.splitlines():
        if line.startswith("#"):
            in_categories = "usage by category" in line.lower()
        elif in_categories:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 3 and re.fullmatch(r"[\d.,]+[kKmM]?", cells[1]):
                out["categories"][cells[0]] = parse_count(cells[1])
    return out


def capture_context(claude: Optional[str] = None) -> Optional[dict]:
    """Ask the installed Claude Code for its real numbers. This starts a real (tiny) Claude Code run."""
    claude = claude or plat.find_claude()
    if not claude:
        return None
    r = plat.run([claude, "-p", "/context", "--no-session-persistence"], timeout=120)
    if not r or r.returncode != 0:
        return None
    parsed = parse_context(r.stdout)
    return parsed if parsed["categories"] else None
