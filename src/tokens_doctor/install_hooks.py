"""Merge / remove our entries in a settings.json. Entries are recognized by a trailing shell comment."""
from __future__ import annotations

import json
import shlex
import shutil
import sys
from pathlib import Path

from .backup import FixError, Run

EVENTS = ("Stop", "UserPromptSubmit", "SessionStart", "SessionEnd", "PreCompact")
MARKER = "#tokens-doctor"


def executable() -> str:
    """Absolute path, so the hooks work whatever PATH the session has."""
    exe = shutil.which("tokens-doctor")
    if exe:
        return shlex.quote(str(Path(exe).resolve()))
    return f"{shlex.quote(sys.executable)} -m tokens_doctor"


def settings_path(scope: str, claude_dir: Path, project: Path) -> Path:
    return claude_dir / "settings.json" if scope == "user" else project / ".claude" / "settings.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise FixError(f"{path} is not valid JSON, not touching it: {e}") from e
    if not isinstance(data, dict):
        raise FixError(f"{path} is not a JSON object, not touching it")
    return data


def _is_ours(h) -> bool:
    return isinstance(h, dict) and str(h.get("command", "")).rstrip().endswith(MARKER)


def install(path: Path, backup_root: Path, exe: str = "") -> bool:
    """Returns True when the file changed (idempotent: a second call changes nothing)."""
    data, exe = _load(path), exe or executable()
    hooks = data.setdefault("hooks", {})
    changed = False
    for ev in EVENTS:
        cmd = f"{exe} hook {ev} {MARKER}"
        groups = hooks.setdefault(ev, [])
        entries = [h for g in groups if isinstance(g, dict) for h in g.get("hooks", [])]
        mine = [h for h in entries if _is_ours(h)]
        if mine and all(h.get("command") == cmd for h in mine) and len(mine) == 1:
            continue
        _strip(groups)
        groups.append({"hooks": [{"type": "command", "command": cmd}]})
        changed = True
    if changed:
        Run(backup_root).write_json(path, data)
    return changed


def _strip(groups: list) -> bool:
    removed = False
    for g in list(groups):
        if isinstance(g, dict) and isinstance(g.get("hooks"), list):
            keep = [h for h in g["hooks"] if not _is_ours(h)]
            if len(keep) != len(g["hooks"]):
                removed = True
                g["hooks"] = keep
            if not keep:
                groups.remove(g)
    return removed


def uninstall(path: Path, backup_root: Path) -> bool:
    if not path.exists():
        return False
    data = _load(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    removed = False
    for ev in list(hooks):
        if isinstance(hooks[ev], list):
            removed |= _strip(hooks[ev])
            if not hooks[ev]:
                del hooks[ev]
    if not hooks:
        data.pop("hooks")
    if removed:
        Run(backup_root).write_json(path, data)
    return removed
