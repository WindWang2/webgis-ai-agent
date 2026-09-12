"""
Safe Spatial Repair Pipeline: Performs non-destructive remediation operations on spatial datasets.
Produces clean Derived GeoJSON and an audit log of changes.

ADR-0153（adaptive-cartography/04）扩展：
- ``plan_repair_ops``：audit 诊断码 → 固定顺序 op 序列的**执行调度器**
  （此前 repair_planning 的提案是 plan-only，全仓没有「提案 → pipeline op」
  的调度环节）；破坏性 op（deduplicate / normalize / attribute 归一 / 删行）
  由裁决阈值（重复率 / 类型混杂度 / 重叠率）开启并记证据，绝不无条件跑。
- 新 op：``fix_topology_overlap``（拓扑重叠，difference 消除或标记）、
  ``fix_gaps``（缝隙，容差可配的 pairwise snap 或标记）、
  ``drop_outliers_or_flag``（离群标记/剔除，默认只标记）、
  ``attribute_drop_or_flag``（高空值率字段标记/删列，默认只标记）。
- ``repair_dataset_with_lineage``：per-op 修复血缘
  ``{op, before_count, after_count, area_delta, evidence[], ts}`` —— 与
  ``data_quality.repair_execution.build_repair_evidence`` 的 op 级证据键
  （op/features_affected/failed_count）同形对齐，不新建第二套 provenance。
- 非破坏红线不变：输入 deepcopy，源对象永不被污染。
"""

import copy
import datetime as _dt
import logging
import math
from typing import List, Dict, Any, Tuple, Optional
from shapely.geometry import (
    shape,
    mapping,
    Polygon,
    MultiPolygon,
    MultiLineString,
    MultiPoint,
)
from shapely.validation import make_valid
import shapely

logger = logging.getLogger(__name__)

#: Wave-4（审计 08 §4.2）：per-op 证据上限 —— 修复证据是有界事实（哪些操作
#: 影响了多少要素、失败多少），不是日志转储。
_MAX_EVIDENCE_OPS = 16

#: 证据输出的规范操作顺序（确定性：同执行 ⇒ 同证据序）。
_EVIDENCE_OP_ORDER = (
    "remove_empty",
    "make_valid",
    "normalize_geometry_type",
    "crs_transform",
    "snap_within_tolerance",
    "deduplicate",
    "attribute_type_normalization",
    # ADR-0153 新 op 追加在既有 7 op 之后（消费方按名取，序只保证确定性）。
    "fix_topology_overlap",
    "fix_gaps",
    "drop_outliers_or_flag",
    "attribute_drop_or_flag",
)

#: ADR-0153 编排器（plan_repair_ops）产出的固定 op 顺序 —— 任务书 §2 P2
#: 规定的 7 op 顺序 + 新 op 追加段。**只允许按此序产出，禁止自由编排**；
#: 执行器内部的落点（dataset 级 op 在 per-feature 循环之后按依赖安全序执行：
#: 去重 → 重叠 → 缝隙 → 属性归一 → 空值标记 → 离群标记）是执行细节，
#: 与本计划序的偏差在 RepairOpPlan.execution_note 中披露。
CANONICAL_OP_ORDER = (
    "remove_empty",
    "make_valid",
    "normalize_geometry_type",
    "deduplicate",
    "crs_transform",
    "snap_within_tolerance",
    "attribute_type_normalization",
    "fix_topology_overlap",
    "fix_gaps",
    "drop_outliers_or_flag",
    "attribute_drop_or_flag",
)

#: 破坏性 op 集合（改变要素数 / 改写属性类型 / 删列 / 删行语义）——
#: 默认关闭，仅由裁决阈值或显式 allow_destructive 开启，且必须记证据。
DESTRUCTIVE_OPS = frozenset({
    "deduplicate",
    "normalize_geometry_type",
    "attribute_type_normalization",
    "attribute_drop_or_flag",
})

#: 裁决阈值（adjudication）：超阈才开启对应破坏性 op。
DEDUP_RATIO_THRESHOLD = 0.05          # 要素重复率 ≥5% → deduplicate 开
GEOMETRY_MIX_RATIO_THRESHOLD = 0.2    # 次类型占比 ≥20% → normalize 开
ATTR_TYPE_MIX_RATIO_THRESHOLD = 0.3   # 属性类型混杂度 ≥30% → attribute 归一开
OVERLAP_PAIR_RATIO_THRESHOLD = 0.01   # 重叠对占比 ≥1% → difference 消除（否则只标记）
OUTLIER_DROP_RATIO_CAP = 0.02         # 离群率 ≤2% 才允许 drop（否则降级为标记）

#: 拓扑修复 pairwise 预算（对齐 audit 的 #539 有界纪律）。
_MAX_TOPOLOGY_FIX_PAIRS = 200

#: flag 模式写入载荷顶层的唯一标记键（有界、可整体剥离）。
_QUALITY_FLAG_KEY = "ac04_quality_flags"


def _utc_ts() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _area_total(features: List[Dict[str, Any]]) -> float:
    """数据集多边形总面积（lineage area_delta 的统一量纲）。"""
    total = 0.0
    for f in features:
        try:
            g = shape(f.get("geometry")) if isinstance(f, dict) else None
            if g is not None and not g.is_empty:
                total += float(g.area)
        except Exception:  # noqa: BLE001 — 面积统计是增值证据，绝不阻断修复
            continue
    return total


# ─────────────────────────────────────────────────────────────────────────────
# ADR-0153：op 编排器（audit 诊断码 → 固定顺序可执行 op 序列）
# ─────────────────────────────────────────────────────────────────────────────
class RepairOpPlan:
    """plan_repair_ops 的产物：固定顺序 op 序列 + 裁决记录 + 为何修。

    与 ``repair_planning.RepairProposal``（REMEDIATION_OPS 提案词表，plan-only）
    的关系：提案层回答「哪些确定性修复**存在**」，本计划层回答「对这个
    数据集，pipeline **应该按什么顺序跑哪些 op、参数是什么、为什么**」。
    两层词表不同源（提案 = REMEDIATION_OPS；计划 = pipeline op 名），映射
    由 ``_CODE_TO_OP`` 单点维护。
    """

    def __init__(
        self,
        *,
        ops: List[str],
        op_params: Dict[str, Dict[str, Any]],
        reasons: Dict[str, List[str]],
        destructive_decisions: Dict[str, str],
        advisories: List[Dict[str, Any]],
        skipped_ops: List[Dict[str, str]],
        source_crs: Optional[str],
        target_crs: str,
        tolerance: float,
    ) -> None:
        self.ops = ops
        self.op_params = op_params
        self.reasons = reasons
        #: op → 裁决结论（"enabled_by_adjudication" | "flag_only" | "disabled_below_threshold" ...）
        self.destructive_decisions = destructive_decisions
        self.advisories = advisories
        self.skipped_ops = skipped_ops
        self.source_crs = source_crs
        self.target_crs = target_crs
        self.tolerance = tolerance

    @property
    def execution_note(self) -> str:
        return (
            "plan order is canonical (CANONICAL_OP_ORDER); the executor places "
            "dataset-level ops after the per-feature loop in dependency-safe "
            "order (dedup -> overlap -> gaps -> attribute normalization -> "
            "null flags -> outlier flags); crs_transform runs before topology "
            "ops so tolerances are interpreted in TARGET CRS units (GIS-17)."
        )

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "ops": [str(op)[:32] for op in self.ops],
            "op_params": {
                str(k)[:32]: {str(pk)[:32]: pv for pk, pv in list(v.items())[:6]}
                for k, v in list(self.op_params.items())[:12]
            },
            "reasons": {str(k)[:32]: [str(c)[:32] for c in v[:8]] for k, v in list(self.reasons.items())[:12]},
            "destructive_decisions": {str(k)[:32]: str(v)[:40] for k, v in list(self.destructive_decisions.items())[:12]},
            "advisories": self.advisories[:16],
            "skipped_ops": self.skipped_ops[:16],
            "source_crs": self.source_crs,
            "target_crs": self.target_crs,
            "tolerance": self.tolerance,
            "execution_note": self.execution_note,
        }

    def pipeline_kwargs(self) -> Dict[str, Any]:
        """直接可传给 SpatialRepairPipeline.repair_dataset_with_lineage 的参数。"""
        return {
            "ops": list(self.ops),
            "op_params": dict(self.op_params),
            "source_crs": self.source_crs or "EPSG:4326",
            "target_crs": self.target_crs,
            "tolerance": self.tolerance,
        }


# 诊断码 → 计划层触发（op 候选）。单一事实源；注释 = 只报不修缺口的收口方式。
_CODE_TO_OP: Dict[str, Tuple[str, ...]] = {
    "EMPTY_GEOMETRY": ("remove_empty",),
    "NULL_ISLAND": ("remove_empty",),  # 仅当 allow_destructive：drop_zero_coordinates
    "INVALID_GEOMETRY": ("make_valid",),
    "SELF_INTERSECTION": ("make_valid",),
    "RING_CHECK_FAILED": ("make_valid",),
    "DUPLICATE_GEOMETRY": ("deduplicate",),
    "DUPLICATE_FEATURE": ("deduplicate",),
    "DUPLICATE_PRIMARY_KEY": ("deduplicate",),  # 语义收窄：pipeline 键 = geom+attrs
    "MISSING_CRS": ("crs_transform",),
    "SUSPICIOUS_CRS": ("crs_transform",),
    "IMPOSSIBLE_LAT_LON": ("crs_transform",),
    "EXTREME_COORDINATES": ("crs_transform", "make_valid"),
    "TOPOLOGY_GAP": ("fix_gaps", "snap_within_tolerance"),
    "NEAR_DUPLICATE_VERTICES": ("fix_gaps", "snap_within_tolerance"),
    "TOPOLOGY_OVERLAP": ("fix_topology_overlap",),
    "TYPE_INCONSISTENCY": ("attribute_type_normalization",),
    "HIGH_NULL_RATIO": ("attribute_drop_or_flag",),
    "NUMERIC_OUTLIER": ("drop_outliers_or_flag",),
    "INCONSISTENT_SCHEMA": ("attribute_type_normalization",),
}


def plan_repair_ops(
    audit_result: Any,
    *,
    total_features: int = 0,
    duplicate_ratio: float = 0.0,
    geometry_mix_ratio: float = 0.0,
    attribute_type_mix_ratio: float = 0.0,
    overlap_pair_ratio: float = 0.0,
    outlier_fields: Optional[List[Dict[str, Any]]] = None,
    null_heavy_fields: Optional[List[str]] = None,
    crs_inference: Optional[Dict[str, Any]] = None,
    declared_crs: Optional[str] = None,
    target_crs: str = "EPSG:4326",
    tolerance: float = 1e-5,
    allow_destructive: bool = False,
) -> RepairOpPlan:
    """audit 报告 → 固定顺序可执行修复计划（plan-only；本函数绝不执行）。

    破坏性 op 的裁决（任务书 §2 P2）：默认关闭；重复率 / 类型混杂度 /
    重叠率超阈 **或** 调用方显式 ``allow_destructive=True`` 时开启，并记
    裁决结论入 ``destructive_decisions``。人工 CRS 声明优先于推断
    （``declared_crs`` 非空时 crs_transform 不采用 ``crs_inference``）。
    确定性：同输入 ⇒ 同计划。
    """
    codes: List[str] = []
    issues = getattr(audit_result, "issues", None)
    if issues is None and isinstance(audit_result, dict):
        issues = audit_result.get("issues")
    for issue in issues or []:
        code = str(getattr(issue, "code", None) or (issue.get("code") if isinstance(issue, dict) else ""))
        if code and code not in codes:
            codes.append(code)

    inference = dict(crs_inference or {})
    inferred_crs = inference.get("crs")
    destructive_decisions: Dict[str, str] = {}
    advisories: List[Dict[str, Any]] = []
    skipped_ops: List[Dict[str, str]] = []
    reasons: Dict[str, List[str]] = {}

    def _reason(op: str, code: str) -> None:
        reasons.setdefault(op, [])
        if code not in reasons[op]:
            reasons[op].append(code)

    candidate_ops: List[str] = []
    for code in codes:
        for op in _CODE_TO_OP.get(code, ()):
            if op not in candidate_ops:
                candidate_ops.append(op)
            _reason(op, code)
    for op in candidate_ops:
        reasons.setdefault(op, [])

    # ── 破坏性裁决 ────────────────────────────────────────────────────────
    # deduplicate：重复率超阈 或 显式允许。
    if "deduplicate" in candidate_ops:
        if duplicate_ratio >= DEDUP_RATIO_THRESHOLD:
            destructive_decisions["deduplicate"] = (
                f"enabled_by_adjudication(duplicate_ratio={duplicate_ratio:.3f}"
                f">={DEDUP_RATIO_THRESHOLD})"
            )
        elif allow_destructive:
            destructive_decisions["deduplicate"] = "enabled_by_explicit_allow"
        else:
            destructive_decisions["deduplicate"] = (
                f"disabled_below_threshold(duplicate_ratio={duplicate_ratio:.3f})"
            )
            candidate_ops.remove("deduplicate")
            skipped_ops.append({
                "op": "deduplicate",
                "why": f"duplicate_ratio {duplicate_ratio:.3f} < {DEDUP_RATIO_THRESHOLD} "
                       "and destructive not explicitly allowed",
            })
    # normalize_geometry_type：几何类型混杂度裁决。
    if "normalize_geometry_type" in candidate_ops or geometry_mix_ratio >= GEOMETRY_MIX_RATIO_THRESHOLD:
        if geometry_mix_ratio >= GEOMETRY_MIX_RATIO_THRESHOLD:
            if "normalize_geometry_type" not in candidate_ops:
                candidate_ops.append("normalize_geometry_type")
                _reason("normalize_geometry_type", "GEOMETRY_TYPE_MIX")
            destructive_decisions["normalize_geometry_type"] = (
                f"enabled_by_adjudication(geometry_mix_ratio={geometry_mix_ratio:.3f})"
            )
        elif allow_destructive:
            destructive_decisions["normalize_geometry_type"] = "enabled_by_explicit_allow"
        else:
            destructive_decisions["normalize_geometry_type"] = (
                f"disabled_below_threshold(geometry_mix_ratio={geometry_mix_ratio:.3f})"
            )
            if "normalize_geometry_type" in candidate_ops:
                candidate_ops.remove("normalize_geometry_type")
            skipped_ops.append({
                "op": "normalize_geometry_type",
                "why": f"geometry_mix_ratio {geometry_mix_ratio:.3f} < "
                       f"{GEOMETRY_MIX_RATIO_THRESHOLD} and destructive not explicitly allowed",
            })
    # attribute_type_normalization：属性类型混杂裁决。
    if "attribute_type_normalization" in candidate_ops:
        if attribute_type_mix_ratio >= ATTR_TYPE_MIX_RATIO_THRESHOLD:
            destructive_decisions["attribute_type_normalization"] = (
                "enabled_by_adjudication(attribute_type_mix_ratio="
                f"{attribute_type_mix_ratio:.3f})"
            )
        elif allow_destructive:
            destructive_decisions["attribute_type_normalization"] = "enabled_by_explicit_allow"
        else:
            destructive_decisions["attribute_type_normalization"] = (
                f"disabled_below_threshold(attribute_type_mix_ratio="
                f"{attribute_type_mix_ratio:.3f})"
            )
            candidate_ops.remove("attribute_type_normalization")
            skipped_ops.append({
                "op": "attribute_type_normalization",
                "why": f"attribute_type_mix_ratio {attribute_type_mix_ratio:.3f} < "
                       f"{ATTR_TYPE_MIX_RATIO_THRESHOLD} and destructive not explicitly allowed",
            })

    # ── CRS：人工声明优先，推断兜底（P3 去人工化）─────────────────────────
    source_crs: Optional[str] = None
    if "crs_transform" in candidate_ops:
        if declared_crs:
            source_crs = declared_crs
            destructive_decisions["crs_transform"] = "source_crs_from_manual_declaration"
        elif inferred_crs and inferred_crs != target_crs:
            source_crs = str(inferred_crs)
            confidence = str(inference.get("confidence", "low"))
            destructive_decisions["crs_transform"] = (
                f"source_crs_inferred({inferred_crs}, confidence={confidence})"
            )
            if inference.get("low_confidence"):
                advisories.append({
                    "code": "CRS_INFERENCE_LOW_CONFIDENCE",
                    "level": "info",
                    "message": (
                        "CRS 推断置信度低：已按推断源 CRS 计划重投影，建议人工核对。"
                    ),
                    "evidence": {
                        k: inference.get(k) for k in ("method", "bbox", "coord_range")
                        if inference.get(k) is not None
                    },
                })
        else:
            # 推断结果 = 目标 CRS（或缺推断）：重投影是 no-op，诚实剔除。
            destructive_decisions["crs_transform"] = "skipped_no_op(source==target or no inference)"
            candidate_ops.remove("crs_transform")
            skipped_ops.append({
                "op": "crs_transform",
                "why": "inferred/declared CRS equals target CRS; reproject would be a no-op",
            })
            source_crs = target_crs

    # ── 新 op 的模式裁决（默认只标记 / 只计划）────────────────────────────
    op_params: Dict[str, Dict[str, Any]] = {}
    if "remove_empty" in candidate_ops and "NULL_ISLAND" in codes:
        if allow_destructive:
            op_params["remove_empty"] = {"drop_zero_coordinates": True}
            destructive_decisions["remove_empty.drop_zero_coordinates"] = (
                "enabled_by_explicit_allow(NULL_ISLAND)"
            )
        else:
            advisories.append({
                "code": "NULL_ISLAND",
                "level": "warning",
                "message": "检出 Null Island (0,0) 要素：疑似缺失值填充；默认只标记不删，"
                           "显式 allow_destructive 后 remove_empty 将携带 drop_zero_coordinates。",
            })
    if "fix_topology_overlap" in candidate_ops:
        if overlap_pair_ratio >= OVERLAP_PAIR_RATIO_THRESHOLD:
            op_params["fix_topology_overlap"] = {"mode": "difference"}
            destructive_decisions["fix_topology_overlap"] = (
                f"mode=difference(overlap_pair_ratio={overlap_pair_ratio:.4f})"
            )
        else:
            op_params["fix_topology_overlap"] = {"mode": "flag"}
            destructive_decisions["fix_topology_overlap"] = "mode=flag(below_threshold)"
    if "fix_gaps" in candidate_ops:
        if allow_destructive:
            op_params["fix_gaps"] = {"mode": "snap", "tolerance": tolerance}
            destructive_decisions["fix_gaps"] = "mode=snap(enabled_by_explicit_allow)"
        else:
            op_params["fix_gaps"] = {"mode": "flag", "tolerance": tolerance}
            destructive_decisions["fix_gaps"] = "mode=flag(default)"
    if "drop_outliers_or_flag" in candidate_ops:
        fields = [dict(f) for f in (outlier_fields or [])][:8]
        max_ratio = max((float(f.get("outlier_ratio", 0.0)) for f in fields), default=0.0)
        if allow_destructive and fields and max_ratio <= OUTLIER_DROP_RATIO_CAP:
            op_params["drop_outliers_or_flag"] = {"mode": "drop", "fields": fields}
            destructive_decisions["drop_outliers_or_flag"] = (
                f"mode=drop(outlier_ratio={max_ratio:.4f}<={OUTLIER_DROP_RATIO_CAP})"
            )
        else:
            op_params["drop_outliers_or_flag"] = {"mode": "flag", "fields": fields}
            destructive_decisions["drop_outliers_or_flag"] = (
                "mode=flag(default; drop requires allow_destructive and "
                f"outlier_ratio<={OUTLIER_DROP_RATIO_CAP})"
            )
        advisories.append({
            "code": "OUTLIER_POLICY_SUGGESTION",
            "level": "info",
            "message": "离群值默认只标记；裁剪（clip_p99 等）由 symbology 线按 "
                       "outlier_policy 契约执行，本线不裁剪数据。",
            "fields": fields,
        })
    if "attribute_drop_or_flag" in candidate_ops:
        heavy = [str(f)[:64] for f in (null_heavy_fields or [])][:8]
        if allow_destructive and heavy:
            op_params["attribute_drop_or_flag"] = {"mode": "drop_column", "fields": heavy}
            destructive_decisions["attribute_drop_or_flag"] = "mode=drop_column(enabled_by_explicit_allow)"
        else:
            op_params["attribute_drop_or_flag"] = {"mode": "flag", "fields": heavy}
            destructive_decisions["attribute_drop_or_flag"] = "mode=flag(default)"

    ops = [op for op in CANONICAL_OP_ORDER if op in candidate_ops]
    unknown = [op for op in candidate_ops if op not in CANONICAL_OP_ORDER]
    if unknown:  # pragma: no cover — 词表漂移防御
        raise ValueError(f"plan_repair_ops produced ops outside CANONICAL_OP_ORDER: {unknown}")

    return RepairOpPlan(
        ops=ops,
        op_params=op_params,
        reasons=reasons,
        destructive_decisions=destructive_decisions,
        advisories=advisories,
        skipped_ops=skipped_ops,
        source_crs=source_crs,
        target_crs=target_crs,
        tolerance=tolerance,
    )


class SpatialRepairPipeline:
    @staticmethod
    def repair_dataset(
        geojson_data: Dict[str, Any],
        ops: Optional[List[str]] = None,
        tolerance: float = 1e-5,
        operations: Optional[List[str]] = None,
        source_crs: str = "EPSG:4326",
        target_crs: str = "EPSG:4326",
        op_params: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Remediates GeoJSON dataset without mutating original input.
        Returns (repaired_geojson, audit_logs).

        Note: when ``crs_transform`` is active, ``snap_within_tolerance`` is
        applied AFTER reprojection, so ``tolerance`` is interpreted in the
        TARGET CRS units (e.g. meters for a projected CRS, degrees for
        EPSG:4326) — not in the source CRS units.
        """
        repaired, logs, _evidence, _lineage = SpatialRepairPipeline._repair_impl(
            geojson_data,
            ops=ops,
            tolerance=tolerance,
            operations=operations,
            source_crs=source_crs,
            target_crs=target_crs,
            op_params=op_params,
        )
        return repaired, logs

    @staticmethod
    def repair_dataset_detailed(
        geojson_data: Dict[str, Any],
        ops: Optional[List[str]] = None,
        tolerance: float = 1e-5,
        operations: Optional[List[str]] = None,
        source_crs: str = "EPSG:4326",
        target_crs: str = "EPSG:4326",
        op_params: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]:
        """
        Same remediation as :meth:`repair_dataset`, additionally returning
        bounded per-operation evidence: ``[{op, features_affected,
        failed_count}, ...]`` (≤16 ops). Wave-4 repair evidence consumers
        (``repair_evidence`` lineage column) use this — the op-level audit
        log list itself remains ephemeral/return-only.
        """
        repaired, logs, evidence, _lineage = SpatialRepairPipeline._repair_impl(
            geojson_data,
            ops=ops,
            tolerance=tolerance,
            operations=operations,
            source_crs=source_crs,
            target_crs=target_crs,
            op_params=op_params,
        )
        return repaired, logs, evidence

    @staticmethod
    def repair_dataset_with_lineage(
        geojson_data: Dict[str, Any],
        ops: Optional[List[str]] = None,
        tolerance: float = 1e-5,
        operations: Optional[List[str]] = None,
        source_crs: str = "EPSG:4326",
        target_crs: str = "EPSG:4326",
        op_params: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """ADR-0153：同 :meth:`repair_dataset_detailed`，额外返回修复血缘。

        lineage 条目：``{op, before_count, after_count, area_delta,
        evidence[], ts}`` —— ``evidence[]`` 复用 Wave-4 op 级证据键
        （op/features_affected/failed_count，对齐
        ``data_quality.repair_execution.build_repair_evidence``），不新建
        第二套 provenance 结构。回放语义：按 op 序重放即可回答「为何修、
        影响了多少要素、面积变化多少」。
        """
        return SpatialRepairPipeline._repair_impl(
            geojson_data,
            ops=ops,
            tolerance=tolerance,
            operations=operations,
            source_crs=source_crs,
            target_crs=target_crs,
            op_params=op_params,
        )

    @staticmethod
    def _repair_impl(
        geojson_data: Dict[str, Any],
        ops: Optional[List[str]] = None,
        tolerance: float = 1e-5,
        operations: Optional[List[str]] = None,
        source_crs: str = "EPSG:4326",
        target_crs: str = "EPSG:4326",
        op_params: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """统一执行体：修复 + per-op 证据 + 修复血缘（非破坏，deepcopy 输入）。"""
        params_by_op: Dict[str, Dict[str, Any]] = {
            str(k): dict(v) if isinstance(v, dict) else {}
            for k, v in (op_params or {}).items()
        }
        active_ops = ops if ops is not None else (operations or ["make_valid", "remove_empty"])
        _drop_zero_coords = bool(params_by_op.get("remove_empty", {}).get("drop_zero_coordinates"))

        # NON-DESTRUCTIVE: Deep copy input GeoJSON
        repaired_geojson = copy.deepcopy(geojson_data)
        features = repaired_geojson.get("features", [])
        if not isinstance(features, list) and repaired_geojson.get("type") == "Feature":
            features = [repaired_geojson]
        _input_count = len(features)

        # Wave-4: per-op counters — evidence must reflect what actually ran,
        # including zero-effect and failed executions (honest, never silent).
        op_stats: Dict[str, Dict[str, int]] = {}
        # ADR-0153 lineage: per-op area deltas accumulated inline (loop ops)
        # or measured at stage boundaries (dataset-level ops).
        area_delta_by_op: Dict[str, float] = {}

        def _bump(op: str, affected: int = 0, failed: int = 0) -> None:
            stat = op_stats.setdefault(op, {"features_affected": 0, "failed_count": 0})
            stat["features_affected"] += affected
            stat["failed_count"] += failed

        logs: List[str] = []
        cleaned_features = []

        # ----------------------------------------------------
        # Operation: crs_transform
        # ----------------------------------------------------
        transformer = None
        crs_transform_failures = 0
        if "crs_transform" in active_ops and source_crs != target_crs:
            try:
                from pyproj import Transformer
                transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
            except Exception as e:
                logs.append(f"crs_transform: Failed to initialize transformer from {source_crs} to {target_crs}: {e}")
                _bump("crs_transform", failed=1)

        for idx, feat in enumerate(features):
            if not isinstance(feat, dict):
                logs.append(f"Feature {idx}: Skipped non-dictionary record")
                continue

            geom_raw = feat.get("geometry")

            # ----------------------------------------------------
            # Operation: remove_empty
            # ----------------------------------------------------
            if geom_raw is None:
                if "remove_empty" in active_ops:
                    logs.append(f"remove_empty: Removed feature at index {idx} with null geometry")
                    _bump("remove_empty", affected=1)
                    continue
                else:
                    cleaned_features.append(feat)
                    continue

            try:
                geom = shape(geom_raw)
            except Exception as e:
                logs.append(f"Feature {idx}: Skipped due to unparseable geometry: {e}")
                continue

            if geom.is_empty:
                if "remove_empty" in active_ops:
                    logs.append(f"remove_empty: Removed feature at index {idx} with empty geometry")
                    _bump("remove_empty", affected=1)
                    continue
                else:
                    cleaned_features.append(feat)
                    continue

            # ----------------------------------------------------
            # Operation: remove_empty (drop_zero_coordinates, ADR-0153)
            # ----------------------------------------------------
            # Null Island 扩展：centroid 落在 (0,0)±1e-7（与 audit 同判据）的
            # 要素视为缺失值填充；仅在 plan 裁决（allow_destructive）显式
            # 开启时剔除，默认保留并走 advisory。
            if (
                _drop_zero_coords
                and "remove_empty" in active_ops
            ):
                centroid = geom.centroid
                if abs(centroid.x) < 1e-7 and abs(centroid.y) < 1e-7:
                    area_delta_by_op["remove_empty"] = (
                        area_delta_by_op.get("remove_empty", 0.0) - float(geom.area)
                    )
                    logs.append(
                        f"remove_empty: Removed feature at index {idx} with Null Island (0,0) centroid"
                    )
                    _bump("remove_empty", affected=1)
                    continue

            # ----------------------------------------------------
            # Operation: make_valid
            # ----------------------------------------------------
            if "make_valid" in active_ops and not geom.is_valid:
                try:
                    area_before = float(geom.area) if hasattr(geom, "area") else 0.0
                    geom = make_valid(geom)
                    area_after = float(geom.area) if hasattr(geom, "area") else 0.0
                    if area_after != area_before:
                        area_delta_by_op["make_valid"] = (
                            area_delta_by_op.get("make_valid", 0.0) + (area_after - area_before)
                        )
                    logs.append(f"make_valid: Repaired invalid geometry at feature index {idx}")
                    _bump("make_valid", affected=1)
                except Exception as e:
                    logs.append(f"make_valid: Failed to repair feature index {idx}: {e}")
                    _bump("make_valid", failed=1)

            # ----------------------------------------------------
            # Operation: normalize_geometry_type
            # ----------------------------------------------------
            if "normalize_geometry_type" in active_ops:
                old_type = geom.geom_type
                if old_type == "Polygon":
                    geom = MultiPolygon([geom])
                    logs.append(f"normalize_geometry_type: Converted Polygon to MultiPolygon at feature index {idx}")
                    _bump("normalize_geometry_type", affected=1)
                elif old_type == "LineString":
                    geom = MultiLineString([geom])
                    logs.append(f"normalize_geometry_type: Converted LineString to MultiLineString at feature index {idx}")
                    _bump("normalize_geometry_type", affected=1)
                elif old_type == "Point":
                    geom = MultiPoint([geom])
                    logs.append(f"normalize_geometry_type: Converted Point to MultiPoint at feature index {idx}")
                    _bump("normalize_geometry_type", affected=1)

            # ----------------------------------------------------
            # Operation: crs_transform (Coordinate Reprojection)
            # ----------------------------------------------------
            # Run BEFORE snapping so grid precision is interpreted in the
            # TARGET CRS units (audit GIS-17). Snapping in source units first
            # (e.g. degrees) then reprojecting would apply a geodesically
            # meaningless grid and leave vertices that snap to the wrong
            # coordinate in the target CRS.
            if transformer is not None:
                try:
                    from shapely.ops import transform
                    _area_before = float(geom.area)
                    geom = transform(transformer.transform, geom)
                    area_delta_by_op["crs_transform"] = (
                        area_delta_by_op.get("crs_transform", 0.0)
                        + (float(geom.area) - _area_before)
                    )
                    logs.append(f"crs_transform: Reprojected geometry at feature index {idx}")
                    _bump("crs_transform", affected=1)
                except Exception as e:
                    crs_transform_failures += 1
                    logs.append(f"crs_transform: Failed to reproject feature index {idx}: {e}")
                    _bump("crs_transform", failed=1)

            # ----------------------------------------------------
            # Operation: snap_within_tolerance
            # ----------------------------------------------------
            if "snap_within_tolerance" in active_ops:
                try:
                    _area_before = float(geom.area)
                    geom = shapely.set_precision(geom, grid_size=tolerance)
                    area_delta_by_op["snap_within_tolerance"] = (
                        area_delta_by_op.get("snap_within_tolerance", 0.0)
                        + (float(geom.area) - _area_before)
                    )
                    logs.append(
                        f"snap_within_tolerance: Snapped vertices of feature index {idx} with grid precision {tolerance} (target CRS units)"
                    )
                    _bump("snap_within_tolerance", affected=1)
                except Exception as e:
                    logs.append(f"snap_within_tolerance: Snapping failed for feature index {idx}: {e}")
                    _bump("snap_within_tolerance", failed=1)

            feat["geometry"] = mapping(geom)
            cleaned_features.append(feat)

        # #618-15: 数据集级 CRS 声明必须在「所有要素都成功投影」之后才更新。
        # 只要有任一要素投影失败（坐标停留在源 CRS），把数据集标记成目标 CRS
        # 就会让下游按错误基准解释这些坐标 —— 部分失败时保持源 CRS 声明并披露。
        if transformer is not None:
            if crs_transform_failures == 0:
                repaired_geojson["crs"] = {
                    "type": "name",
                    "properties": {"name": target_crs},
                }
                logs.append(f"crs_transform: Updated dataset CRS definition from {source_crs} to {target_crs}")
            else:
                logs.append(
                    f"crs_transform: {crs_transform_failures} feature(s) failed to reproject — "
                    f"dataset CRS definition left as {source_crs}"
                )

        # ----------------------------------------------------
        # Operation: deduplicate
        # ----------------------------------------------------
        _post_loop_count = len(cleaned_features)
        if "deduplicate" in active_ops and cleaned_features:
            unique_features = []
            seen_hashes = set()
            for f_idx, f in enumerate(cleaned_features):
                geom_dict = f.get("geometry")
                props_dict = f.get("properties")

                try:
                    g_shape = shape(geom_dict)
                    wkb_key = g_shape.wkb
                except Exception:
                    wkb_key = str(geom_dict)

                props_key = str(sorted(props_dict.items())) if isinstance(props_dict, dict) else ""
                combined_key = (wkb_key, props_key)

                if combined_key not in seen_hashes:
                    seen_hashes.add(combined_key)
                    unique_features.append(f)
                else:
                    try:
                        area_delta_by_op["deduplicate"] = (
                            area_delta_by_op.get("deduplicate", 0.0) - float(g_shape.area)
                        )
                    except Exception:  # noqa: BLE001 — lineage 面积是增值证据
                        pass
                    logs.append(f"deduplicate: Removed duplicate feature at index {f_idx}")
            removed_duplicates = len(cleaned_features) - len(unique_features)
            if removed_duplicates > 0:
                _bump("deduplicate", affected=removed_duplicates)
            cleaned_features = unique_features
        _post_dedup_count = len(cleaned_features)

        # ----------------------------------------------------
        # Operations: fix_topology_overlap / fix_gaps (ADR-0153, P6)
        # 共享预处理：多边形要素位置表（pos, geom 快照）—— 预算与 STRtree
        # 在两个 op 内各自独立（重叠修复可能改变几何，缝隙 snap 亦然）。
        # ----------------------------------------------------
        _poly_ops_active = ("fix_topology_overlap" in active_ops) or ("fix_gaps" in active_ops)
        poly_positions: List[Tuple[int, Any]] = []
        if _poly_ops_active and cleaned_features:
            for _pos, _f in enumerate(cleaned_features):
                _gdict = _f.get("geometry") if isinstance(_f, dict) else None
                if not isinstance(_gdict, dict):
                    continue
                try:
                    _g = shape(_gdict)
                except Exception:
                    continue
                if isinstance(_g, (Polygon, MultiPolygon)) and not _g.is_empty:
                    poly_positions.append((_pos, _g))

        # ----------------------------------------------------
        # Operation: fix_topology_overlap (ADR-0153, P6)
        # ----------------------------------------------------
        # 拓扑重叠修复：mode=difference 时后入要素对先入要素让位
        # （geom_j := geom_j - geom_i，先到先得的确定性规则）；mode=flag
        # 时只把重叠对写进 _QUALITY_FLAG_KEY（默认，由 plan 裁决选择）。
        # 面预算 ≤ _MAX_TOPOLOGY_FIX_PAIRS（对齐 audit #539 有界纪律）。
        if "fix_topology_overlap" in active_ops and len(poly_positions) >= 2:
            _fx_params = params_by_op.get("fix_topology_overlap", {})
            _fx_mode = str(_fx_params.get("mode", "flag"))
            try:
                from app.services.spatial_quality_service import _crs_is_geographic
                _geo_units = _crs_is_geographic(target_crs)
            except Exception:  # noqa: BLE001 — 阈值量纲回退按度²口径
                _geo_units = True
            _overlap_thr = float(
                _fx_params.get(
                    "overlap_area_threshold",
                    1.0 / (111320.0 ** 2) if _geo_units else 1.0,
                )
            )
            _pairs_resolved = 0
            _pairs_flagged = 0
            _fx_failed = 0
            from shapely.strtree import STRtree
            _otree = STRtree([g for _, g in poly_positions])
            _examined: set = set()
            _budget_hit = False
            for _i, (_pos_i, _g_i) in enumerate(poly_positions):
                if _budget_hit:
                    break
                try:
                    _cands = _otree.query(_g_i)
                except Exception:
                    continue
                for _c in _cands:
                    _j = int(_c)
                    if _j <= _i:
                        continue
                    if _pairs_resolved + _pairs_flagged >= _MAX_TOPOLOGY_FIX_PAIRS:
                        _budget_hit = True
                        break
                    _pos_j = poly_positions[_j][0]
                    if (_pos_i, _pos_j) in _examined:
                        continue
                    # i 的几何可能已被更早的 pair 裁剪过 —— 每对用最新形态。
                    try:
                        _g_i_now = shape(cleaned_features[_pos_i]["geometry"])
                        _g_j_now = shape(cleaned_features[_pos_j]["geometry"])
                    except Exception:
                        continue
                    try:
                        _hits = _g_i_now.overlaps(_g_j_now) or (
                            _g_i_now.intersects(_g_j_now)
                            and _g_i_now.intersection(_g_j_now).area > _overlap_thr
                        )
                    except Exception:
                        continue
                    if not _hits:
                        continue
                    _examined.add((_pos_i, _pos_j))
                    if _fx_mode == "difference":
                        try:
                            _resolved = _g_j_now.difference(_g_i_now)
                            if _resolved.is_empty or _resolved.area <= 0:
                                # difference 清空 = 该要素完全被吞 —— 删除是
                                # 破坏性决策，本 op 不删，如实计失败并披露。
                                _fx_failed += 1
                                logs.append(
                                    f"fix_topology_overlap: difference emptied feature at index {_pos_j}; "
                                    "kept original (removal is a destructive decision, not taken here)"
                                )
                                continue
                            _lost = float(_g_j_now.area) - float(_resolved.area)
                            cleaned_features[_pos_j]["geometry"] = mapping(_resolved)
                            area_delta_by_op["fix_topology_overlap"] = (
                                area_delta_by_op.get("fix_topology_overlap", 0.0) - _lost
                            )
                            _pairs_resolved += 1
                            logs.append(
                                f"fix_topology_overlap: Resolved overlap of feature at index {_pos_j} "
                                f"against index {_pos_i} (area loss {_lost:.6g})"
                            )
                        except Exception as e:
                            _fx_failed += 1
                            logs.append(
                                f"fix_topology_overlap: Failed to resolve overlap at index {_pos_j}: {e}"
                            )
                    else:
                        _pairs_flagged += 1
                        _flags = repaired_geojson.setdefault(_QUALITY_FLAG_KEY, {})
                        _ov = _flags.setdefault("topology_overlap_pairs", [])
                        if len(_ov) < _MAX_EVIDENCE_OPS:
                            _ov.append({"first": int(_pos_i), "second": int(_pos_j)})
                        elif not _flags.get("topology_overlap_truncated"):
                            _flags["topology_overlap_truncated"] = True
            if _pairs_resolved:
                _bump("fix_topology_overlap", affected=_pairs_resolved)
            if _pairs_flagged:
                _bump("fix_topology_overlap", affected=_pairs_flagged)
            if _fx_failed:
                _bump("fix_topology_overlap", failed=_fx_failed)
            if _budget_hit:
                logs.append(
                    f"fix_topology_overlap: pair budget {_MAX_TOPOLOGY_FIX_PAIRS} exhausted; "
                    "remaining overlaps not examined (disclosed, not silent)"
                )

        # ----------------------------------------------------
        # Operation: fix_gaps (ADR-0153, P6)
        # ----------------------------------------------------
        # 缝隙修复：mode=snap 时后入要素顶点吸附到先入要素（pairwise
        # shapely.snap，容差可配，目标 CRS 单位）；mode=flag 时只记录。
        if "fix_gaps" in active_ops and len(poly_positions) >= 2:
            _fg_params = params_by_op.get("fix_gaps", {})
            _fg_mode = str(_fg_params.get("mode", "flag"))
            _fg_tol = float(_fg_params.get("tolerance", tolerance))
            _gap_pairs_snapped = 0
            _gap_pairs_flagged = 0
            _fg_failed = 0
            from shapely.strtree import STRtree
            _gtree = STRtree([g for _, g in poly_positions])
            _gexamined: set = set()
            _gbudget_hit = False
            for _i, (_pos_i, _g_i) in enumerate(poly_positions):
                if _gbudget_hit:
                    break
                try:
                    _cands = _gtree.query(_g_i.buffer(_fg_tol))
                except Exception:
                    continue
                for _c in _cands:
                    _j = int(_c)
                    if _j <= _i:
                        continue
                    if _gap_pairs_snapped + _gap_pairs_flagged >= _MAX_TOPOLOGY_FIX_PAIRS:
                        _gbudget_hit = True
                        break
                    _pos_j = poly_positions[_j][0]
                    if (_pos_i, _pos_j) in _gexamined:
                        continue
                    _gexamined.add((_pos_i, _pos_j))
                    try:
                        _g_i_now = shape(cleaned_features[_pos_i]["geometry"])
                        _g_j_now = shape(cleaned_features[_pos_j]["geometry"])
                        _dist = _g_i_now.distance(_g_j_now)
                    except Exception:
                        continue
                    if not (0 < _dist <= _fg_tol):
                        continue
                    if _fg_mode == "snap":
                        try:
                            _snapped = shapely.snap(_g_j_now, _g_i_now, _fg_tol)
                            cleaned_features[_pos_j]["geometry"] = mapping(_snapped)
                            _gap_pairs_snapped += 1
                            logs.append(
                                f"fix_gaps: Snapped feature at index {_pos_j} toward index "
                                f"{_pos_i} (gap {_dist:.6g} <= tol {_fg_tol:.6g})"
                            )
                        except Exception as e:
                            _fg_failed += 1
                            logs.append(f"fix_gaps: Snap failed at index {_pos_j}: {e}")
                    else:
                        _gap_pairs_flagged += 1
                        _gflags = repaired_geojson.setdefault(_QUALITY_FLAG_KEY, {})
                        _gp = _gflags.setdefault("topology_gap_pairs", [])
                        if len(_gp) < _MAX_EVIDENCE_OPS:
                            _gp.append({
                                "first": int(_pos_i),
                                "second": int(_pos_j),
                                "gap": round(float(_dist), 9),
                            })
                        elif not _gflags.get("topology_gap_truncated"):
                            _gflags["topology_gap_truncated"] = True
            if _gap_pairs_snapped:
                _bump("fix_gaps", affected=_gap_pairs_snapped)
            if _gap_pairs_flagged:
                _bump("fix_gaps", affected=_gap_pairs_flagged)
            if _fg_failed:
                _bump("fix_gaps", failed=_fg_failed)
            if _gbudget_hit:
                logs.append(
                    f"fix_gaps: pair budget {_MAX_TOPOLOGY_FIX_PAIRS} exhausted; "
                    "remaining gaps not examined (disclosed, not silent)"
                )

        # ----------------------------------------------------
        # Operation: attribute_type_normalization
        # ----------------------------------------------------
        if "attribute_type_normalization" in active_ops and cleaned_features:
            all_keys = set()
            for f in cleaned_features:
                if isinstance(f.get("properties"), dict):
                    all_keys.update(f["properties"].keys())

            for f in cleaned_features:
                if not isinstance(f.get("properties"), dict):
                    f["properties"] = {}
                props = f["properties"]

                for k in all_keys:
                    if k not in props:
                        props[k] = None

                for k, v in list(props.items()):
                    if isinstance(v, str):
                        v_stripped = v.strip()
                        if v_stripped.isdigit() or (v_stripped.startswith("-") and v_stripped[1:].isdigit()):
                            props[k] = int(v_stripped)
                        else:
                            try:
                                f_val = float(v_stripped)
                                if not math.isnan(f_val) and not math.isinf(f_val):
                                    props[k] = f_val
                                else:
                                    props[k] = v_stripped
                            except ValueError:
                                props[k] = v_stripped
                    elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                        props[k] = None

            logs.append(
                f"attribute_type_normalization: Standardized property schemas and normalized attribute values across {len(cleaned_features)} features"
            )
            _bump("attribute_type_normalization", affected=len(cleaned_features))

        # ----------------------------------------------------
        # Operation: attribute_drop_or_flag (ADR-0153, P6 — HIGH_NULL_RATIO)
        # ----------------------------------------------------
        # 高空值率字段：mode=flag（默认）只把字段清单写进 _QUALITY_FLAG_KEY；
        # mode=drop_column 删除全部要素的该字段（破坏性，plan 裁决开启）。
        if "attribute_drop_or_flag" in active_ops and cleaned_features:
            _ad_params = params_by_op.get("attribute_drop_or_flag", {})
            _ad_mode = str(_ad_params.get("mode", "flag"))
            _heavy_fields = [str(k) for k in (_ad_params.get("fields") or [])][:8]
            if _heavy_fields:
                if _ad_mode == "drop_column":
                    _touched = 0
                    for _f in cleaned_features:
                        _props = _f.get("properties")
                        if not isinstance(_props, dict):
                            continue
                        for _k in _heavy_fields:
                            if _k in _props:
                                _props.pop(_k, None)
                                _touched += 1
                    if _touched:
                        _bump("attribute_drop_or_flag", affected=_touched)
                        logs.append(
                            "attribute_drop_or_flag: Dropped null-heavy column(s) "
                            f"{_heavy_fields} ({_touched} cell(s) removed)"
                        )
                else:
                    _aflags = repaired_geojson.setdefault(_QUALITY_FLAG_KEY, {})
                    _aflags["null_heavy_fields"] = _heavy_fields
                    _bump("attribute_drop_or_flag", affected=len(_heavy_fields))
                    logs.append(
                        "attribute_drop_or_flag: Flagged null-heavy field(s) "
                        f"{_heavy_fields} (flag-only default; no data removed)"
                    )

        # ----------------------------------------------------
        # Operation: drop_outliers_or_flag (ADR-0153, P6 — NUMERIC_OUTLIER)
        # ----------------------------------------------------
        # 离群值：mode=flag（默认）标记越界要素与字段建议（outlier_policy
        # 契约由 gate/P4 产出，本 op 不裁剪）；mode=drop 仅在 plan 裁决
        # （allow_destructive 且 outlier_ratio ≤ 上限）时按阈值剔除。
        # 无 fields 参数时回退 classic >3σ 判据（与 audit 同口径）。
        if "drop_outliers_or_flag" in active_ops and cleaned_features:
            _do_params = params_by_op.get("drop_outliers_or_flag", {})
            _do_mode = str(_do_params.get("mode", "flag"))
            _fields_spec = [dict(f) for f in (_do_params.get("fields") or [])][:8]

            _numeric: Dict[str, List[Tuple[int, float]]] = {}
            for _fidx, _f in enumerate(cleaned_features):
                _props = _f.get("properties")
                if not isinstance(_props, dict):
                    continue
                for _k, _v in _props.items():
                    if isinstance(_v, (int, float)) and not isinstance(_v, bool) and math.isfinite(float(_v)):
                        _numeric.setdefault(str(_k), []).append((_fidx, float(_v)))

            _bounds: Dict[str, Tuple[float, float]] = {}
            if _fields_spec:
                for _spec in _fields_spec:
                    _fname = str(_spec.get("field", ""))
                    if not _fname or _fname not in _numeric:
                        continue
                    _lo = _spec.get("lower")
                    _hi = _spec.get("upper")
                    _bounds[_fname] = (
                        float(_lo) if _lo is not None else float("-inf"),
                        float(_hi) if _hi is not None else float("inf"),
                    )
            if not _bounds:
                for _k, _vals in _numeric.items():
                    if len(_vals) < 3:
                        continue
                    _arr = [v for _, v in _vals]
                    _mean = sum(_arr) / len(_arr)
                    _std = math.sqrt(sum((v - _mean) ** 2 for v in _arr) / len(_arr))
                    if _std > 1e-8:
                        _bounds[_k] = (_mean - 3.0 * _std, _mean + 3.0 * _std)

            _flagged_feats: set = set()
            _field_counts: Dict[str, int] = {}
            for _k, (_lo, _hi) in _bounds.items():
                for _fidx, _v in _numeric.get(_k, ()):
                    if _v < _lo or _v > _hi:
                        _flagged_feats.add(_fidx)
                        _field_counts[_k] = _field_counts.get(_k, 0) + 1

            if _flagged_feats:
                if _do_mode == "drop":
                    _before_drop = len(cleaned_features)
                    cleaned_features = [
                        f for i, f in enumerate(cleaned_features) if i not in _flagged_feats
                    ]
                    _removed = _before_drop - len(cleaned_features)
                    if _removed:
                        _bump("drop_outliers_or_flag", affected=_removed)
                        logs.append(
                            f"drop_outliers_or_flag: Removed {_removed} outlier feature(s) "
                            f"across field(s) {sorted(_field_counts)}"
                        )
                else:
                    _oflags = repaired_geojson.setdefault(_QUALITY_FLAG_KEY, {})
                    _oflags["outlier_fields"] = [
                        {"field": k, "count": c, "lower": _bounds[k][0], "upper": _bounds[k][1]}
                        for k, c in sorted(_field_counts.items())
                    ][:_MAX_EVIDENCE_OPS]
                    _oflags["outlier_feature_indices"] = sorted(int(i) for i in _flagged_feats)[
                        :_MAX_EVIDENCE_OPS
                    ]
                    if len(_flagged_feats) > _MAX_EVIDENCE_OPS:
                        _oflags["outlier_truncated"] = True
                    _bump("drop_outliers_or_flag", affected=len(_flagged_feats))
                    logs.append(
                        f"drop_outliers_or_flag: Flagged {len(_flagged_feats)} outlier feature(s) "
                        f"across field(s) {sorted(_field_counts)} (flag-only default; "
                        "clip/trim is line-03's outlier_policy decision)"
                    )

        repaired_geojson["features"] = cleaned_features

        # Wave-4: bounded per-op evidence in canonical op order (≤16 entries).
        ops_evidence: List[Dict[str, Any]] = []
        for op in _EVIDENCE_OP_ORDER:
            if op in op_stats:
                stat = op_stats[op]
                ops_evidence.append(
                    {
                        "op": op[:32],
                        "features_affected": int(stat["features_affected"]),
                        "failed_count": int(stat["failed_count"]),
                    }
                )
            if len(ops_evidence) >= _MAX_EVIDENCE_OPS:
                break

        # ------------------------------------------------------------
        # ADR-0153: 修复血缘（per-op before/after/area_delta/evidence/ts）。
        # 计数口径：remove_empty 在 per-feature 循环内 → before=输入数、
        # after=循环后数；deduplicate 在其后 → before=循环后数、after=去重后数；
        # drop 模式的 drop_outliers_or_flag → before=去重后数、after=最终数。
        _remove_empty_affected = op_stats.get("remove_empty", {}).get("features_affected", 0)
        _dedup_affected = op_stats.get("deduplicate", {}).get("features_affected", 0)
        _outlier_drop_affected = (
            op_stats.get("drop_outliers_or_flag", {}).get("features_affected", 0)
            if params_by_op.get("drop_outliers_or_flag", {}).get("mode") == "drop"
            else 0
        )
        _final_count = len(cleaned_features)
        _post_loop_count_actual = _input_count - _remove_empty_affected
        _post_dedup_count_actual = _post_loop_count_actual - _dedup_affected

        def _stage_count(op: str) -> int:
            """op 执行时点的数据集规模（计数未因该 op 改变）。"""
            if op == "remove_empty":
                return _input_count
            if op in ("deduplicate",):
                return _post_loop_count_actual
            if op in ("drop_outliers_or_flag",):
                return _post_dedup_count_actual
            # attribute_drop_or_flag 删列不删行；其余 loop op 在循环内（计数 = 循环后）。
            return _post_dedup_count_actual if op in (
                "attribute_type_normalization",
                "attribute_drop_or_flag",
            ) else _post_loop_count_actual

        lineage: List[Dict[str, Any]] = []
        for op in _EVIDENCE_OP_ORDER:
            if op not in op_stats:
                continue
            stat = op_stats[op]
            before = _stage_count(op)
            if op == "remove_empty":
                after = _post_loop_count_actual
            elif op == "deduplicate":
                after = _post_dedup_count_actual
            elif op == "drop_outliers_or_flag":
                after = _final_count
            else:
                after = before
            lineage.append(
                {
                    "op": op[:32],
                    "before_count": int(before),
                    "after_count": int(after),
                    "area_delta": round(float(area_delta_by_op.get(op, 0.0)), 9),
                    "evidence": [
                        {
                            "op": op[:32],
                            "features_affected": int(stat["features_affected"]),
                            "failed_count": int(stat["failed_count"]),
                        }
                    ],
                    "ts": _utc_ts(),
                }
            )
            if len(lineage) >= _MAX_EVIDENCE_OPS:
                break

        logger.info(f"[SpatialRepairPipeline] Applied {len(active_ops)} operations; generated {len(logs)} audit log entries.")
        return repaired_geojson, logs, ops_evidence, lineage
