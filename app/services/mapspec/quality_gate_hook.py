"""ADR-0153（ac-04 数据自适应预处理）：制图前置质量门禁钩子（P1）。

只加钩子，不改既有突变逻辑：钩子在 UpsertLayer / UpsertSource 突变分支
内、数据落盘（commit）之前调用；blocking 级问题 → 拒绝本次 mutation 并
返回可操作修复计划（一键 op 序列）；warning 级 → 放行但把
quality_advisories 与 P7 profile 契约扩展写进 layer/source 元数据。
settings.MAP_QUALITY_GATE_MODE = enforce|advisory|off 是总开关与回滚面；
per-intent ``quality_gate_bypass`` 是逃生舱（block 降级放行，必须留
审计事件）。与 09 线的边界：09 改评审段（cartographic_review），本钩子
只在 pre-commit 数据质量面；若上游冲突本线让位。
"""
import logging
from typing import Any, Dict, Optional

from app.lib.cartography.data_tiers import (
    TIER_INLINE_FEATURES as MAP_QUALITY_GATE_FALLBACK,
)
from app.services.mapspec.mutation_contracts import MapSpecResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# ADR-0153（ac-04 数据自适应预处理）：制图前置质量门禁钩子（P1）
#
# 只加钩子，不改既有突变逻辑：钩子在 UpsertLayer / UpsertSource 突变分支
# 内、数据落盘（commit）之前调用；blocking 级问题 → 拒绝本次 mutation 并
# 返回可操作修复计划（一键 op 序列）；warning 级 → 放行但把
# quality_advisories 与 P7 profile 契约扩展写进 layer/source 元数据。
# settings.MAP_QUALITY_GATE_MODE = enforce|advisory|off 是总开关与回滚面；
# per-intent ``quality_gate_bypass`` 是逃生舱（block 降级放行，必须留
# 审计事件）。与 09 线的边界：09 改评审段（cartographic_review），本钩子
# 只在 pre-commit 数据质量面；若上游冲突本线让位。
# ─────────────────────────────────────────────────────────────────────────────
async def _run_quality_gate_hook(
    source_entry: Dict[str, Any],
    processed_layer: Dict[str, Any],
    *,
    origin: str,
) -> Optional["MapSpecResult"]:
    """数据质量门禁（pre-commit 段）。

    返回 None = 放行（advisory 已就地写进 source_entry / processed_layer）；
    返回 MapSpecResult（is_error=True, error_code="quality_gate_blocked"）=
    拒绝本次 mutation（候选 spec 尚未提交，直接丢弃即可）。
    """
    import asyncio as _asyncio

    from app.core.config import settings

    mode = str(getattr(settings, "MAP_QUALITY_GATE_MODE", "enforce") or "enforce").lower()
    if mode == "off":
        return None  # off：完全关闭（回滚面 = 合入前行为）
    if mode not in ("enforce", "advisory"):
        # fail-closed：未知/拼错的模式绝不静默关闸（打错字把安全门悄悄关掉
        # 比拦错数据更危险）—— 一律按 enforce 兜底并留审计事件。
        logger.warning(
            "[quality-gate] invalid MAP_QUALITY_GATE_MODE=%r; falling back to enforce",
            getattr(settings, "MAP_QUALITY_GATE_MODE", None),
        )
        mode = "enforce"
        try:
            from app.services.spatial_quality_gate import record_gate_event

            record_gate_event("invalid_mode_fallback_enforce", mode=mode)
        except Exception:  # noqa: BLE001 — 留痕失败不影响门禁判定
            pass

    from app.services.mapspec_source import (
        is_data_fabric_entry,
        is_raster_entry,
    )

    # 门禁只审计矢量内联载荷；raster / fabric / ref 载体的全量审计在
    # ingest/Celery 路径（这里逐要素审计既贵又拿不到载荷）。
    if is_raster_entry(source_entry) or is_data_fabric_entry(source_entry):
        return None
    data = source_entry.get("inlineData")
    if not isinstance(data, dict):
        return None
    feats = data.get("features")
    # 空 FeatureCollection 也进门禁（evaluate 产出 EMPTY_FEATURE_COLLECTION
    # warning advisory —— 空图层上图同样是毁图形态）。
    if not isinstance(feats, list) and data.get("type") != "Feature":
        return None

    from app.services.spatial_quality_gate import (
        evaluate_quality_gate,
        record_gate_event,
    )

    declared_crs = processed_layer.get("crs") if isinstance(processed_layer.get("crs"), str) else None
    layer_id = str(processed_layer.get("id") or source_entry.get("ref_id") or "layer")[:80]
    try:
        max_features = int(getattr(settings, "MAP_QUALITY_GATE_MAX_FEATURES", MAP_QUALITY_GATE_FALLBACK))
    except (TypeError, ValueError):
        max_features = MAP_QUALITY_GATE_FALLBACK
    # 重算离事件循环（仓库红线；与 process_layer_ingestion 的 to_thread 同纪律）。
    verdict = await _asyncio.to_thread(
        evaluate_quality_gate,
        data,
        declared_crs=declared_crs,
        dataset_id=layer_id,
        max_features=max_features,
    )

    # P7：profile 契约扩展合并进 source profile（有界，键封闭）。UpsertSource
    # 路径没有 ingestion 自动画像 —— 无 profile 时创建仅含门禁字段的画像
    # （扩展字典自带全部 6 键的诚实值，自洽可独立消费）。
    profile = source_entry.get("profile")
    extension = verdict.get("profile_extension") or {}
    if extension:
        if not isinstance(profile, dict):
            profile = {}
            source_entry["profile"] = profile
        profile.update({k: extension[k] for k in (
            "geometry_mix", "n_valid", "extent", "crs_confidence",
            "outlier_policy", "quality_advisories",
        ) if k in extension})
    # P1：warning 级 → 放行但 layer metadata 落 quality_advisories（07/09 消费）。
    advisories = verdict.get("advisories") or []
    if advisories:
        processed_layer["quality_advisories"] = advisories
        source_entry["quality_advisories"] = advisories

    if verdict.get("verdict") != "block":
        record_gate_event(
            "evaluated",
            verdict=str(verdict.get("verdict", "pass")),
            mode=mode,
            dataset_id=layer_id,
            codes=list(verdict.get("blocking_codes") or []) + list(verdict.get("error_codes") or []),
        )
        return None

    blocking_codes = list(verdict.get("blocking_codes") or [])
    bypass = bool(processed_layer.get("quality_gate_bypass") is True)
    if mode == "advisory" or bypass:
        # 逃生舱：降级放行，但必须留审计事件（禁止静默放行）。
        record_gate_event(
            "bypass" if bypass else "advisory_passthrough",
            verdict="block",
            mode=mode,
            dataset_id=layer_id,
            codes=blocking_codes,
        )
        processed_layer.setdefault("quality_advisories", [])
        processed_layer["quality_advisories"] = (
            list(processed_layer.get("quality_advisories") or [])
            + [{
                "code": "QUALITY_GATE_BLOCK_DOWNGRADED",
                "level": "error",
                "message": f"blocking 质量问题已放行（bypass={bypass}）: {','.join(blocking_codes)}",
            }]
        )[:16]
        return None

    record_gate_event(
        "blocked",
        verdict="block",
        mode=mode,
        dataset_id=layer_id,
        codes=blocking_codes,
    )
    plan = verdict.get("repair_plan") or {}
    ops = list(plan.get("ops") or [])
    op_seq = " → ".join(ops) if ops else "(no auto-repair plan; manual fix required)"
    return MapSpecResult(
        is_error=True,
        origin=origin,
        error_code="quality_gate_blocked",
        error_msg=(
            f"数据质量门禁拦截图层 {layer_id}：blocking 级问题 "
            f"{','.join(blocking_codes)}（禁止静默放行）"
        ),
        correction_hint=(
            "先修复数据再上图 —— 一键修复 op 序列（SpatialRepairPipeline，"
            f"非破坏 deepcopy + 证据）：{op_seq}。计划详情 reasons/"
            f"destructive_decisions: plan_id 附于 advisories。CRS 推断："
            f"{(verdict.get('crs_inference') or {}).get('crs') or '未知'}"
            f"（{(verdict.get('crs_inference') or {}).get('confidence') or '-'}）。"
            "如确需跳过：该次 mutation 显式携带 quality_gate_bypass=true"
            "（将记录审计事件）。"
        ),
        warnings=[
            str(a.get("message", ""))[:200]
            for a in (verdict.get("advisories") or [])[:5]
            if isinstance(a, dict)
        ],
    )
