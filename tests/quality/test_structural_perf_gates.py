"""Structural Perf Gates（ADR-0104 Wave 11）——上下文预算的确定性闸。

与墙钟 harness（tests/benchmarks/test_perf_harness.py，warn/fail 带）互补：
这里只放**对机器不敏感的结构量**——canonical 查询的工具 schema 字节数与
活动 schema 计数。回归语义 = 单调增长（上下文膨胀必须显式抬基线）。

- 基线：tests/quality/structural_baselines.json（fail-closed，缺基线即红，
  复用 _baseline_policy 的 opt-in 语义）；
- 刷新：STRUCTURAL_UPDATE_BASELINES=1 pytest tests/quality/test_structural_perf_gates.py
- 选择固定 session_id/turn_id：关闭会话粘性带来的顺序依赖（确定性）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "benchmarks"))

from _baseline_policy import ALLOW_MISSING_ENV, UPDATE_ENV  # noqa: E402

STRUCTURAL_UPDATE_ENV = "STRUCTURAL_UPDATE_BASELINES"
BASELINES_PATH = Path(__file__).resolve().parent / "structural_baselines.json"

#: canonical 查询（覆盖 无域首轮 / 单域 / 多域 三种选择面；与
#: tests/benchmarks/_master_context_baseline.py 的代表查询同源）
CASES = {
    "no_domain_first_turn": "帮我分析一下当前地图上的数据情况",
    "network_turn": "帮我算一下从 A 到 B 的最短路径，看看开车多久能到",
    "multi_domain_turn": "对比成都各区县的医院分布热点，评估通勤可达性，并结合遥感 NDVI 影像",
}


def _catalog():
    # ADR-0104：闸必须对进程内 registry 污染免疫 —— 同一 pytest 进程里
    # 更早的测试模块可能向全局单例注册过额外条目；闸测量前重置为
    # builtins（单一事实源口径）。
    from app.lib.gis.algorithm_registry import reset_algorithm_registry
    from app.lib.gis.capability_registry import reset_capability_registry
    from app.lib.gis.artifacts import reset_artifact_type_registry
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.services.tool_catalog import ToolCatalog

    reset_algorithm_registry()
    reset_capability_registry()
    reset_artifact_type_registry()
    reg = ToolRegistry()
    init_tools(reg)
    return ToolCatalog(reg)


@pytest.fixture(scope="module")
def measurements():
    catalog = _catalog()
    out = {}
    for key, text in CASES.items():
        schemas = catalog.select_schemas(
            text, session_id="structural-gate", turn_id=f"t-{key}")
        payload = json.dumps(schemas, ensure_ascii=False)
        out[key] = {"chars": len(payload), "count": len(schemas)}
    return out


def _baselines() -> dict:
    if not BASELINES_PATH.exists():
        pytest.fail(
            f"missing structural baselines: {BASELINES_PATH}（用 "
            f"{STRUCTURAL_UPDATE_ENV}=1 记录）")
    return json.loads(BASELINES_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", sorted(CASES))
def test_context_schema_bytes_gate(measurements, key):
    """上下文 schema 字节：只许显式抬基线，不许静默膨胀。"""
    baselines = _baselines()
    name = f"context_chars_{key}"
    if name not in baselines:
        if os_env_flag(STRUCTURAL_UPDATE_ENV):
            return
        pytest.fail(f"structural baseline missing: {name}")
    baseline = baselines[name]["chars"]
    measured = measurements[key]["chars"]
    assert measured <= baseline, (
        f"context schema grew: {key} {measured} > {baseline} chars。"
        f"如属故意（新增能力），{STRUCTURAL_UPDATE_ENV}=1 刷新基线并在 PR 说明。")


#: 工具可见性投影存在 ±1~2 的过程态抖动（availability 投影依赖注册时
#: 状态）；数量闸给 +3 余量，真正的预算闸是字节天花板（精确）。
COUNT_SLACK = 3


@pytest.mark.parametrize("key", sorted(CASES))
def test_context_schema_count_gate(measurements, key):
    baselines = _baselines()
    name = f"schema_count_{key}"
    if name not in baselines:
        if os_env_flag(STRUCTURAL_UPDATE_ENV):
            return
        pytest.fail(f"structural baseline missing: {name}")
    assert measurements[key]["count"] <= baselines[name]["count"] + COUNT_SLACK, (
        f"schema count grew beyond slack: {name} "
        f"{measurements[key]['count']} > {baselines[name]['count'] + COUNT_SLACK}")


def test_no_missing_baseline_lurks():
    """所有 canonical case 必须有基线（防新增 case 静默漏出闸外）。"""
    baselines = _baselines()
    for key in CASES:
        assert f"context_chars_{key}" in baselines
        assert f"schema_count_{key}" in baselines


def os_env_flag(name: str) -> bool:
    import os

    return os.environ.get(name) == "1"


def _record_baselines(measurements: dict) -> None:  # pragma: no cover - 手动刷新用
    baselines = {}
    for key, m in measurements.items():
        baselines[f"context_chars_{key}"] = {"chars": m["chars"]}
        baselines[f"schema_count_{key}"] = {"count": m["count"]}
    BASELINES_PATH.write_text(
        json.dumps(baselines, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8")


def test_record_mode_writes_baselines(measurements):
    """STRUCTURAL_UPDATE_BASELINES=1 时记录基线（显式 opt-in）。"""
    if not os_env_flag(STRUCTURAL_UPDATE_ENV):
        pytest.skip("not in record mode")
    _record_baselines(measurements)


_ = ALLOW_MISSING_ENV, UPDATE_ENV  # 复用面提示：与墙钟基线同一 opt-in 约定
