"""All OS-specific logic. The rest of the code uses pathlib and these helpers only."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

APP = "tokens-doctor"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()
    except OSError:
        return False


def os_name() -> str:
    return "macOS" if is_macos() else "WSL" if is_wsl() else "Linux"


def claude_dir(override: Optional[str] = None) -> Path:
    """User config root. CLAUDE_CONFIG_DIR is the variable Claude Code itself honors."""
    raw = override or os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


def claude_json(override: Optional[str] = None) -> Path:
    """The file holding user-scope MCP servers: ~/.claude.json, or .claude.json inside a custom config dir."""
    if override or os.environ.get("CLAUDE_CONFIG_DIR"):
        return claude_dir(override) / ".claude.json"
    return Path.home() / ".claude.json"


def config_dir() -> Path:
    if is_macos():
        return Path.home() / "Library" / "Application Support" / APP
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP


def state_dir() -> Path:
    if is_macos():
        return Path.home() / "Library" / "Application Support" / APP / "state"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / APP


def find_claude() -> Optional[str]:
    return shutil.which("claude")


def run(cmd: list[str], timeout: float = 10, input: Optional[str] = None) -> Optional[subprocess.CompletedProcess]:
    """Run a command with shell=False; None when it is missing, times out or cannot start."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input, shell=False)
    except (OSError, subprocess.SubprocessError):
        return None


def is_windows_path(s: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", s)) or s.startswith("\\\\")


def on_windows_drive(p: Path) -> bool:
    return str(p).startswith("/mnt/") and is_wsl() and bool(re.match(r"^/mnt/[a-z]/", str(p)))


def process_cmdline(pid: int) -> str:
    """Command line of a process, '' when it does not exist."""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
    except OSError:
        r = run(["ps", "-p", str(pid), "-o", "command="])
        return r.stdout.strip() if r and r.returncode == 0 else ""


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
