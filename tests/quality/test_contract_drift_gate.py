"""Contract Drift Gate（ADR-0104 Wave 4）红线测试。

- 提交的 CONTRACT_DRIFT_REPORT.{md,json} 与当前派生字节一致；
- BLOCKER / MAJOR 必须清零（漂移即红）；
- 编译确定性（指纹稳定）；
- 前端 path 清洗规则的单测（证明检测器有真实判别力，不是恒绿）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from gen_drift_report import DEFAULT_JSON, DEFAULT_MD, generate  # noqa: E402

from app.lib.quality.drift import (  # noqa: E402
    _frontend_api_calls,
    compile_drift_report,
)


@pytest.fixture(autouse=True)
def _fresh_registries():
    """R1 review MAJOR-4：drift 校验器基于 builtins 口径（防进程内污染）。"""
    from app.lib.gis.algorithm_registry import reset_algorithm_registry
    from app.lib.gis.artifacts import reset_artifact_type_registry
    from app.lib.gis.capability_registry import reset_capability_registry

    reset_algorithm_registry()
    reset_capability_registry()
    reset_artifact_type_registry()
    yield


@pytest.fixture(scope="module")
def report():
    return compile_drift_report()


def test_committed_report_is_current():
    md, js = generate()
    assert DEFAULT_MD.read_text(encoding="utf-8") == md, (
        "CONTRACT_DRIFT_REPORT.md 过期：python scripts/gen_drift_report.py"
    )
    assert DEFAULT_JSON.read_text(encoding="utf-8") == js


def test_no_blocking_drift(report):
    """BLOCKER / MAJOR 清零（MINOR 是富化债，报告内跟踪即可）。"""
    assert report.counts["BLOCKER"] == 0, (
        f"contract drift: {report.blocking[:5]}"
    )
    assert report.counts["MAJOR"] == 0


def test_report_deterministic(report):
    again = compile_drift_report()
    assert again.fingerprint == report.fingerprint
    assert again.issues == report.issues


def test_frontend_call_normalization_rules():
    """清洗规则单测：模板插值 / 查询串 / MapLibre 占位 / 前缀字面量。"""
    calls = _frontend_api_calls()
    # 真实调用必须被收集（后端存在，编号路由族）
    assert any(c.startswith("/api/v1/chat/sessions/") or
               c == "/api/v1/chat/sessions" for c in calls)
    # MapLibre tile 占位必须折叠为 *
    tile_paths = [p for p in calls if "/tiles/" in p]
    for p in tile_paths:
        assert "{z}" not in p and "{x}" not in p, p
    # 注释行不得进入证据（authenticated-download.ts 顶部注释含字面量）
    comment_only = "/api/v1/export/download"
    count = calls.get(comment_only, 0)
    assert count <= 1, f"注释泄漏为调用证据：{comment_only} ×{count}"


def test_report_has_real_signal():
    """漂移检测器必须有真实吞吐：后端收集到足够路由、前端收集到足够调用。"""
    from app.lib.quality.drift import _backend_api_routes

    backend = _backend_api_routes()
    frontend = _frontend_api_calls()
    assert len(backend) >= 50, f"backend 路由收集异常：{len(backend)}"
    assert len(frontend) >= 20, f"frontend 调用收集异常：{len(frontend)}"
