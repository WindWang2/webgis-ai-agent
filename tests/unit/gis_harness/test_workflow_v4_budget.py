"""Workflow V4 —— 编译器性能/资源预算（结构性 + 差异型，防回归）。

不做脆弱的整机 wall-clock 硬阈值：主断言是**结构性预算**（阶段数、
产物有界、缓存复用），wall-clock 仅作宽松护栏（2s）。

运行：pytest tests/unit/gis_harness/test_workflow_v4_budget.py
"""
from __future__ import annotations

import json
import time

from app.services.gis_harness.workflow_compiler import COMPILER_STAGES
from app.services.gis_harness.workflow_v4.compiler_v4 import (
    WORKFLOW_V4_STAGES,
    compile_workflow_v4,
)

_PROFILE = {
    "featureCount": 5000,
    "geometryTypes": ["Point"],
    "crs": "EPSG:32648",
    "fields": {"name": {"type": "string"}, "value": {"type": "number"}},
}


def test_stage_budget_is_15_plus_8() -> None:
    assert len(COMPILER_STAGES) == 15
    assert len(WORKFLOW_V4_STAGES) == 8


def test_compilation_size_budget() -> None:
    c = compile_workflow_v4("分析成都便利店的空间密度", profile=_PROFILE)
    payload = json.dumps(c.to_bounded_dict(), ensure_ascii=False)
    assert len(payload) < 64_000, "V4 产物必须是摘要不是证据倾倒"
    dag = c.typed_dag
    assert len(dag.get("nodes", [])) <= 64
    assert len(dag.get("edges", [])) <= 128


def test_compile_wallclock_guard_and_memo_reuse() -> None:
    """宽松护栏：首编译 < 2s；memo 命中的复编译不劣于首编译（宽松系数
    1.5 吸收计时噪声），锁定 planner memo 复用主张。"""
    t0 = time.perf_counter()
    compile_workflow_v4("分析成都便利店的空间密度", profile=_PROFILE)
    first = time.perf_counter() - t0
    t0 = time.perf_counter()
    compile_workflow_v4("分析成都便利店的空间密度", profile=_PROFILE)
    second = time.perf_counter() - t0
    assert first < 2.0, f"first compile too slow: {first:.3f}s"
    assert second < 2.0, f"re-compile too slow: {second:.3f}s"
    assert second <= first * 1.5 + 0.05, (
        f"memo 命中的复编译不应显著慢于首编译: {first:.3f}s -> {second:.3f}s")


def test_corpus_evaluation_budget() -> None:
    """全语料评估（37 表述 × 双编译）< 5s —— 评估可进本地门禁。"""
    from app.services.gis_harness.workflow_v4.evaluation import (
        evaluate_compiler,
    )
    t0 = time.perf_counter()
    report = evaluate_compiler()
    elapsed = time.perf_counter() - t0
    assert report.passed == report.total
    assert elapsed < 5.0, f"corpus evaluation too slow: {elapsed:.2f}s"
