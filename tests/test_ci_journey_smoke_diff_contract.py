"""TC-06：PR journey smoke 不得因浅检出静默跳过。

修复前：actions/checkout 默认 depth=1 + ``git fetch --depth=1`` → 三点 diff
报 "no merge base"，管道末端 ``|| true`` 吞掉错误，node 收到空输入返回
``run:false``，smoke 步骤被 skip，job 却是绿的。
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quality-e2e.yml"


def _job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["journey-smoke"]


def _decide_script() -> str:
    for step in _job()["steps"]:
        if step.get("id") == "lanes":
            return step["run"]
    raise AssertionError("journey-smoke 缺少 lanes 决策步骤")


def test_journey_smoke_checkout_has_full_history():
    checkout = next(s for s in _job()["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("fetch-depth") == 0


def test_lane_decision_does_not_shallow_fetch_base():
    assert "--depth=1" not in _decide_script()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_lane_decision_fails_closed_when_diff_unavailable(tmp_path):
    """git diff 失败（无 merge base）时必须输出 run:true。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text("#!/bin/sh\necho 'fatal: no merge base' >&2\nexit 128\n")
    fake_git.chmod(fake_git.stat().st_mode | stat.S_IEXEC)
    script = _decide_script().replace("${{ github.base_ref }}", "master")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
           "GITHUB_OUTPUT": str(tmp_path / "out")}
    subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, env=env, check=True,
                   capture_output=True, text=True)
    assert json.loads((tmp_path / "lanes.json").read_text())["run"] is True
    assert "run=true" in (tmp_path / "out").read_text()
