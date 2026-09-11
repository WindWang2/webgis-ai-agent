"""性能预算回归检测（ADR-0131 D6）。

manifest 是封闭词表 + 配置化预算的真相文件。本套件保证：
- manifest 结构合法（key 唯一、limit 正数、封闭词表纪律可机器强制）；
- breach 检测语义正确（>limit 触发、≤limit 不触发、未注册 key 不炸）；
- manifest ↔ 代码注册表一致（loading 不丢条目、不增幽灵条目）；
- 关键路径词表与 ADR-0131 的链路图逐项对齐（预算 key 不可被悄悄删除）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from app.lib.observability.budgets import (
    BUDGET_MANIFEST_PATH,
    Budget,
    load_manifest,
    reset_budget_registry_for_tests,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = _REPO_ROOT / BUDGET_MANIFEST_PATH

#: ADR-0131 D6 的目标关键路径（删 key = 结构性红，必须显式改本测试 + ADR）
REQUIRED_KEYS = frozenset({
    "agent_first_response",
    "tool_dispatch",
    "data_query",
    "map_render",
    "workflow_scheduling",
    "artifact_transfer",
})


@pytest.fixture()
def registry():
    return reset_budget_registry_for_tests()


# ── manifest 结构 ────────────────────────────────────────────────────────


def test_manifest_exists_and_valid():
    budgets = load_manifest(MANIFEST)
    assert REQUIRED_KEYS.issubset(set(budgets.keys()))
    for key, budget in budgets.items():
        assert budget.limit_s > 0, key
        assert budget.description, f"{key} 缺描述（预算必须可解释）"


def test_manifest_keys_unique_by_construction():
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    keys = [entry["key"] for entry in raw["budgets"]]
    assert len(keys) == len(set(keys))


def test_invalid_manifest_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "budgets": [
            {"key": "ok_path", "limit_s": 1.0},
            {"key": "bad_path", "limit_s": -1},
        ]
    }), encoding="utf-8")
    with pytest.raises(ValueError):
        load_manifest(bad)

    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps({
        "budgets": [
            {"key": "x", "limit_s": 1.0},
            {"key": "x", "limit_s": 2.0},
        ]
    }), encoding="utf-8")
    with pytest.raises(ValueError):
        load_manifest(dup)


def test_default_registry_loads_repo_manifest():
    registry = reset_budget_registry_for_tests()
    registry.load_manifest(_REPO_ROOT / BUDGET_MANIFEST_PATH)
    assert REQUIRED_KEYS.issubset(set(registry.keys()))


# ── breach 检测语义 ──────────────────────────────────────────────────────


def test_breach_detection_semantics(registry):
    registry.register(Budget(key="probe", limit_s=1.0))
    assert registry.observe("probe", 0.5) is None      # 未超限
    assert registry.observe("probe", 1.0) is None      # 恰好等于（上界内）
    assert registry.observe("probe", 1.001) is not None  # 超限


def test_unregistered_key_creates_no_series(registry):
    """未注册 key 不写任何 Prometheus 序列（封闭词表 = 基数有界，R1-M1）。"""
    from prometheus_client import REGISTRY

    assert registry.observe("ghost_key", 9.9) is None
    assert "ghost_key" not in registry.keys()
    assert REGISTRY.get_sample_value(
        "perf_budget_observed_seconds_count", {"budget": "ghost_key"}) is None


def test_duplicate_registration_rejected(registry):
    registry.register(Budget(key="dup", limit_s=1.0))
    with pytest.raises(ValueError):
        registry.register(Budget(key="dup", limit_s=2.0))


def test_breach_counter_and_histogram_move(registry):
    registry.register(Budget(key="countered", limit_s=0.1))
    breach_before = REGISTRY.get_sample_value(
        "perf_budget_breach_total", {"budget": "countered"}) or 0
    count_before = REGISTRY.get_sample_value(
        "perf_budget_observed_seconds_count", {"budget": "countered"}) or 0
    registry.observe("countered", 5.0)
    assert REGISTRY.get_sample_value(
        "perf_budget_breach_total", {"budget": "countered"}) - breach_before == 1
    assert REGISTRY.get_sample_value(
        "perf_budget_observed_seconds_count", {"budget": "countered"}) - count_before == 1
