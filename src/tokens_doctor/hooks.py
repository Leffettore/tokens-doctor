"""Entry points for Claude Code hook events. Fast, always exit 0, never print to stdout."""
from __future__ import annotations

import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import act, config, state
from . import platform as plat

SPAWN_ARGS = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "start_new_session": True}


def spawn_watcher(session_id: str, *extra: str):
    return subprocess.Popen([sys.executable, "-m", "tokens_doctor", "watch", "--session", session_id, *extra], **SPAWN_ARGS)


def handle(event: str, payload: dict, spawn=spawn_watcher) -> None:
    """Never raises: a monitoring bug must not block or slow Claude."""
    try:
        _handle(event, payload, spawn)
    except Exception as e:  # noqa: BLE001
        state.log(f"hook {event} failed: {type(e).__name__}: {e}")


def _handle(event: str, p: dict, spawn) -> None:
    sid = p.get("session_id")
    if not sid:
        return
    st, cfg = state.State(sid), config.load()
    s = st.get()
    if event == "Stop":
        state.kill_watcher(s.get("pid"))
        st.update(transcript=p.get("transcript_path", s.get("transcript", "")), cwd=p.get("cwd", ""))
        child = spawn(sid, "--idle", f"{cfg['idle_minutes']}m")
        st.update(pid=child.pid)
        state.prune()
    elif event == "UserPromptSubmit":
        st.update(cancel=uuid.uuid4().hex, last_action="none")
        state.kill_watcher(s.get("pid"))
    elif event == "SessionStart":
        st.update(pane=act.find_pane(sid, plat.claude_dir()), transcript=p.get("transcript_path", ""), cwd=p.get("cwd", ""))
    elif event == "PreCompact":
        st.update(compactions=s.get("compactions", 0) + 1)
    elif event == "SessionEnd":
        state.kill_watcher(s.get("pid"))
        if p.get("reason") in ("clear", "resume") or not s.get("transcript"):
            st.delete()  # a fresh context follows, or the session never finished a turn
        else:
            spawn(sid, "--exit-check")  # detached: this hook may be killed as soon as it returns


def run_stdin(event: str) -> None:
    import json
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    handle(event, payload if isinstance(payload, dict) else {})


def now() -> float:
    return time.time()


__all__ = ["handle", "run_stdin", "Path"]
