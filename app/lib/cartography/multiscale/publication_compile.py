"""出版综合编译器（WP3 纯函数面）—— 纸面契约 → 派生工件 + 冻结计划。

单一编排点（确定性纯函数，零 IO）::

    feature_collection + SourceRevision + PublicationScaleContract
        + （可选）importance 裁决 / 显式 kept_values / sum_fields
        ↓ compile_paper_scale_plan()
    PaperPlanResult
      .plan        paper_scale_plan.v1 计划块（additive 落笔
                   ``layer.scale_profile.paper_plans``，服务层回填工件 ref）
      .artifact    派生 FeatureCollection（内存态；服务层存新 ref）
      .receipts    逐引擎回执链（eliminate → topology / network / aggregate）
      .semantic_checks / .disclosures / .fingerprint

管线（按几何族）：

- polygon：先消除（minimum_mappable_area_m2，硬约束/重点区域保护）→
  再拓扑一致共享边简化（resolve_tolerance_m）—— 顺序不可换：消除改变
  邻接关系，简化在消除后的集合上保持无缝；
- line：等级取舍 + 连通性桥接（network_connectivity_hard 按用途；
  无重要性证据 → 不取舍全保留，诚实披露）；
- point：目标尺度格网聚合（5mm 纸面地面覆盖缺省格；要素数低于
  触发阈值 → 不聚合直通，诚实披露）。

任何一步都不替换统计口径：classification 恒 original_source；原始
分析数据不受综合影响（工件是独立 display ref）。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.lib.cartography.multiscale.importance import (
    ImportanceDerivation,
    ImportanceModel,
)
from app.lib.cartography.multiscale.paper_scale import (
    PublicationScaleContract,
    ground_m_per_mm,
    keep_region_of,
    mandatory_ids_of,
    minimum_mappable_area_m2,
    minimum_mappable_length_m,
    resolve_tolerance_m,
    strategy_hints_of,
    validate_contract,
)
from app.lib.cartography.multiscale.point_aggregate import aggregate_points
from app.lib.cartography.multiscale.polygon_eliminate import (
    eliminate_small_polygons,
)
from app.lib.cartography.multiscale.profile import SourceRevision
from app.lib.cartography.multiscale.qa_pack import (
    qa_summary,
    run_paper_scale_qa,
)
from app.lib.cartography.multiscale.network_select import (
    select_lines_with_connectivity,
)
from app.lib.cartography.multiscale.topology import simplify_polygon_topology
from app.lib.cartography.representation import normalize_geometry_family

#: 计划块版本。
SCHEMA_VERSION = "paper_scale_plan.v1"

#: 点聚合触发阈值（要素数；低于则不聚合 —— 小数据直通诚实统计）。
POINT_AGG_MIN_FEATURES = 30

#: 点聚合缺省格（纸面 mm；服务层/引擎换算为地面米）。
POINT_AGG_CELL_MM = 5.0

#: 用途 → 线等级保留秩（词表序前 K；与 importance.DEFAULT_KEEP_RANKS 的
#: zoom 带缺省独立 —— 纸面出版按用途档位）。
PAPER_KEEP_RANKS_BY_PURPOSE: Dict[str, int] = {
    "overview": 3,
    "reference": 4,
    "planning": 5,
    "navigation": 6,
    "thematic_exhibition": 8,
}


class PaperCompileError(Exception):
    """编译级错误（契约非法等调用方契约错误；工具面转结构化 error）。"""


class PaperPlanResult(BaseModel):
    """编译产物：计划块 + 工件 + 回执链 + QA（可序列化、确定性）。"""

    model_config = ConfigDict(extra="allow")

    plan: Dict[str, Any]
    artifact: Dict[str, Any]
    receipts: Dict[str, Any]
    semantic_checks: List[Dict[str, Any]]
    disclosures: List[str]
    fingerprint: str


def _kept_values_for_paper(
    model: Optional[ImportanceModel],
    purpose: str,
    explicit: Optional[Sequence[Any]],
) -> Optional[List[Any]]:
    """kept_values 裁决：显式 > 重要性模型按用途秩 > None（不取舍）。"""
    if explicit is not None:
        return [str(v) for v in explicit]
    if model is None:
        return None
    k = PAPER_KEEP_RANKS_BY_PURPOSE.get(purpose, 4)
    return list(model.value_order[:k])


def _plan_fingerprint(payload: Dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "psp-sha256:" + hashlib.sha256(blob).hexdigest()[:16]


def compile_paper_scale_plan(
    *,
    layer_id: str,
    feature_collection: Dict[str, Any],
    source: SourceRevision,
    contract: PublicationScaleContract,
    importance: Optional[ImportanceDerivation] = None,
    kept_values: Optional[Sequence[Any]] = None,
    sum_fields: Sequence[str] = (),
    latitude_hint: float = 0.0,
    generated_by: str = "",
) -> PaperPlanResult:
    """纸面契约 × 数据 → 派生工件 + 冻结计划（确定性）。

    契约非法（validate_contract 非空）→ PaperCompileError（调用方契约
    错误；工具面转结构化 error，不静默半计划）。
    """
    errors = validate_contract(contract)
    if errors:
        raise PaperCompileError(f"paper scale contract invalid: {errors}")
    family = normalize_geometry_family(source.geometry_family) \
        if source.geometry_family else "unknown"
    features = list((feature_collection or {}).get("features") or [])
    original_count = len(features)

    tolerance_m = resolve_tolerance_m(contract)
    min_area_m2 = minimum_mappable_area_m2(contract)
    min_length_m = minimum_mappable_length_m(contract)
    hints = strategy_hints_of(contract)
    mandatory = frozenset(mandatory_ids_of(contract))
    region = keep_region_of(contract)
    budget_ratio = float(contract.error_budget.max_area_error_ratio)

    pipeline: List[Dict[str, Any]] = []
    receipts: Dict[str, Any] = {}
    disclosures: List[str] = []
    artifact_features = [copy.copy(f) for f in features]

    m_per_mm = ground_m_per_mm(int(contract.target_denominator))

    # ── polygon：消除 → 拓扑一致共享边简化（顺序不可换）──
    if family == "polygon":
        elim = eliminate_small_polygons(
            artifact_features, min_area_m2=min_area_m2,
            latitude_hint=latitude_hint, protected_ids=mandatory,
            keep_region=region, sum_fields=list(sum_fields))
        pipeline.append({
            "step": "eliminate_small_polygons",
            "engine": elim.receipt["engine"],
            "eliminated": elim.receipt["eliminated_count"],
            "isolated_kept": elim.receipt["isolated_kept"],
            "display_divergence": elim.receipt["display_divergence"],
        })
        receipts["eliminate"] = elim.receipt
        disclosures.extend(elim.receipt["disclosures"])
        topo = simplify_polygon_topology(
            elim.feature_collection["features"], tolerance_m=tolerance_m,
            latitude_hint=latitude_hint,
            max_area_error_ratio=budget_ratio, mandatory_ids=mandatory)
        pipeline.append({
            "step": "topology_simplify",
            "engine": topo.receipt["engine"],
            "simplified": topo.receipt["geometry_simplified"],
            "fallback": topo.receipt["geometry_fallback"],
            "chains_protected": topo.receipt["chains_protected_original"],
            "partial_vertex_match": topo.receipt["partial_vertex_match"],
        })
        receipts["topology"] = topo.receipt
        disclosures.extend(topo.receipt["disclosures"])
        artifact_features = topo.feature_collection["features"]

    # ── line：等级取舍 + 连通性桥接 ──
    elif family == "line":
        model = importance.selected if importance is not None else None
        sel_values = _kept_values_for_paper(model, contract.purpose,
                                            kept_values)
        if sel_values and model is not None:
            net = select_lines_with_connectivity(
                artifact_features,
                importance_field=model.field, kept_values=sel_values,
                mandatory_ids=mandatory,
                # 硬约束（navigation）不设桥接预算；软用途设帽超限诚实披露
                max_bridges=(None if hints["network_connectivity_hard"]
                             else 16),
            )
            pipeline.append({
                "step": "network_select",
                "engine": net.receipt["engine"],
                "kept": net.receipt["kept_count"],
                "suppressed": net.receipt["suppressed_count"],
                "bridges_added": net.receipt["bridges_added"],
                "fully_connected": net.receipt["fully_connected"],
            })
            receipts["network_select"] = net.receipt
            disclosures.extend(net.receipt["disclosures"])
            artifact_features = net.feature_collection["features"]
        else:
            disclosures.append(
                "无可信重要性字段且未显式给 kept_values —— 线层不取舍全保留"
                "（不伪造主干）")
            pipeline.append({"step": "network_select", "skipped":
                             "no_importance_evidence"})

    # ── point：目标尺度格网聚合（低于触发阈值直通）──
    elif family == "point":
        if original_count >= POINT_AGG_MIN_FEATURES:
            cell_m = POINT_AGG_CELL_MM * m_per_mm
            agg = aggregate_points(
                artifact_features, cell_size_m=cell_m,
                latitude_hint=latitude_hint,
                sum_fields=list(sum_fields), protected_ids=mandatory)
            pipeline.append({
                "step": "aggregate_points",
                "engine": agg.receipt["engine"],
                "representatives": agg.receipt["representative_count"],
                "aggregated": agg.receipt["aggregated_count"],
                "max_members_per_cell": agg.receipt["max_members_per_cell"],
            })
            receipts["aggregate"] = agg.receipt
            disclosures.extend(agg.receipt["disclosures"])
            artifact_features = agg.feature_collection["features"]
        else:
            disclosures.append(
                f"点要素 {original_count} 条低于聚合触发阈值 "
                f"{POINT_AGG_MIN_FEATURES} —— 不聚合直通（诚实统计）")
            pipeline.append({"step": "aggregate_points", "skipped":
                             "below_threshold"})

    artifact_fc = {"type": "FeatureCollection", "features": artifact_features}

    # ── QA（独立 oracle，非引擎自述）──
    checks = run_paper_scale_qa(
        contract, features, artifact_features, geometry_family=family,
        sum_fields=list(sum_fields), latitude_hint=latitude_hint,
        min_area_m2=min_area_m2 if family == "polygon" else None,
        min_length_m=min_length_m if family == "line" else None,
        network_connectivity_hard=bool(hints["network_connectivity_hard"]),
    )
    summary = qa_summary(checks)

    plan: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "layer_id": layer_id,
        "contract": contract.model_dump(mode="json", exclude_none=True),
        "source": source.model_dump(mode="json", exclude_none=True),
        "family": family,
        "status": "planned",
        "pipeline": pipeline,
        "stats": {
            "original_count": original_count,
            "artifact_count": len(artifact_features),
            "target_denominator": int(contract.target_denominator),
            "tolerance_m": tolerance_m,
            "min_area_m2": min_area_m2 if family == "polygon" else None,
            "min_length_m": min_length_m if family == "line" else None,
            "classification_basis": "original_source",
        },
        "qa_summary": summary,
        "purpose": contract.purpose,
        "equivalent_zoom_note": (
            "纸面比例尺经 paper_scale.equivalent_display_zoom 单点换算，"
            "工件窗口由服务层冻结（export 按 zoom 裁决读取）"),
    }

    fingerprint_payload = {
        "layer_id": layer_id,
        "contract": plan["contract"],
        "source": plan["source"],
        "family": family,
        "pipeline": pipeline,
        "stats": plan["stats"],
    }
    fingerprint = _plan_fingerprint(fingerprint_payload)
    plan["fingerprint"] = fingerprint

    return PaperPlanResult(
        plan=plan,
        artifact=artifact_fc,
        receipts=receipts,
        semantic_checks=checks,
        disclosures=disclosures[:12],
        fingerprint=fingerprint,
    )


def plan_with_artifact_ref(
    plan: Dict[str, Any],
    *,
    artifact_ref: str,
    artifact_source_id: str,
    window: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """工件落库后回填（深拷贝不改原计划；status → frozen）。"""
    out = copy.deepcopy(plan)
    out["status"] = "frozen"
    out["artifact_ref"] = artifact_ref
    out["artifact_source_id"] = artifact_source_id
    if isinstance(window, dict):
        out["export_window"] = dict(window)
    return out


__all__ = [
    "SCHEMA_VERSION",
    "POINT_AGG_MIN_FEATURES",
    "POINT_AGG_CELL_MM",
    "PAPER_KEEP_RANKS_BY_PURPOSE",
    "PaperCompileError",
    "PaperPlanResult",
    "compile_paper_scale_plan",
    "plan_with_artifact_ref",
]
