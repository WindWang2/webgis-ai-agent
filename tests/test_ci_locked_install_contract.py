"""TC-03 / TC-04：CI 必须安装与生产镜像相同的钉定依赖面。

生产镜像（Dockerfile / Dockerfile.prod）用 ``requirements.lock`` 安装；CI 若只
``pip install -r requirements*.txt``，resolver 会选取范围内最新版本（曾选中
pandas 3.0.6，击穿 #1512），CI 与生产测的是两套依赖。本文件守住：
  * 每个 workflow 中安装 requirements*.txt 的 pip 命令都带 ``-c requirements.lock``；
  * lint job 运行阻塞的依赖上界门禁；
  * lint job 运行 skills-lock 完整性 / ADS source-registry lint（#1734 前零接线）；
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
    bad = [f"{n}:{i}: {line.strip()}" for n, i, line in lines if "-c requirements.lock" not in line]
    assert not bad, "以下 CI 安装未受 requirements.lock 约束：\n" + "\n".join(bad)


def test_lint_job_runs_dependency_bounds_gate():
    wf = yaml.safe_load((WORKFLOWS / "production.yml").read_text(encoding="utf-8"))
    runs = [s.get("run", "") for s in wf["jobs"]["lint"]["steps"]]
    assert any("scripts/check_dependency_bounds.py" in r for r in runs)


def test_lint_job_runs_skills_lock_and_source_registry_gates():
    """#1734：verify_skills_lock.py / check_source_registry.py 曾零接线
    （无任何 workflow/test 引用，篡改与 source YAML 失真从未在 CI 现形）——
    接入 lint job 后在此钉住，防再被静默摘掉。"""
    wf = yaml.safe_load((WORKFLOWS / "production.yml").read_text(encoding="utf-8"))
    runs = [s.get("run", "") for s in wf["jobs"]["lint"]["steps"]]
    assert any("scripts/verify_skills_lock.py" in r for r in runs), (
        "lint job 必须跑 skills-lock 完整性门禁（#1349）"
    )
    assert any("scripts/check_source_registry.py" in r for r in runs), (
        "lint job 必须跑 ADS source-registry lint（DS1 / ADR-0171）"
    )


def test_pandas_upper_bound_below_3():
    req = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    m = re.search(r"^pandas([^\n#]*)", req, re.M)
    assert m, "requirements.txt 缺少 pandas"
    assert "<3.0" in m.group(1).replace(" ", ""), m.group(0)
