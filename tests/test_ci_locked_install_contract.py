"""TC-03 / TC-04：CI 必须安装与生产镜像相同的钉定依赖面。

生产镜像（Dockerfile / Dockerfile.prod）用 ``requirements.lock`` 安装；CI 若只
``pip install -r requirements*.txt``，resolver 会选取范围内最新版本（曾选中
pandas 3.0.6，击穿 #1512），CI 与生产测的是两套依赖。本文件守住：
  * 每个 workflow 中安装 requirements*.txt 的 pip 命令都带 ``-c requirements.lock``；
  * lint job 运行阻塞的依赖上界门禁；
  * requirements.txt 的 pandas 上界保持 <3.0。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _pip_install_lines():
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for lineno, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"pip install .*-r requirements", line):
                yield wf.name, lineno, line


def test_every_requirements_install_is_lock_constrained():
    lines = list(_pip_install_lines())
    assert lines, "未找到任何 requirements 安装命令（解析失效？）"
    bad = [f"{n}:{i}: {l.strip()}" for n, i, l in lines if "-c requirements.lock" not in l]
    assert not bad, "以下 CI 安装未受 requirements.lock 约束：\n" + "\n".join(bad)


def test_lint_job_runs_dependency_bounds_gate():
    wf = yaml.safe_load((WORKFLOWS / "production.yml").read_text(encoding="utf-8"))
    runs = [s.get("run", "") for s in wf["jobs"]["lint"]["steps"]]
    assert any("scripts/check_dependency_bounds.py" in r for r in runs)


def test_pandas_upper_bound_below_3():
    req = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    m = re.search(r"^pandas([^\n#]*)", req, re.M)
    assert m, "requirements.txt 缺少 pandas"
    assert "<3.0" in m.group(1).replace(" ", ""), m.group(0)
