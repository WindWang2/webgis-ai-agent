"""G15 WP05 —— 跨画幅共享分类（统一分级只计算一次）。

科学契约（任务书 §3.3/§3.4）：
- 共同颜色域必须有相同变量含义/单位/分母/分类方法/缺失值语义 ——
  ``ratio``（计数，零有意义）与 ``rate``（归一化率）绝不共用色带；
- breaks 在**跨画幅真实数据并集**上只计算一次（classify_values 单次
  调用），绝不逐帧 quantile 后冒充统一分级，也绝不用视口样本冒充总体；
- domain_status 如实：complete（全部画幅有数据）/ partial（部分画幅
  空数据，仍按可用并集计算 + 披露）/ unknown（无数据，不带 breaks）；
- 退化分布保持明确语义：全零/常数列 → 单级 [v, v]；极端值不静默裁剪。

纯函数、确定性；输入为各画幅**已按帧绑定过滤**的真实 FeatureCollection。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.lib.cartography.classify import classify_values
from app.lib.cartography.defaults import (
    DEFAULT_CLASS_COUNT,
    DEFAULT_CLASSIFICATION_METHOD,
    DEFAULT_PALETTE,
)
from app.lib.cartography.semantic_inputs import derive_semantic_inputs
from app.lib.cartography.visual_variables import MEASUREMENT_KINDS

logger = logging.getLogger("gis_harness.frame_products.shared_expression")

__all__ = ["SharedExpressionOutcome", "compute_shared_expression", "union_values"]


#: 采样上限（并集分级输入；Jenks 内部另有 1000 采样帽 —— 这里只防
#: 百万级病态输入的内存放大，正常行政区产品远低于此）。
MAX_UNION_SAMPLE = 20_000


@dataclass(frozen=True)
class SharedExpressionOutcome:
    """计算产物：要么可入契约的 shared_expression 配置，要么 typed 拒绝。"""

    config: Optional[Dict[str, Any]] = None
    refused: bool = False
    refusal_code: str = ""
    refusal_message: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused and self.config is not None


def _finite_values(fc: Dict[str, Any], fld: str) -> List[float]:
    feats = fc.get("features") if isinstance(fc, dict) else None
    if not isinstance(feats, list):
        return []
    out: List[float] = []
    for f in feats:
        if not isinstance(f, dict):
            continue
        props = f.get("properties")
        if not isinstance(props, dict):
            continue
        v = props.get(fld)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        fv = float(v)
        if fv == fv and fv not in (float("inf"), float("-inf")):
            out.append(fv)
    return out


def union_values(
    frame_datasets: Sequence[Tuple[str, Dict[str, Any]]], fld: str
) -> Tuple[List[float], Dict[str, int], List[str]]:
    """并集收集 → (union values, per-frame counts, empty frame ids)。"""
    union: List[float] = []
    per_frame: Dict[str, int] = {}
    empty: List[str] = []
    for frame_id, fc in frame_datasets:
        vals = _finite_values(fc, fld)
        per_frame[frame_id] = len(vals)
        if not vals:
            empty.append(frame_id)
        union.extend(vals)
    if len(union) > MAX_UNION_SAMPLE:
        # 均匀确定性抽样（seed 固定；分级帽内可复现）。
        step = len(union) / MAX_UNION_SAMPLE
        union = [union[int(i * step)] for i in range(MAX_UNION_SAMPLE)]
    return union, per_frame, empty


def _normalize_breaks(raw: List[float]) -> Optional[List[float]]:
    """classify 输出 → 严格递增 breaks；退化（<2 唯一值）→ 单级 [v, v]。"""
    seen: List[float] = []
    for b in raw:
        b = float(b)
        if not seen or b > seen[-1]:
            seen.append(b)
    if len(seen) >= 2:
        return seen
    if len(seen) == 1:
        return [seen[0], seen[0]]
    return None


def _measurement_of(field: str, samples: Sequence[Any], explicit: Optional[str]) -> str:
    if explicit in MEASUREMENT_KINDS:
        return explicit
    try:
        sem = derive_semantic_inputs(str(field), value_samples=list(samples[:200]))
        return sem.measurement_kind or ""
    except Exception:  # noqa: BLE001 — 语义推导失败 = 诚实未知
        return ""


#: 共享资格分组：这些测量语义两两不同即拒绝共享（同组才可比）。
_RATE_LIKE = {"rate"}
_COUNT_LIKE = {"ratio"}


def compute_shared_expression(
    frame_datasets: Sequence[Tuple[str, Dict[str, Any]]],
    field: str,
    *,
    method: Optional[str] = None,
    k: Optional[int] = None,
    palette: Optional[str] = None,
    semantics: str = "other",
    unit: Optional[str] = None,
    denominator: Optional[str] = None,
    explicit_measurement: Optional[str] = None,
) -> SharedExpressionOutcome:
    """跨画幅真实数据 → 共享分级配置（breaks 单次计算 + 资格判定）。

    ``frame_datasets``：[(frame_id, 该帧绑定过滤后的 FeatureCollection)]。
    资格拒绝（refused）不产出配置 —— 调用方应转独立图例或修正绑定，
    绝不降级为「各自分级但标称共享」。
    """
    if not frame_datasets:
        return SharedExpressionOutcome(
            refused=True, refusal_code="no_frames",
            refusal_message="共享分级需要至少一个画幅的数据绑定",
        )
    method = method or DEFAULT_CLASSIFICATION_METHOD
    k = int(k or DEFAULT_CLASS_COUNT)
    palette = palette or DEFAULT_PALETTE

    # ── 资格 1：跨画幅测量语义一致（逐帧推导 + 显式 pin 优先）。──
    kinds: Dict[str, str] = {}
    for frame_id, fc in frame_datasets:
        vals = _finite_values(fc, field)
        kinds[frame_id] = _measurement_of(field, vals, explicit_measurement)
    distinct = {kd for kd in kinds.values() if kd}
    if len(distinct) > 1:
        return SharedExpressionOutcome(
            refused=True,
            refusal_code="measurement_kind_mismatch",
            refusal_message=(
                "画幅间字段测量语义不一致（"
                + ", ".join(f"{fid}:{kd or 'unknown'}" for fid, kd in list(kinds.items())[:4])
                + "）——计数与归一化率等不可共用色带；请改独立图例或统一口径。"
            ),
        )
    resolved_kind = next(iter(distinct), "")
    # 声明语义与推导语义矛盾（如声明 ratio 但数据是 rate）→ 拒绝。
    declared_map = {"count": "ratio", "index": "quantitative"}
    if semantics in declared_map and resolved_kind \
            and declared_map[semantics] != resolved_kind:
        return SharedExpressionOutcome(
            refused=True,
            refusal_code="declared_semantics_conflict",
            refusal_message=(
                f"声明语义 {semantics!r} 与数据推导语义 {resolved_kind!r} 矛盾 —— "
                "共享分级拒绝落盘错误契约。"
            ),
        )

    # ── 资格 2：比率语义必须有分母声明（与计数区分的可复核来源）。──
    if resolved_kind == "rate" and not denominator:
        return SharedExpressionOutcome(
            refused=True,
            refusal_code="rate_missing_denominator",
            refusal_message=(
                "rate 语义共享分级需要 denominator 声明（分母字段/口径），"
                "否则读者无法复核跨画幅可比性。"
            ),
        )

    # ── 并集分级（只算一次）。──
    union, per_frame_counts, empty_frames = union_values(frame_datasets, field)
    eligibility: List[str] = ["union_breaks"]
    if resolved_kind:
        eligibility.append(f"measurement_{resolved_kind}")
    if empty_frames:
        eligibility.append("partial_frame_missing_data")

    if not union:
        # 全部画幅无数据 → 诚实 unknown（不带 breaks，不伪造 0 域）。
        return SharedExpressionOutcome(config={
            "field": field, "semantics": semantics,
            "unit": unit, "denominator": denominator,
            "method": method, "k": k, "palette": palette,
            "breaks": None, "domain_status": "unknown",
            "per_frame_counts": per_frame_counts, "sample_size": 0,
            "eligibility": eligibility[:8],
        })

    breaks = _normalize_breaks(classify_values(union, method, k))
    if breaks is None:
        return SharedExpressionOutcome(config={
            "field": field, "semantics": semantics,
            "unit": unit, "denominator": denominator,
            "method": method, "k": k, "palette": palette,
            "breaks": None, "domain_status": "unknown",
            "per_frame_counts": per_frame_counts,
            "sample_size": len(union),
            "eligibility": eligibility[:8],
        })
    # classify 归一后类数可能与 k 不同（去重塌缩）——契约 k 与 breaks 同步。
    k_effective = len(breaks) - 1
    domain_status = "partial" if empty_frames else "complete"
    return SharedExpressionOutcome(config={
        "field": field, "semantics": semantics,
        "unit": unit, "denominator": denominator,
        "method": method, "k": k_effective, "palette": palette,
        "breaks": breaks, "domain_status": domain_status,
        "per_frame_counts": per_frame_counts, "sample_size": len(union),
        "eligibility": eligibility[:8],
    })
