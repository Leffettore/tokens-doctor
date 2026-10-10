import json
import shutil
from pathlib import Path

import pytest

from tokens_doctor import config


@pytest.fixture
def tree(tmp_path):
    """A copy of tests/fixtures in a folder whose name has a space and a non-ASCII character."""
    root = tmp_path / "çà root"
    shutil.copytree(Path(__file__).parent / "fixtures", root)
    inst = root / "claude" / "plugins" / "installed_plugins.json"
    data = json.loads(inst.read_text())
    data["plugins"]["demo@market"][0]["installPath"] = str(root / "claude" / "plugins" / "demo")
    inst.write_text(json.dumps(data))
    return root


@pytest.fixture
def ctx(tree):
    return config.Context(claude_dir=tree / "claude", claude_json=tree / "claude" / ".claude.json",
                          project=tree / "project", cfg=config.load(tree / "nope.json"))


def snapshot(root: Path) -> dict:
    import hashlib
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}
