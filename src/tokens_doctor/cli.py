"""Command line: argparse, orchestration, exit codes (0 ok, 1 over targets / action recommended, 2 error)."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__, audit, backup, config, report
from . import platform as plat
from .measure import capture_context

COMMANDS = ("audit", "fix", "restore", "check", "install-hooks", "uninstall-hooks", "hook", "watch", "notify-test")


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--scope", choices=["user", "project", "both"], default="both")
    p.add_argument("--project-path", default=".")
    p.add_argument("--config-dir", help="override the Claude Code user config location")
    p.add_argument("--window", type=int, help="context window in tokens (default 200000)")
    p.add_argument("--format", choices=["text", "md", "json"], default="text")
    p.add_argument("--redact", action="store_true", help="hide user names and home paths")
    p.add_argument("--no-color", action="store_true")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="tokens-doctor", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")
    for name in ("audit", "fix"):
        p = sub.add_parser(name, help="audit (changes nothing)" if name == "audit" else "apply static changes")
        _common(p)
        p.add_argument("--only", help="comma list of: " + ",".join(audit.PHASES))
        p.add_argument("--report-dir")
        p.add_argument("--use-context", action="store_true",
                       help="also run `claude -p /context` for the real numbers (starts a tiny Claude Code run)")
        if name == "fix":
            p.add_argument("--yes", action="store_true", help="apply only the flagged changes, without asking")
    p = sub.add_parser("restore", help="undo a previous `fix` run")
    p.add_argument("backup_id", nargs="?", help="omit to list the available backups")
    c = sub.add_parser("check", help="check one session now and recommend /compact or /clear")
    _common(c)
    c.add_argument("--session")
    c.add_argument("--latest", action="store_true")
    c.add_argument("--task-done", action="store_true", help="the current task is finished")
    c.add_argument("--notify", action="store_true", help="also send a notification when action is recommended")
    for name in ("install-hooks", "uninstall-hooks"):
        h = sub.add_parser(name)
        h.add_argument("--scope", choices=["user", "project"], default="user")
        h.add_argument("--project-path", default=".")
        h.add_argument("--config-dir")
    sub.add_parser("hook", help="called by Claude Code").add_argument("event")
    w = sub.add_parser("watch", help="internal: idle timer started by a hook")
    w.add_argument("--session", required=True)
    w.add_argument("--idle", default=None, help="e.g. 5m")
    w.add_argument("--exit-check", action="store_true", help="internal: SessionEnd advice for next time")
    sub.add_parser("notify-test", help="try the notification and sound")
    return ap


def make_context(a) -> config.Context:
    scope = getattr(a, "scope", "both")
    cfg = config.load()
    return config.Context(
        claude_dir=plat.claude_dir(getattr(a, "config_dir", None)),
        claude_json=plat.claude_json(getattr(a, "config_dir", None)),
        project=Path(getattr(a, "project_path", ".")).expanduser().resolve(),
        scopes=("user", "project") if scope == "both" else (scope,),
        window=getattr(a, "window", None) or cfg["window"],
        cfg=cfg,
    )


def _emit(a, text: str) -> None:
    sys.stdout.write(report.redact(text) if getattr(a, "redact", False) else text)


def _phases(a):
    if not a.only:
        return audit.PHASES
    names = tuple(x.strip() for x in a.only.split(","))
    bad = [n for n in names if n not in audit.PHASES]
    if bad:
        raise SystemExit(f"tokens-doctor: unknown phase {bad[0]!r}; use {','.join(audit.PHASES)}")
    return names


def _measure(a, ctx):
    pre = audit.preflight(ctx)
    results = audit.run_audit(ctx, _phases(a))
    real = capture_context() if getattr(a, "use_context", False) else None
    if real and real["window"] and not a.window:
        ctx.window = real["window"]  # the installed model's real window beats the 200k default
    return pre, results, audit.summarize(ctx, results), real


def _write_reports(a, ctx, pre, results, summary, real) -> None:
    d = report.build(pre, results, summary, ctx.window, real)
    out = Path(a.report_dir).expanduser() if a.report_dir else plat.state_dir() / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for ext, fmt in (("md", "md"), ("json", "json")):
        text = report.render(d, fmt)
        (out / f"report-{stamp}.{ext}").write_text(report.redact(text) if a.redact else text, encoding="utf-8")
    print(f"reports written to {out}", file=sys.stderr)


def cmd_audit(a) -> int:
    ctx = make_context(a)
    pre, results, summary, real = _measure(a, ctx)
    _emit(a, report.render(report.build(pre, results, summary, ctx.window, real), a.format))
    if a.report_dir:
        _write_reports(a, ctx, pre, results, summary, real)
    return 1 if summary["over"] else 0


def _ask(text: str, options: str = "y/N") -> str:
    try:
        return input(f"{text} [{options}] ").strip().lower()
    except EOFError:
        return ""


def cmd_fix(a) -> int:
    ctx = make_context(a)
    pre, results, summary, real = _measure(a, ctx)
    _emit(a, report.render(report.build(pre, results, summary, ctx.window, real), a.format))
    run = backup.Run(plat.state_dir())
    done, failed = [], 0
    for act in audit.plan_actions(ctx, results, lambda: run):
        if a.yes and not act.flagged:
            continue
        if act.diff:
            print(act.diff)
        choice = "y" if a.yes else _ask(act.text, "y/p/N" if act.alt else "y/N")
        fn = act.apply if choice == "y" else act.alt[2] if act.alt and choice == act.alt[0] else None
        if not fn:
            continue
        try:
            fn()
            done.append(act.text)
        except Exception as e:  # one bad change must not stop the others
            failed += 1
            print(f"FAILED: {act.text}: {e}", file=sys.stderr)
    if "mcp" in results and not a.yes and results["mcp"]["servers"] and sys.stdin.isatty():
        names = _ask("Essential MCP servers for a minimal.mcp.json (comma list, empty to skip)", "names")
        if names:
            out = ctx.project / "minimal.mcp.json"
            audit.minimal_mcp(ctx, results, [n.strip() for n in names.split(",")], out, run)
            done.append(f"wrote {out}")
            if "--strict-mcp-config" in pre["supports"]:
                print(f"launch with: claude --mcp-config {out} --strict-mcp-config")
    if not done:
        print("nothing changed")
        return 2 if failed else 0
    pre2, results2, summary2, _ = _measure(a, ctx)
    print(f"\n{len(done)} change(s) applied, undo with: tokens-doctor restore {run.id}")
    for before, after in zip(summary["rows"], summary2["rows"]):
        print(f"  {before['component']}: {before['tokens']:,} -> {after['tokens']:,} tokens ({after['status']})")
    _write_reports(a, ctx, pre2, results2, summary2, None)
    return 2 if failed else (1 if summary2["over"] else 0)


def cmd_restore(a) -> int:
    root = plat.state_dir()
    if not a.backup_id:
        runs = backup.list_runs(root)
        print("\n".join(runs) if runs else "no backups")
        return 0
    for m in backup.restore(root, a.backup_id):
        print(m)
    return 0


def main(argv=None) -> int:
    ap = parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "audit")  # audit is the default command
    a = ap.parse_args(argv)
    try:
        if a.cmd in ("audit", "fix", "restore"):
            return {"audit": cmd_audit, "fix": cmd_fix, "restore": cmd_restore}[a.cmd](a)
        from . import monitor_cli  # the session monitor, imported only when used
        return monitor_cli.dispatch(a)
    except (backup.FixError, OSError) as e:
        print(f"tokens-doctor: {e}", file=sys.stderr)
        return 2
