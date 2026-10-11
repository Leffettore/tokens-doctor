"""Backup of files before they are rewritten (used by install-hooks): copies and a manifest, nothing is deleted."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional


class FixError(Exception):
    pass


def _detect_indent(raw: str):
    m = re.search(r"\n([ \t]+)\"", raw)
    if not m:
        return 2
    return "\t" if m.group(1).startswith("\t") else len(m.group(1))


class Run:
    def __init__(self, root: Path, run_id: Optional[str] = None):
        self.root = root
        base = run_id or time.strftime("%Y%m%d-%H%M%S")
        self.id, n = base, 1
        while (root / "backup" / self.id).exists():
            n += 1
            self.id = f"{base}-{n}"
        self.backup_dir = root / "backup" / self.id
        self.actions: list[dict[str, Any]] = []

    def _rel(self, path: Path) -> Path:
        return Path(str(path.resolve()).lstrip("/"))

    def _save(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        (self.backup_dir / "manifest.json").write_text(json.dumps(self.actions, indent=2), encoding="utf-8")

    def backup_file(self, path: Path) -> None:
        if any(a["op"] == "modify" and a["path"] == str(path.resolve()) for a in self.actions):
            return
        dest = self.backup_dir / "files" / self._rel(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        self.actions.append({"op": "modify", "path": str(path.resolve()), "backup": str(dest)})
        self._save()

    def write_bytes(self, path: Path, data: bytes) -> None:
        existed = path.exists()
        if existed:
            self.backup_file(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.actions.append({"op": "create", "path": str(path.resolve())})
            self._save()
        path.write_bytes(data)

    def write_json(self, path: Path, obj: Any) -> None:
        """Write JSON keeping the file's line endings, indent and trailing newline; undo if it does not parse."""
        raw_bytes = path.read_bytes() if path.exists() else b""
        raw = raw_bytes.decode("utf-8", errors="replace")
        text = json.dumps(obj, indent=_detect_indent(raw) if raw else 2, ensure_ascii=False)
        if not raw or raw.endswith("\n"):
            text += "\n"
        if b"\r\n" in raw_bytes:
            text = text.replace("\n", "\r\n")
        self.write_bytes(path, text.encode("utf-8"))
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            if raw_bytes:
                path.write_bytes(raw_bytes)
            raise FixError(f"{path} would not parse after the edit, original restored: {e}") from e
