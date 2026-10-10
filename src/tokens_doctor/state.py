"""Per-session state files and the hook log, under the platform state folder."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from . import platform as plat


def root() -> Path:
    return plat.state_dir()


def log(msg: str) -> None:
    """Errors and decisions go to a file, never to stdout (hook stdout can be injected into Claude's context)."""
    try:
        f = root() / "monitor.log"
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists() and f.stat().st_size > 200_000:
            f.replace(f.with_suffix(".log.1"))
        with f.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


class State:
    def __init__(self, session_id: str):
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_.") or "unknown"
        self.id = safe
        self.path = root() / "sessions" / f"{safe}.json"

    def get(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def exists(self) -> bool:
        return self.path.exists()

    def update(self, **kw: Any) -> dict[str, Any]:
        data = {**self.get(), **kw}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")  # tmp + rename: a reader never sees half a file
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.path)
        return data

    def delete(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


def prune(days: int = 7) -> None:
    """State of sessions that crashed without a SessionEnd is never read again."""
    d = root() / "sessions"
    try:
        for p in d.glob("*.json"):
            if time.time() - p.stat().st_mtime > days * 86400:
                p.unlink()
    except OSError:
        pass


def kill_watcher(pid: Optional[int]) -> None:
    """Only signals a process that is still one of ours (the pid may have been reused)."""
    import signal
    if pid and pid != os.getpid() and "tokens_doctor" in plat.process_cmdline(pid).replace("-", "_"):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
