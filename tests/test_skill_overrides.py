import json

from tests.conftest import snapshot
from tokens_doctor import audit, backup, cli, skills


def test_plugin_skills_counted_once_and_overrides_reduce(tree, ctx, tmp_path):
    plug = tree / "claude" / "plugins" / "demo" / "skills" / "helper"
    plug.mkdir(parents=True)
    (plug / "SKILL.md").write_text("---\nname: helper\ndescription: " + "Helps with lots of things. " * 10 + "\n---\nbody\n")
    r = skills.audit(ctx)
    helper = next(s for s in r["skills"] if s.scope == "plugin")
    assert helper.name == "demo:helper" and helper.tokens > 50
    before = r["tokens"]
    run = backup.Run(tmp_path / "state")
    acts = [a for a in audit.plan_actions(ctx, audit.run_audit(ctx), lambda: run) if a.slim]
    assert [a.text for a in acts] == ["Set skill 'demo:helper' (plugin, ~%d tokens) to name-only" % helper.tokens]
    acts[0].apply()
    assert json.loads((tree / "claude" / "settings.json").read_text())["skillOverrides"] == {"demo:helper": "name-only"}
    assert skills.audit(ctx)["tokens"] < before
    acts[0].alt[2]()  # turn it off completely
    assert skills.audit(ctx)["tokens"] == before - helper.tokens
    assert (tree / "claude" / "settings.json").read_bytes().count(b"\r\n") > 3  # CRLF kept


def test_real_context_skills_drive_overrides(ctx, tmp_path):
    results = audit.run_audit(ctx)
    results["skills"]["real"] = [
        {"name": "pdf", "source": "claude.ai sync", "tokens": 150}, {"name": "init", "source": "Built-in", "tokens": 20},
        {"name": "graphify", "source": "User", "tokens": 120}]
    run = backup.Run(tmp_path / "state")
    slim = [a for a in audit.plan_actions(ctx, results, lambda: run) if a.slim]
    assert [a.text.split("'")[1] for a in slim] == ["pdf"]  # tiny and user-folder skills are left to the other actions


def test_slim_skills_flag_is_unattended(tree, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setattr(cli, "capture_context", lambda: {"window": 1000000, "total": 1, "categories": {"Skills": 1},
                                                         "skills": [{"name": "pdf", "source": "Built-in", "tokens": 150}]})
    before = snapshot(tree)
    code = cli.main(["fix", "--slim-skills", "--config-dir", str(tree / "claude"), "--project-path", str(tree / "project")])
    assert code in (0, 1)
    ov = json.loads((tree / "claude" / "settings.json").read_text())["skillOverrides"]
    assert ov == {"pdf": "name-only"} and snapshot(tree) != before
