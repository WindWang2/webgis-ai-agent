"""方向 09（ADR-0204）：hotpath flag registry 双向一致性测试。

1. 热路径目录里出现的每个 ``GIS_*`` 环境变量字面量都必须登记 —— 新 flag
   绕过盘点静默增殖（行为矩阵爆炸的根因）会被 CI 捕捉；
2. registry 每个条目在仓内 ``app/`` 源码中有真实咨询点 —— 无死条目；
3. 类别自洽：opt_in 必须 default_on=False，stable 必须 default_on=True。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services.gis_harness.hotpath_convergence.flag_registry import (
    REGISTRY,
    registered_envs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# 热路径扫描范围（新 flag 高发区）。扩大范围前先确认条目语义仍然成立。
SCAN_TARGETS = [
    REPO_ROOT / "app" / "services" / "gis_harness",
    REPO_ROOT / "app" / "services" / "chat",
    REPO_ROOT / "app" / "agent_pi_bridge.py",
]

_GIS_LITERAL = re.compile(r"[\"']GIS_[A-Z0-9_]+[\"']")


def _iter_py_files():
    for target in SCAN_TARGETS:
        if target.is_file():
            yield target
        else:
            yield from sorted(target.rglob("*.py"))


def test_every_gis_literal_in_hotpath_is_registered():
    scanned: dict[str, list[str]] = {}
    for py in _iter_py_files():
        text = py.read_text(encoding="utf-8", errors="replace")
        for match in _GIS_LITERAL.findall(text):
            env = match.strip("\"'")
            scanned.setdefault(env, []).append(str(py.relative_to(REPO_ROOT)))
    registered = registered_envs()
    unregistered = {
        env: paths for env, paths in scanned.items() if env not in registered
    }
    assert not unregistered, (
        "热路径出现未登记的 GIS_* flag —— 请在 flag_registry.REGISTRY 登记"
        f"（env/默认值/类别/咨询点），禁止无盘点增殖：{unregistered}"
    )


def test_no_dead_registry_entries():
    import subprocess

    for flag in REGISTRY:
        probe = subprocess.run(
            ["grep", "-rl", flag.env, str(REPO_ROOT / "app")],
            capture_output=True, text=True,
        )
        assert probe.returncode == 0, (
            f"registry 死条目：{flag.env} 在 app/ 无任何咨询点"
        )


def test_registry_kind_default_coherence():
    for flag in REGISTRY:
        if flag.kind == "opt_in":
            assert flag.default_on is False, flag.env
        elif flag.kind == "stable":
            assert flag.default_on is True, flag.env
        # mode：default_on 表示空值时模块语义是否处于开启路径，不强约束


def test_known_flag_semantics_snapshot():
    """关键 flag 语义快照（ADR-0204 裁决：不改默认值，只收口可见性）。"""
    by_env = {f.env: f for f in REGISTRY}
    assert by_env["GIS_MISSION_HOTPATH"].kind == "opt_in"
    assert by_env["GIS_MISSION_HOTPATH"].default_on is False
    assert by_env["GIS_CAPABILITY_DISPATCH_BIND"].default_on is True
    assert by_env["GIS_CLAIM_INGEST"].default_on is True
    assert by_env["GIS_SWARM_ORCHESTRATOR"].default_on is False
    assert by_env["GIS_VISUAL_EVALUATOR"].kind == "mode"
