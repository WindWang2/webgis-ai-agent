"""ADR-0153（ac-04）：14+1 诊断码矩阵回归 —— 每码「拦截或修复」闭环。

任务书 §5 验收：14 个诊断码样本 blocking 类 100% 被拦截或自动修复，零静默
放行；修复前后要素数变化写入证据；修复全程非破坏（原始 deepcopy 不受污
染）。fixtures 见 tests/cartography/fixtures/quality_cases/<CODE>/。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services.spatial_quality_gate import (
    evaluate_quality_gate,
    infer_crs,
)
from app.services.spatial_quality_service import SpatialQualityEngine
from app.services.spatial_repair_pipeline import SpatialRepairPipeline, plan_repair_ops

CASES_ROOT = Path(__file__).parent.parent / "cartography" / "fixtures" / "quality_cases"
CASE_DIRS = sorted(
    d for d in CASES_ROOT.iterdir()
    if d.is_dir() and (d / "expected.json").exists()
)


def _load_case(case_dir: Path):
    data = json.loads((case_dir / "input.geojson").read_text(encoding="utf-8"))
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    return data, expected


def _build_plan(report, verdict, crs_inf, allow_destructive=False):
    """audit 报告 + gate 判定 → 编排计划（裁决输入比率由 issue 计数推导，
    与 gate 的近似口径一致）。"""
    total = max(report.total_features, 1)
    return plan_repair_ops(
        report,
        total_features=report.total_features,
        duplicate_ratio=sum(
            1 for i in report.issues
            if i.code in ("DUPLICATE_GEOMETRY", "DUPLICATE_FEATURE")
        ) / total,
        geometry_mix_ratio=(verdict["profile_extension"]["geometry_mix"]["mix_ratio"]),
        attribute_type_mix_ratio=sum(
            1 for i in report.issues if i.code == "TYPE_INCONSISTENCY"
        ) / total,
        overlap_pair_ratio=sum(
            1 for i in report.issues if i.code == "TOPOLOGY_OVERLAP"
        ) / total,
        outlier_fields=[
            {"field": k, "outlier_ratio": p.get("outlier_ratio", 0.0), "upper": p.get("p99")}
            for k, p in verdict["outlier_fields"].items()
            if p.get("field_policy") not in (None, "none")
        ],
        null_heavy_fields=[
            str(i.details.get("attribute", ""))
            for i in report.issues if i.code == "HIGH_NULL_RATIO"
        ],
        crs_inference=crs_inf,
        allow_destructive=allow_destructive,
    )


def test_matrix_covers_taskbook_codes():
    names = {d.name for d in CASE_DIRS}
    taskbook_14 = {
        "MISSING_CRS", "SUSPICIOUS_CRS", "IMPOSSIBLE_LAT_LON", "NULL_ISLAND",
        "EMPTY_GEOMETRY", "INVALID_GEOMETRY", "RING_CHECK_FAILED",
        "SELF_INTERSECTION", "DUPLICATE_GEOMETRY", "DUPLICATE_FEATURE",
        "DUPLICATE_PRIMARY_KEY", "HIGH_NULL_RATIO", "TOPOLOGY_OVERLAP",
        "NUMERIC_OUTLIER",
    }
    assert taskbook_14 <= names, f"missing taskbook cases: {taskbook_14 - names}"
    assert "TOPOLOGY_GAP" in names  # +1 超集（P6 fix_gaps）


@pytest.mark.cartography
@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=lambda d: d.name)
def test_quality_case_block_or_repair_closed_loop(case_dir: Path):
    data, expected = _load_case(case_dir)
    original_snapshot = copy.deepcopy(data)

    # 1) audit 触发目标诊断码，且级别与矩阵一致。
    # audit 的 crs 参数：fixture 可显式指定；有 crs member 的用例传 4326
    # （audit 的 GIS-15 回退会取 member）；无 member 的传 UNKNOWN —— 否则
    # audit 的 MISSING_CRS/info 分支不会触发（GIS-15 行为）。
    if expected.get("audit_crs"):
        crs_arg = expected["audit_crs"]
    elif isinstance(data.get("crs"), dict):
        crs_arg = "EPSG:4326"
    else:
        crs_arg = "UNKNOWN"
    report = SpatialQualityEngine.audit_dataset(data, crs=crs_arg)
    codes = {i.code for i in report.issues}
    if expected.get("audit_unreachable"):
        # 勘察纪要的诚实断言：该码经标准 GeoJSON 解析不可达（如 ring 自动闭合）。
        assert expected["code"] not in codes, (
            f"{case_dir.name}: {expected['code']} unexpectedly raised — unreachability note stale"
        )
        assert expected["surrogate_code"] in codes
    else:
        assert expected["code"] in codes, f"{case_dir.name}: {expected['code']} not raised (got {codes})"
        levels = {i.code: i.level for i in report.issues}
        assert levels[expected["code"]] == expected["audit_level"]

    # 2) 门禁判定：blocking 拦截（block），error/warning 放行 + 修复计划。
    verdict = evaluate_quality_gate(data)
    assert verdict["verdict"] == expected["gate_verdict"], (
        f"{case_dir.name}: gate verdict {verdict['verdict']} != {expected['gate_verdict']}"
    )
    if expected["gate_verdict"] == "block":
        # 零静默放行：拒绝必须携带修复计划（可为空的诚实计划 + correction_hint）。
        assert verdict["repair_plan"] is not None
        assert verdict["repair_plan"].get("ops") is not None

    # 3) 编排计划：固定顺序 + 期望 op 集。
    crs_inf = infer_crs(data)
    plan = _build_plan(report, verdict, crs_inf)
    for op in expected.get("plan_ops", []):
        assert op in plan.ops, f"{case_dir.name}: expected op {op} in plan (got {plan.ops})"
    for op in expected.get("plan_ops_must_not_contain", []):
        assert op not in plan.ops, (
            f"{case_dir.name}: op {op} must not be planned (no-op honesty)"
        )
    for skipped in expected.get("plan_skipped_ops_contains", []):
        assert any(s["op"] == skipped for s in plan.skipped_ops), (
            f"{case_dir.name}: {skipped} must be honestly skipped (got {plan.skipped_ops})"
        )
    # 固定顺序：plan.ops 必须是 CANONICAL_OP_ORDER 的子序列。
    from app.services.spatial_repair_pipeline import CANONICAL_OP_ORDER

    order_idx = [CANONICAL_OP_ORDER.index(op) for op in plan.ops]
    assert order_idx == sorted(order_idx), f"{case_dir.name}: plan order not canonical"

    # 4) 执行修复（非破坏）：期望的后置条件成立，输入 deepcopy 不受污染。
    repair = expected.get("repair") or {}
    if repair.get("ops"):
        allow = bool(repair.get("allow_destructive"))
        plan2 = _build_plan(report, verdict, crs_inf, allow_destructive=allow)
        # 只跑期望 op 集（case 聚焦；完整默认链由其余单测覆盖）。
        ops = [op for op in plan2.ops if op in repair["ops"]]
        op_params = {op: plan2.op_params.get(op, {}) for op in ops}
        repaired, logs, evidence, lineage = SpatialRepairPipeline.repair_dataset_with_lineage(
            data,
            ops=ops,
            op_params=op_params,
            source_crs=plan2.source_crs or "EPSG:4326",
        )
        # 非破坏红线：原始输入与修复前快照逐字节一致。
        assert data == original_snapshot, f"{case_dir.name}: input mutated by repair!"

        post = expected.get("post") or {}
        feats = repaired.get("features") or []
        if "feature_count" in post:
            assert len(feats) == post["feature_count"], (
                f"{case_dir.name}: post feature_count {len(feats)} != {post['feature_count']}"
            )
        if post.get("code_gone_after_repair"):
            re_report = SpatialQualityEngine.audit_dataset(repaired)
            re_codes = {i.code for i in re_report.issues}
            assert expected["code"] not in re_codes, (
                f"{case_dir.name}: {expected['code']} persists after repair"
            )
        if post.get("bbox_max_abs_xy") is not None:
            from shapely.geometry import shape as _shape

            max_abs = 0.0
            for f in feats:
                g = _shape(f["geometry"])
                if not g.is_empty:
                    max_abs = max(max_abs, max(abs(c) for c in g.bounds))
            assert max_abs <= post["bbox_max_abs_xy"] + 1e-6
        if post.get("flagged"):
            flags = repaired.get("ac04_quality_flags") or {}
            assert flags.get("outlier_feature_indices"), (
                f"{case_dir.name}: outlier flag mode must record flagged indices"
            )
        # 证据与血缘：op 级证据 + per-op 血缘（before/after/area_delta/ts）。
        # 零效应修复（如 flag 模式无命中、不同属性的去重）诚实产出空证据 ——
        # 只有非空时才校验键形（Wave-4 诚实零效应语义）。
        assert isinstance(evidence, list)
        assert isinstance(lineage, list)
        for entry in evidence:
            assert {"op", "features_affected", "failed_count"} <= set(entry)
        for entry in lineage:
            assert {"op", "before_count", "after_count", "area_delta", "evidence", "ts"} <= set(entry)
