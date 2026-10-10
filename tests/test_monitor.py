import io
import json
import stat
import time
from pathlib import Path

import pytest

from tokens_doctor import (
    act,
    cli,
    config,
    decide,
    hooks,
    install_hooks,
    monitor,
    notify,
    session,
    state,
    watcher,
)
from tokens_doctor import platform as plat
from tokens_doctor.decide import Facts


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))


def usage(n):
    return {"input_tokens": n, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def write_transcript(path, last_ts, tokens=160000, overhead=30000, extra=()):
    lines = [
        {"type": "user", "timestamp": iso(last_ts - 100), "cwd": str(path.parent), "message": {"content": "hello"}},
        {"type": "assistant", "timestamp": iso(last_ts - 90), "message": {"content": [], "usage": usage(overhead)}},
        {"type": "assistant", "timestamp": iso(last_ts), "message": {"content": "x", "usage": usage(tokens)}},
        *extra,
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "claude" / "projects" / "p").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "notify", lambda title, msg, **kw: calls.append((title, msg)) or "stub")
    return calls


def cfg_(**kw):
    c = config.load(Path("/nonexistent"))
    c.update(kw)
    return c


# ---- session reader ----

def test_read_session(tmp_path):
    extra = [
        {"type": "assistant", "timestamp": iso(1000), "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/a/b.py"}}], "usage": usage(50000)}},
        {"type": "user", "timestamp": iso(1001), "message": {"content": [{"type": "tool_result", "content": "y" * 4000, "is_error": True}]}},
        {"type": "assistant", "isSidechain": True, "message": {"content": "z", "usage": usage(999999)}},
    ]
    p = write_transcript(tmp_path / "s.jsonl", 900, tokens=40000, extra=extra)
    st = session.read_session(p)
    assert (st.overhead, st.tokens, st.msgs, st.compactions) == (30000, 50000, 1, 0)
    assert st.files == ["/a/b.py"] and st.errors == 1 and st.tool_share > 0.5
    assert st.last_ts == 1001 and st.cwd == str(tmp_path)
    with_compact = write_transcript(tmp_path / "t.jsonl", 900, extra=[{"subtype": "compact_boundary", "compactMetadata": {"postTokens": 8000}, "timestamp": iso(950)}])
    st = session.read_session(with_compact)
    assert (st.compactions, st.tokens, st.msgs) == (1, 38000, 0)


def test_find_transcript(env):
    d = env / "claude"
    a = write_transcript(d / "projects" / "p" / "abc.jsonl", 100)
    assert session.find_transcript(d, "abc") == a and session.find_transcript(d) == a
    assert session.find_transcript(d, "../evil") is None and session.find_transcript(d, "zzz") is None


# ---- decision engine, one row per rule ----

TH = config.DEFAULTS["thresholds"]


def D(**kw):
    base = dict(window=200000, overhead=30000, tokens=30000 + 50000, msgs=10)
    base.update(kw)
    return decide.decide(Facts(**base), TH)


@pytest.mark.parametrize("kw,action,T", [
    (dict(tokens=30000 + 10000), "none", 70),  # under 20k of messages
    (dict(tokens=30000 + 100000), "none", 70),  # 59% < 70
    (dict(tokens=30000 + 125000), "compact", 70),  # 73%
    (dict(tokens=30000 + 105000, msgs=60), "compact", 60),  # >50 messages: T 60 (62%)
    (dict(tokens=30000 + 100000, msgs=60), "none", 60),  # 59% < 60
    (dict(tokens=30000 + 100000, msgs=25), "none", 65),  # 20-50 messages: T 65
    (dict(tokens=30000 + 112000, compactions=1), "compact", 65),  # one earlier compaction: T 65 (66%)
    (dict(tokens=30000 + 105000, compactions=1), "none", 65),
    (dict(tokens=30000 + 100000, tool_share=0.6, msgs=60, compactions=1), "compact", 50),
    (dict(tokens=30000 + 100000, peak=True), "none", 65),
    (dict(tokens=30000 + 100000, msgs=300, compactions=1, tool_share=0.9, peak=True, task_done=False), "compact", 45),
    (dict(tokens=30000 + 100000, idle_minutes=10), "compact", 55),  # away modifier, 59% >= 55
    (dict(tokens=30000 + 80000, idle_minutes=10), "none", 55),  # 47% < 55
    (dict(tokens=30000 + 100000, idle_minutes=90), "clear", 55),  # long idle
    (dict(tokens=30000 + 100000, compactions=2), "clear", 65),
    (dict(tokens=30000 + 100000, task_done=True), "clear", 60),
    (dict(tokens=30000 + 140000), "compact", 70),  # >= 80% ceiling
    (dict(tokens=30000 + 10000, over_targets=["MCP tools"]), "fix-config", 70),
    (dict(tokens=30000 + 125000, over_targets=["MCP tools"]), "compact", 70),
])
def test_decide_table(kw, action, T):
    d = D(**kw)
    assert (d.action, d.T) == (action, T), d.reasons


def test_decide_floor_and_reasons():
    d = D(tokens=30000 + 100000, msgs=300, compactions=1, tool_share=0.9, peak=True, idle_minutes=30)
    assert d.T == 40  # floor
    assert d.action == "compact" and any("messages" in r for r in d.reasons)
    assert "fixed overhead" in " ".join(D(over_targets=["Skills"]).reasons)


def test_leaving_applies_away_modifier():
    assert D(tokens=30000 + 100000, leaving=True).action == "compact"  # 59% >= 55


# ---- hooks ----

class FakeProc:
    pid = 999999


def test_hooks_flow(env, monkeypatch, capsys):
    spawned = []
    sp = lambda sid, *a: spawned.append((sid, a)) or FakeProc()  # noqa: E731
    hooks.handle("Stop", {"session_id": "s1", "transcript_path": "/t.jsonl", "cwd": "/w"}, spawn=sp)
    st = state.State("s1").get()
    assert spawned == [("s1", ("--idle", "5m"))] and st["pid"] == 999999 and st["transcript"] == "/t.jsonl"
    hooks.handle("UserPromptSubmit", {"session_id": "s1"}, spawn=sp)
    assert state.State("s1").get()["cancel"]
    hooks.handle("PreCompact", {"session_id": "s1"}, spawn=sp)
    hooks.handle("PreCompact", {"session_id": "s1"}, spawn=sp)
    assert state.State("s1").get()["compactions"] == 2
    hooks.handle("SessionEnd", {"session_id": "s1", "reason": "other"}, spawn=sp)
    assert spawned[-1] == ("s1", ("--exit-check",))
    hooks.handle("SessionEnd", {"session_id": "s1", "reason": "clear"}, spawn=sp)
    assert not state.State("s1").exists()
    hooks.handle("Nonsense", {"session_id": "s1"}, spawn=sp)  # unknown event: no error
    hooks.handle("Stop", {}, spawn=sp)  # no session id: no error
    assert capsys.readouterr().out == ""


def test_hook_cli_exit_zero_and_silent(env, monkeypatch, capsys):
    for stdin in ("", "not json", "[1,2]", '{"session_id": "x"}'):
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        assert cli.main(["hook", "PreCompact"]) == 0
    assert capsys.readouterr().out == ""


def test_hook_survives_failure(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    hooks.handle("Stop", {"session_id": "s2"}, spawn=boom)  # must not raise
    assert "boom" in (state.root() / "monitor.log").read_text()


# ---- watcher with a fake clock ----

class FakeClock:
    def __init__(self, t0, on_sleep=None):
        self.t, self.on_sleep = t0, on_sleep

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s
        if self.on_sleep:
            self.on_sleep(self.t)


def setup_watch(env, tokens=130000, last_ts=None):
    last_ts = last_ts or 1_800_000_000.0
    claude = env / "claude"
    p = write_transcript(claude / "projects" / "p" / "w1.jsonl", last_ts, tokens=tokens)
    st = state.State("w1")
    st.update(transcript=str(p), cancel="tok", pid=4242, cwd=str(env))
    return claude, st, p, last_ts


def test_watcher_notifies_once_after_idle(env, sent):
    claude, st, p, t0 = setup_watch(env)
    cfg = cfg_(sound=False)
    assert watcher.watch("w1", 5, cfg, claude, FakeClock(t0 + 1), pid=4242) == "notified"
    assert len(sent) == 1 and "Compact" in sent[0][1] and "idle 5 min" in sent[0][1]
    assert watcher.watch("w1", 5, cfg, claude, FakeClock(t0 + 1), pid=4242) == "skipped"  # same idle period
    assert len(sent) == 1


def test_watcher_exactly_one_when_level_check_already_notified(env, sent):
    claude, st, p, t0 = setup_watch(env, tokens=30000 + 140000)  # over T even when active
    assert watcher.watch("w1", 5, cfg_(sound=False), claude, FakeClock(t0 + 1), pid=4242) == "notified"
    assert len(sent) == 1  # level check notified, the idle one was suppressed by the cooldown


def test_watcher_cancelled_by_prompt(env, sent):
    claude, st, p, t0 = setup_watch(env)
    clock = FakeClock(t0 + 1, on_sleep=lambda t: st.update(cancel="new") if t > t0 + 60 else None)
    assert watcher.watch("w1", 5, cfg_(sound=False), claude, clock, pid=4242) == "cancelled"
    assert sent == []


def test_watcher_replaced_by_newer_watcher(env, sent):
    claude, st, p, t0 = setup_watch(env)
    clock = FakeClock(t0 + 1, on_sleep=lambda t: st.update(pid=777) if t > t0 + 60 else None)
    assert watcher.watch("w1", 5, cfg_(sound=False), claude, clock, pid=4242) == "cancelled"


def test_watcher_newer_activity(env, sent):
    claude, st, p, t0 = setup_watch(env)

    def late_entry(t):
        if t > t0 + 200:
            with p.open("a") as f:
                f.write("\n" + json.dumps({"type": "user", "timestamp": iso(t), "message": {"content": "back"}}))
    assert watcher.watch("w1", 5, cfg_(sound=False), claude, FakeClock(t0 + 1, late_entry), pid=4242) == "active"
    assert sent == []


def test_watcher_quiet_hours(env, sent):
    claude, st, p, t0 = setup_watch(env)
    cfg = cfg_(sound=False, quiet_hours=[0, 24])
    assert watcher.watch("w1", 5, cfg, claude, FakeClock(t0 + 1), pid=4242) == "notified"
    assert sent == []


def test_exit_check(env, sent):
    claude, st, p, t0 = setup_watch(env, tokens=30000 + 100000)
    assert watcher.exit_check("w1", cfg_(sound=False), claude, now=t0 + 5) == "compact"
    assert len(sent) == 1 and "Next time" in sent[0][1] and not st.exists()
    assert watcher.exit_check("w1", cfg_(), claude) == "gone"


# ---- check command ----

def test_check_command(env, sent, capsys):
    p = write_transcript(env / "claude" / "projects" / "p" / "c1.jsonl", time.time() - 600, tokens=30000 + 100000)
    assert cli.main(["check", "--session", "c1", "--format", "json", "--notify"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "compact" and len(sent) == 1
    p.write_text(p.read_text().replace('"input_tokens": 130000', '"input_tokens": 31000'))
    assert cli.main(["check", "--latest"]) == 0
    assert cli.main(["check", "--session", "missing"]) == 2


# ---- notifier with stub executables ----

def make_stubs(tmp_path, names):
    b = tmp_path / "bin"
    b.mkdir()
    for n in names:
        f = b / n
        f.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/calls-{n}"\n')
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return b


def test_notify_linux_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    monkeypatch.setattr(plat, "is_wsl", lambda: False)
    monkeypatch.setenv("PATH", str(make_stubs(tmp_path, ["notify-send", "paplay"])))
    assert notify.notify("T", "msg", urgent=True) == "notify-send+paplay"
    assert "critical T msg" in (tmp_path / "calls-notify-send").read_text()


def test_notify_falls_back_to_terminal(tmp_path, monkeypatch, env):
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    monkeypatch.setattr(plat, "is_wsl", lambda: False)
    monkeypatch.setenv("PATH", str(make_stubs(tmp_path, [])))
    assert notify.notify("T", "msg") == "terminal"
    assert "No desktop notifier" in notify.test()


def test_notify_macos_and_wsl(tmp_path, monkeypatch):
    monkeypatch.setattr(plat, "is_macos", lambda: True)
    monkeypatch.setenv("PATH", str(make_stubs(tmp_path, ["osascript", "afplay"])))
    assert notify.notify("T", 'say "hi"') == "osascript+afplay"
    assert '"hi"' not in (tmp_path / "calls-osascript").read_text()  # quotes neutralized


def test_notify_wsl_powershell(tmp_path, monkeypatch):
    (tmp_path / "w").mkdir()
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    monkeypatch.setattr(plat, "is_wsl", lambda: True)
    monkeypatch.setenv("PATH", str(make_stubs(tmp_path / "w", ["powershell.exe"])))
    assert notify.notify("T", "msg") == "powershell-toast+powershell-sound"


# ---- install / uninstall hooks ----

def test_install_hooks_idempotent_and_clean(tmp_path):
    p = tmp_path / "settings.json"
    original = {"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "lint"}]}]}}
    p.write_text(json.dumps(original, indent=2) + "\n")
    root = tmp_path / "state"
    assert install_hooks.install(p, root, exe="/abs/tokens-doctor") is True
    data = json.loads(p.read_text())
    assert set(data["hooks"]) == {"Stop", "UserPromptSubmit", "SessionStart", "SessionEnd", "PreCompact", "PreToolUse"}
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo mine"  # other hooks kept
    assert "/abs/tokens-doctor hook Stop #tokens-doctor" in json.dumps(data)
    assert install_hooks.install(p, root, exe="/abs/tokens-doctor") is False  # idempotent
    assert install_hooks.uninstall(p, root) is True
    assert json.loads(p.read_text()) == original
    assert install_hooks.uninstall(p, root) is False


def test_install_hooks_refuses_broken_json(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{ nope")
    with pytest.raises(Exception, match="not valid JSON"):
        install_hooks.install(p, tmp_path / "s")
    assert p.read_text() == "{ nope"


def test_install_hooks_cli_from_scratch(env, capsys):
    assert cli.main(["install-hooks", "--scope", "user"]) == 0
    s = env / "claude" / "settings.json"
    assert "hook SessionEnd #tokens-doctor" in s.read_text()
    assert cli.main(["uninstall-hooks"]) == 0
    assert json.loads(s.read_text()) == {}


# ---- apply via tmux ----

class R:
    def __init__(self, out="", code=0):
        self.stdout, self.returncode = out, code


def tmux(out="%3|claude|1000", sent=None):
    def run(cmd, **kw):
        if cmd[1] == "display-message":
            return R(out)
        if sent is not None:
            sent.append(cmd)
        return R()
    return run


A = dict(last_ts=1000.0, now=1000.0 + 400, handoff_saved=False, allow_clear=False)


def test_apply_success():
    sent = []
    r = act.try_apply("compact", "%3", cfg_(), run=tmux(sent=sent), **A)
    assert r.ok and sent[0][-1].startswith("/compact ") and sent[1][-1] == "Enter"


@pytest.mark.parametrize("kw,out,reason", [
    (dict(), "%3|vim|1000", "not Claude Code"),
    (dict(), "%9|claude|1000", "no longer matches"),
    (dict(), "%3|claude|1397", "last seconds"),
    (dict(last_ts=1000.0 + 399), "%3|claude|1000", "too recently"),
    (dict(pane=""), "%3|claude|1000", "known tmux pane"),
])
def test_apply_safety_checks(kw, out, reason):
    sent = []
    args = {**A, **kw}
    pane = args.pop("pane", "%3")
    r = act.try_apply("compact", pane, cfg_(), run=tmux(out, sent), **args)
    assert not r.ok and reason in r.reason and sent == []


def test_apply_tmux_missing():
    assert not act.try_apply("compact", "%3", cfg_(), run=lambda *a, **k: None, **A).ok


def test_clear_never_without_flag_and_handoff():
    for allow, saved in [(False, False), (True, False), (False, True)]:
        sent = []
        r = act.try_apply("clear", "%3", cfg_(), run=tmux(sent=sent), **{**A, "allow_clear": allow, "handoff_saved": saved})
        assert not r.ok and sent == []
    sent = []
    assert act.try_apply("clear", "%3", cfg_(), run=tmux(sent=sent), **{**A, "allow_clear": True, "handoff_saved": True}).ok
    assert sent[0][-1] == "/clear"


def test_apply_mode_falls_back_to_suggest(env, sent):
    claude, st, p, t0 = setup_watch(env)
    cfg = cfg_(sound=False, mode="apply")
    assert watcher.watch("w1", 5, cfg, claude, FakeClock(t0 + 1), pid=4242) == "notified"
    assert "auto-apply skipped" in sent[0][1]


def test_handoff_has_no_message_text(tmp_path):
    p = write_transcript(tmp_path / "h.jsonl", 900, extra=[
        {"type": "user", "timestamp": iso(901), "message": {"content": "SECRET USER REQUEST"}}])
    out = monitor.write_handoff(session.read_session(p), tmp_path / "ho")
    assert "SECRET" not in out.read_text() and str(p) in out.read_text()


def test_state_files_hold_no_text(env, sent):
    claude, st, p, t0 = setup_watch(env)
    watcher.watch("w1", 5, cfg_(sound=False), claude, FakeClock(t0 + 1), pid=4242)
    assert "hello" not in st.path.read_text() and "hello" not in (state.root() / "monitor.log").read_text()
