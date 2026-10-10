import json

import pytest

from tests.conftest import snapshot
from tokens_doctor import audit, backup, cli, mcp, memory, skills
from tokens_doctor.measure import estimate_tokens, parse_context, parse_count


def test_estimate():
    assert estimate_tokens("") == 0 and estimate_tokens("abcd") == 1 and estimate_tokens("abcde") == 2
    assert parse_count("2.4k") == 2400 and parse_count("1m") == 1000000 and parse_count("622") == 622


def test_parse_context():
    text = "**Tokens:** 13.6k / 1m (1%)\n\n### Estimated usage by category\n\n| Category | Tokens | Percentage |\n|---|---|---|\n| Skills | 6k | 0.6% |\n| MCP tools | 622 | 0.1% |\n\n### MCP Tools\n\n| Tool | Server | Tokens |\n| x | y | 153 |\n\n### Skills\n\n| Skill | Source | Tokens |\n|---|---|---|\n| pdf | claude.ai sync | ~150 |\n"
    assert parse_context(text) == {"window": 1000000, "total": 13600, "categories": {"Skills": 6000, "MCP tools": 622},
                                   "skills": [{"name": "pdf", "source": "claude.ai sync", "tokens": 150}]}


def test_frontmatter():
    fm = skills.parse_frontmatter("---\nname: a\ndescription: >\n  line one\n  line two\nother: 'x'\n---\nbody")
    assert fm == {"name": "a", "description": "line one line two", "other": "x"}
    assert skills.parse_frontmatter("no front matter") == {}


def test_memory_imports_and_cycles(ctx):
    r = memory.audit(ctx)
    proj = next(f for f in r["files"] if f.path.parent == ctx.project)
    raws = {i[0] for i in proj.imports}
    assert "docs/big.md" in raws and "docs/loop-a.md" in raws and "not-an-import.md" not in raws
    assert any("cycle" in fl for fl in proj.flags) and any("changelog" in fl for fl in proj.flags)
    assert r["tokens"] > 5000  # big.md counted through the import
    assert memory.propose(ctx, r)  # big import -> pointer proposal


def test_windows_path_import_skipped(ctx):
    (ctx.project / "CLAUDE.md").write_text("see @C:\\Users\\x\\notes.md\n")
    f = next(f for f in memory.audit(ctx)["files"] if f.path.parent == ctx.project)
    assert f.imports[0][3] == "outside WSL, skipped"


def test_skills_mcp(ctx):
    r = skills.audit(ctx)
    assert {s.name for s in r["skills"]} == {"big-skill", "small-skill"}
    assert any("big-skill" in f for f in r["findings"])
    servers = mcp.collect(ctx)
    assert {(s.name, s.scope) for s in servers} == {("github", "user"), ("db", "project")}
    assert all("secret" not in json.dumps(mcp.mask(s.spec)) and "hidden" not in json.dumps(mcp.mask(s.spec)) for s in servers)


def test_audit_changes_nothing(tree, capsys):
    before = snapshot(tree)
    code = cli.main(["audit", "--config-dir", str(tree / "claude"), "--project-path", str(tree / "project"), "--format", "json"])
    assert code in (0, 1) and snapshot(tree) == before
    data = json.loads(capsys.readouterr().out)
    assert {c["component"] for c in data["components"]} >= {"MCP tools", "Memory files", "Total fixed overhead"}
    assert "secret-value" not in json.dumps(data)


def test_fix_restore_roundtrip_and_crlf(tree, ctx, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    before = snapshot(tree)
    results = audit.run_audit(ctx)
    run = backup.Run(tmp_path / "state" / "tokens-doctor")
    for act in audit.plan_actions(ctx, results, lambda: run):
        act.apply()
    after = snapshot(tree)
    assert after != before
    # CRLF settings.json keeps its endings after the plugin was disabled and deny entries were added
    raw = (tree / "claude" / "settings.json").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    data = json.loads(raw)
    assert data["enabledPlugins"]["demo@market"] is False and "WebFetch" in data["permissions"]["deny"]
    assert data["permissions"]["allow"] == ["Bash(ls:*)"]  # other keys kept
    assert not (tree / "claude" / "skills" / "big-skill").exists()  # quarantined, not deleted
    assert any(run.quarantine_dir.rglob("SKILL.md"))
    backup.restore(tmp_path / "state" / "tokens-doctor", run.id)
    restored = {k: v for k, v in snapshot(tree).items()}
    assert restored == before


def test_write_json_failure_leaves_original(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"a": 1}\n')
    run = backup.Run(tmp_path / "state")
    with pytest.raises(TypeError):
        run.write_json(p, {"bad": {1, 2}})  # not serializable
    assert p.read_text() == '{"a": 1}\n'


def test_restore_unknown(tmp_path):
    with pytest.raises(backup.FixError):
        backup.restore(tmp_path, "nope")


def test_cli_exit_codes(tree, monkeypatch):
    args = ["audit", "--config-dir", str(tree / "claude"), "--project-path", str(tree / "project")]
    assert cli.main(args + ["--window", "2000"]) == 1  # everything over target
    assert cli.main(args) in (0, 1)
