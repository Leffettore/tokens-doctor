"""Transcript parsing. Reads structure and token counts only; message text is never kept."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


@dataclass
class SessionStats:
    session_id: str = ""
    path: Optional[Path] = None
    cwd: str = ""
    tokens: int = 0  # context size at the last main-thread reply
    overhead: int = 0  # context of the first reply: system prompt, tools, skills, memory
    msgs: int = 0  # user prompts since the last compaction
    tool_share: float = 0.0  # tool results / all message content, since the last compaction
    compactions: int = 0
    last_ts: Optional[float] = None  # epoch seconds of the last entry
    files: list = field(default_factory=list)  # files touched by edit tools
    errors: int = 0  # tool results flagged as errors

    @property
    def message_tokens(self) -> int:
        return max(0, self.tokens - self.overhead)


def parse_ts(s) -> Optional[float]:
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_session(path: Path) -> SessionStats:
    st = SessionStats(session_id=path.stem, path=path)
    tool = other = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        ts = parse_ts(e.get("timestamp"))
        if ts and (st.last_ts is None or ts > st.last_ts):
            st.last_ts = ts
        if e.get("cwd") and not st.cwd:
            st.cwd = e["cwd"]
        if e.get("isSidechain"):
            continue
        if e.get("subtype") == "compact_boundary":
            st.compactions += 1
            meta = e.get("compactMetadata") or {}
            st.tokens = (meta.get("postTokens") or 0) + st.overhead  # postTokens counts messages only
            st.msgs, tool, other = 0, 0, 0
            continue
        msg = e.get("message") if isinstance(e.get("message"), dict) else {}
        usage = msg.get("usage") if e.get("type") == "assistant" else None
        if usage:
            st.tokens = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            st.overhead = st.overhead or st.tokens
        content = msg.get("content")
        if not content or e.get("isMeta") or e.get("isCompactSummary"):
            continue
        blocks = content if isinstance(content, list) else []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") in EDIT_TOOLS:
                fp = (b.get("input") or {}).get("file_path") or (b.get("input") or {}).get("notebook_path")
                if fp and fp not in st.files:
                    st.files.append(fp)
            if b.get("type") == "tool_result" and b.get("is_error"):
                st.errors += 1
        size = len(json.dumps(content))
        if e.get("type") == "user" and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
            tool += size
        else:
            other += size
            if e.get("type") == "user":
                st.msgs += 1
    st.tool_share = tool / (tool + other) if tool + other else 0.0
    return st


def find_transcript(claude_dir: Path, session_id: Optional[str] = None) -> Optional[Path]:
    """The transcript of a session id, or the most recently written one."""
    projects = claude_dir / "projects"
    if not projects.is_dir():
        return None
    if session_id:
        if not re.fullmatch(r"[\w.-]+", session_id):
            return None
        return next(iter(projects.glob(f"*/{session_id}.jsonl")), None)
    files = [p for p in projects.glob("*/*.jsonl") if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime, default=None)
