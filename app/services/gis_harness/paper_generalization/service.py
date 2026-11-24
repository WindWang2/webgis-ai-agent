"""paper_generalization 会话服务（WP3/WP5）—— 纸面目标比例尺综合闭环。

三个 Agent 可发现面（全部经 ToolRegistry 注册，见
``app/tools/paper_scale_tools.py``）：

- ``inspect_paper_scale_generalization``：只读 —— 尺度证据（等效 zoom、
  比例尺变化披露、几何族、重要性证据、消除/聚合阈值预览）；
- ``preview_paper_scale_generalization``：零突变 —— 完整编译（管线 +
  工件内存态 + QA 审计），不落 ref、不改 MapSpec；
- ``apply_paper_scale_generalization``：preview 同上；commit —— 工件存
  **新 ref**（非破坏）→ UpsertSource ×1 + UpsertLayer ×1 经
  ``apply_gis_mutation`` 顺序 CAS 链落 MapSpec（paper_plans 冻结计划 +
  representation window 精确裁剪插入）→ 回执环。

纪律（与 scale_profile.v1 服务同款）：

- 统计/分类口径恒 original_source；原始 ref 永不被修改；
- QA 阻断：qa_summary.blocking > 0 → 拒绝 commit（fail-closed），
  检查行随结构化结果返回；
- 幂等：operation_id 重放返回存证回执；断点续链复用已存工件 ref；
- 诚实降级：无重要性证据不取舍；点低密度不聚合；不支持栅格族
  （栅格重采样走既有能力，不进本通道）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.lib.cartography.multiscale.paper_scale import (
    MAP_PURPOSES,
    PageSpec,
    FrameViewport,
    PublicationScaleContract,
    contract_fingerprint,
    equivalent_display_zoom,
    parse_denominator_text,
    parse_page_text,
    scale_variation_report,
    strategy_hints_of,
)
from app.lib.cartography.multiscale.profile import SourceRevision
from app.lib.cartography.multiscale.publication_compile import (
    SCHEMA_VERSION as PLAN_SCHEMA_VERSION,
    plan_with_artifact_ref,
    upsert_representation_window,
)
from app.services.gis_harness.scale_profile.service import (
    _fc_content_fingerprint,
    _geometry_family_of,
    _latitude_hint,
    _prepare,
)

logger = logging.getLogger(__name__)

SERVICE_SCHEMA_VERSION = "paper_scale_service.v1"
RECEIPTS_KEY = "_paper_scale_receipts"
MAX_RECEIPT_RING = 8

#: 工具入参界（与 scale_profile 工具同量级纪律）。
_MAX_KEPT_VALUES = 32
_MAX_SUM_FIELDS = 8


class PaperScaleError(Exception):
    """服务级 typed 错误（工具面转 {"error": ...}，不泄漏栈）。"""


def _norm_text_list(values: Optional[Sequence[Any]], cap: int,
                    label: str) -> List[str]:
    out: List[str] = []
    for v in values or []:
        if v is None:
            continue
        sv = str(v)
        if sv and sv not in out:
            out.append(sv)
        if len(out) >= cap:
            break
    if len(out) >= cap:
        logger.debug("paper_scale: %s truncated at %d", label, cap)
    return out


def _resolve_denominator(denominator: Optional[int],
                         denominator_text: Optional[str]) -> int:
    if denominator is not None:
        try:
            n = int(denominator)
        except (TypeError, ValueError):
            raise PaperScaleError(f"denominator 非整数：{denominator!r}")
        return n
    if denominator_text:
        parsed = parse_denominator_text(denominator_text)
        if parsed is None:
            raise PaperScaleError(
                f"无法解析比例尺文本：{denominator_text!r}"
                "（示例：1:50万 / 500000）")
        return parsed
    raise PaperScaleError("缺少目标比例尺（denominator 或 denominator_text）")


def _resolve_page(**kw) -> Tuple[str, str, Optional[float]]:
    """页面参数 → (format_id, orientation, dpi|None)。文本优先于显式。"""
    page_text = kw.get("page_text")
    if page_text:
        parsed = parse_page_text(page_text)
        if parsed is None:
            raise PaperScaleError(
                f"无法解析页面文本：{page_text!r}（示例：A4横版 / A3 portrait）")
        fmt, orientation = parsed
        dpi = kw.get("dpi")
        return fmt, orientation, (float(dpi) if dpi is not None else None)
    fmt = str(kw.get("format_id") or "A4")
    orientation = str(kw.get("orientation") or "landscape")
    dpi = kw.get("dpi")
    return fmt, orientation, (float(dpi) if dpi is not None else None)


def _build_contract(
    ctx: Dict[str, Any],
    *,
    denominator: int,
    purpose: str,
    format_id: str,
    orientation: str,
    dpi: Optional[float],
    frame_width_mm: Optional[float],
    frame_height_mm: Optional[float],
) -> PublicationScaleContract:
    """会话证据（descriptor bbox / 纬度）+ 入参 → 契约。"""
    if purpose not in MAP_PURPOSES:
        raise PaperScaleError(
            f"未知用途 {purpose!r}；词表：{', '.join(MAP_PURPOSES)}")
    bbox = ctx["descriptor"].get("bbox")
    data_bbox = [float(v) for v in bbox] if (
        isinstance(bbox, (list, tuple)) and len(bbox) == 4) else None
    page_kwargs: Dict[str, Any] = {
        "format_id": format_id, "orientation": orientation,
    }
    if dpi is not None:
        page_kwargs["dpi"] = dpi
    page = PageSpec(**page_kwargs)
    # 图框缺省 = 页面 90%（留边距）；显式 mm 覆盖
    fw = float(frame_width_mm) if frame_width_mm is not None \
        else round(page.width_mm * 0.9, 1)
    fh = float(frame_height_mm) if frame_height_mm is not None \
        else round(page.height_mm * 0.9, 1)
    frame = FrameViewport(
        width_mm=fw, height_mm=fh,
        scale_reference_lat=_latitude_hint(ctx["descriptor"]),
        data_bbox=data_bbox,
    )
    return PublicationScaleContract(
        target_denominator=denominator,
        purpose=purpose,
        page=page,
        frame=frame,
        generated_by="paper_scale_service",
    )


async def _prepared_ctx(session_id: str, layer_target: str) -> Dict[str, Any]:
    try:
        return await _prepare(session_id, layer_target)
    except Exception as exc:  # noqa: BLE001 — 下游服务错误转 typed
        raise PaperScaleError(str(exc)) from exc


# ── inspect：只读证据面 ──────────────────────────────────────────────────


async def inspect_paper_scale_generalization(
    session_id: str,
    layer_target: str,
    *,
    denominator: Optional[int] = None,
    denominator_text: Optional[str] = None,
    page_text: Optional[str] = None,
    purpose: str = "reference",
    dpi: Optional[float] = None,
) -> Dict[str, Any]:
    ctx = await _prepared_ctx(session_id, layer_target)
    denom = _resolve_denominator(denominator, denominator_text)
    fmt, orientation, dpi_res = _resolve_page(
        page_text=page_text, dpi=dpi)
    contract = _build_contract(
        ctx, denominator=denom, purpose=purpose, format_id=fmt,
        orientation=orientation, dpi=dpi_res,
        frame_width_mm=None, frame_height_mm=None)
    variation = scale_variation_report(contract)
    zoom = equivalent_display_zoom(
        denom, float(contract.frame.scale_reference_lat)) \
        if contract.projection == "web_mercator" else None
    hints = strategy_hints_of(contract) if not _contract_errors(contract) else None
    family = _geometry_family_of(ctx["descriptor"], ctx["layer"])
    derivation = ctx["derivation"]
    return {
        "status": "ok",
        "schema_version": SERVICE_SCHEMA_VERSION,
        "layer_id": ctx["layer"].get("id"),
        "geometry_family": family,
        "supported_family": family in ("point", "line", "polygon"),
        "target_denominator": denom,
        "page": {"format_id": fmt, "orientation": orientation,
                 "dpi": page_dpi_of(contract)},
        "equivalent_display_zoom": round(zoom, 6) if zoom is not None else None,
        "scale_variation": variation,
        "strategy_hints": hints,
        "importance": {
            "selected": derivation.selected.model_dump() if derivation.selected else None,
            "reason_codes": derivation.reason_codes[:8],
        },
        "source": {
            "ref": ctx["anchor"],
            "feature_count": ctx["feature_count"],
            "content_revision": ctx["descriptor"].get("content_revision"),
        },
        "existing_paper_plans": sorted_keys_of_plans(ctx["layer"]),
        "disclosures": ["统计口径恒 original_source；综合只作用于显示工件"],
    }


def page_dpi_of(contract: PublicationScaleContract) -> float:
    return float(contract.page.dpi)


def sorted_keys_of_plans(layer: Dict[str, Any]) -> List[str]:
    plans = ((layer.get("scale_profile") or {}).get("paper_plans") or {})
    return sorted(plans.keys()) if isinstance(plans, dict) else []


def _contract_errors(contract: PublicationScaleContract) -> List[str]:
    from app.lib.cartography.multiscale.paper_scale import validate_contract
    return validate_contract(contract)


# ── preview / apply 共用编译 ─────────────────────────────────────────────


def _compile_from_ctx(
    ctx: Dict[str, Any],
    *,
    contract: PublicationScaleContract,
    kept_values: Optional[Sequence[Any]],
    sum_fields: Optional[Sequence[Any]],
) -> Tuple[Any, Dict[str, Any]]:
    from app.lib.cartography.multiscale.publication_compile import (
        compile_paper_scale_plan as _compile,
    )

    family = _geometry_family_of(ctx["descriptor"], ctx["layer"])
    if family not in ("point", "line", "polygon"):
        raise PaperScaleError(
            f"几何族 {family!r} 不支持纸面综合（栅格请走重采样/金字塔能力；"
            "矢量族仅 point/line/polygon）")
    d = ctx["descriptor"]
    source = SourceRevision(
        ref=ctx["anchor"],
        content_revision=d.get("content_revision") if isinstance(
            d.get("content_revision"), int) else None,
        data_fingerprint=d.get("data_fingerprint"),
        feature_count=ctx["feature_count"],
        geometry_family=family,
    )
    result = _compile(
        layer_id=str(ctx["layer"].get("id")),
        feature_collection=ctx["fc"],
        source=source,
        contract=contract,
        importance=ctx["derivation"],
        kept_values=_norm_text_list(kept_values, _MAX_KEPT_VALUES,
                                    "kept_values") or None,
        sum_fields=_norm_text_list(sum_fields, _MAX_SUM_FIELDS,
                                   "sum_fields"),
        latitude_hint=float(contract.frame.scale_reference_lat),
        generated_by="paper_scale_service",
    )
    return result, {"family": family, "source": source}


async def preview_paper_scale_generalization(
    session_id: str,
    layer_target: str,
    *,
    denominator: Optional[int] = None,
    denominator_text: Optional[str] = None,
    page_text: Optional[str] = None,
    purpose: str = "reference",
    dpi: Optional[float] = None,
    kept_values: Optional[Sequence[Any]] = None,
    sum_fields: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    ctx = await _prepared_ctx(session_id, layer_target)
    contract = _contract_from_args(
        ctx, denominator=denominator, denominator_text=denominator_text,
        page_text=page_text, purpose=purpose, dpi=dpi)
    result, meta = _compile_from_ctx(
        ctx, contract=contract, kept_values=kept_values, sum_fields=sum_fields)
    return {
        "status": "preview",
        "schema_version": SERVICE_SCHEMA_VERSION,
        "plan_schema_version": PLAN_SCHEMA_VERSION,
        "layer_id": result.plan["layer_id"],
        "fingerprint": result.fingerprint,
        "family": meta["family"],
        "pipeline": result.plan["pipeline"],
        "stats": result.plan["stats"],
        "qa_summary": result.plan["qa_summary"],
        "semantic_checks": result.semantic_checks,
        "disclosures": result.disclosures,
        "note": "零突变预览：未存 ref、未改 MapSpec；QA blocking>0 时 commit 会被拒绝",
    }


def _contract_from_args(
    ctx: Dict[str, Any],
    *,
    denominator: Optional[int],
    denominator_text: Optional[str],
    page_text: Optional[str],
    purpose: str,
    dpi: Optional[float],
    frame_width_mm: Optional[float] = None,
    frame_height_mm: Optional[float] = None,
) -> PublicationScaleContract:
    denom = _resolve_denominator(denominator, denominator_text)
    fmt, orientation, dpi_res = _resolve_page(page_text=page_text, dpi=dpi)
    contract = _build_contract(
        ctx, denominator=denom, purpose=purpose, format_id=fmt,
        orientation=orientation, dpi=dpi_res,
        frame_width_mm=frame_width_mm, frame_height_mm=frame_height_mm)
    errors = _contract_errors(contract)
    if errors:
        raise PaperScaleError(f"契约非法：{errors}")
    return contract


# ── apply：preview / commit（CAS 链 + 回执环）────────────────────────────


async def apply_paper_scale_generalization(
    session_id: str,
    layer_target: str,
    *,
    mode: str = "preview",
    denominator: Optional[int] = None,
    denominator_text: Optional[str] = None,
    page_text: Optional[str] = None,
    purpose: str = "reference",
    dpi: Optional[float] = None,
    kept_values: Optional[Sequence[Any]] = None,
    sum_fields: Optional[Sequence[Any]] = None,
    operation_id: Optional[str] = None,
    expected_revision: Optional[int] = None,
    note: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Dict[str, Any]:
    op_id = operation_id or f"pgen:{int(time.time() * 1000)}:{layer_target}"
    prior = await _find_receipt(session_id, op_id, statuses=("committed",))
    if prior is not None:
        prior = dict(prior)
        prior["replayed"] = True
        return prior

    ctx = await _prepared_ctx(session_id, layer_target)
    contract = _contract_from_args(
        ctx, denominator=denominator, denominator_text=denominator_text,
        page_text=page_text, purpose=purpose, dpi=dpi)
    result, meta = _compile_from_ctx(
        ctx, contract=contract, kept_values=kept_values, sum_fields=sum_fields)

    if mode != "commit":
        receipt = await preview_paper_scale_generalization(
            session_id, layer_target, denominator=denominator,
            denominator_text=denominator_text, page_text=page_text,
            purpose=purpose, dpi=dpi, kept_values=kept_values,
            sum_fields=sum_fields)
        receipt.update({
            "operation_id": op_id, "mode": "preview",
            "note": (note or "")[:160] or None,
            "commit_hint": "同参 + mode=commit 提交（工件将存为新 ref，源数据不动）",
        })
        return receipt

    # QA fail-closed：blocking > 0 拒绝 commit（诚实门禁，不静默上版）
    if result.plan["qa_summary"]["blocking"] > 0:
        return {
            "status": "qa_blocked",
            "schema_version": SERVICE_SCHEMA_VERSION,
            "operation_id": op_id,
            "layer_id": result.plan["layer_id"],
            "qa_summary": result.plan["qa_summary"],
            "semantic_checks": result.semantic_checks,
            "error": "QA 存在 fail 检查 —— 拒绝冻结上版（fail-closed）",
        }

    # 工件落新 ref（非破坏）
    from app.services.session_data import session_data_manager

    ref_id = await session_data_manager.store(
        session_id, result.artifact, prefix="pgen")
    source_id = f"{result.plan['layer_id']}__paper@{contract.target_denominator}"

    # 冻结计划 + 表达窗口（等效 zoom 跨度 ±0.5，钳制 [0,24)）
    zoom = equivalent_display_zoom(
        int(contract.target_denominator),
        float(contract.frame.scale_reference_lat))
    if zoom is None:
        raise PaperScaleError("等效 zoom 换算失败（分母/DPI 越域）")
    half = 0.5
    win_a = max(0.0, round(zoom - half, 3))
    win_b = min(24.0, round(zoom + half, 3))
    if win_b <= win_a:
        win_b = min(24.0, win_a + 0.1)
    mode_word = "aggregate_grid" if meta["family"] == "point" else "generalized"
    window = {"min_zoom": win_a, "max_zoom": win_b, "mode": mode_word,
              "source": source_id,
              "reason_codes": ["PSC.PAPER_FROZEN"]}
    frozen = plan_with_artifact_ref(
        result.plan, artifact_ref=ref_id, artifact_source_id=source_id,
        window=window)
    frozen["contract_fingerprint"] = contract_fingerprint(contract)

    layer = dict(ctx["layer"])
    scale_profile = dict(layer.get("scale_profile") or {})
    plans = dict(scale_profile.get("paper_plans") or {})
    plans[result.fingerprint] = frozen
    scale_profile["paper_plans"] = plans
    layer["scale_profile"] = scale_profile
    rep = layer.get("representation") if isinstance(
        layer.get("representation"), dict) else {}
    windows = upsert_representation_window(
        rep.get("windows") or [], window)
    layer["representation"] = {**rep, "windows": windows}

    from app.services.mapspec.intents import (
        UpsertLayerIntent,
        UpsertSourceIntent,
    )
    from app.services.gis_world_state.mutation import apply_gis_mutation

    intents: List[Any] = [
        UpsertSourceIntent(source_id=source_id, source={
            "type": "geojson",
            "ref": ref_id,
            "ref_id": ref_id,
            "data_fingerprint": _fc_content_fingerprint(result.artifact),
            "profile": {
                "featureCount": len(result.artifact.get("features") or []),
                "generated_by": PLAN_SCHEMA_VERSION,
                "derived_from": ctx["anchor"],
            },
        }),
        UpsertLayerIntent(layer=layer),
    ]

    chain_rev = int(expected_revision) if expected_revision is not None \
        else None
    last_res = None
    for slot, intent in enumerate(intents):
        res = await apply_gis_mutation(
            session_id, intent,
            origin="agent", actor="paper_scale_service",
            expected_revision=chain_rev,
            mutation_id=f"op:{op_id}:{slot}",
            turn_id=turn_id,
            reason=note or "apply paper scale generalization",
        )
        if res.is_error or res.superseded:
            partial = {
                "status": "partial",
                "schema_version": SERVICE_SCHEMA_VERSION,
                "operation_id": op_id,
                "layer_id": result.plan["layer_id"],
                "committed_slots": slot,
                "artifact_ref": ref_id,
                "artifact_source_id": source_id,
                "error": res.error_msg or (
                    "superseded: MapSpec revision moved during apply"),
                "at": round(time.time(), 3),
            }
            await _persist_receipt(session_id, partial)
            return {
                **partial,
                "hint": "部分 intent 已提交；同 operation_id 重放复用已存工件",
            }
        chain_rev = int(res.mutation_revision) if res.mutation_revision else None
        last_res = res
    res = last_res

    receipt = {
        "status": "committed",
        "schema_version": SERVICE_SCHEMA_VERSION,
        "operation_id": op_id,
        "layer_id": result.plan["layer_id"],
        "plan_fingerprint": result.fingerprint,
        "mapspec_revision": int(res.mutation_revision) if res else None,
        "artifact_ref": ref_id,
        "artifact_source_id": source_id,
        "export_window": window,
        "qa_summary": result.plan["qa_summary"],
        "source_untouched": ctx["anchor"],
        "classification_basis": "original_source",
        "note": (note or "")[:160] or None,
        "at": round(time.time(), 3),
    }
    await _persist_receipt(session_id, receipt)
    return receipt


# ── 回执环（幂等重放；与 scale_profile 同纪律）──────────────────────────


async def _find_receipt(
    session_id: str, operation_id: str,
    *, statuses: Tuple[str, ...] = ("committed",),
) -> Optional[Dict[str, Any]]:
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id) or {}
    except Exception:  # noqa: BLE001
        return None
    ring = state.get(RECEIPTS_KEY)
    if isinstance(ring, list):
        for row in ring:
            if isinstance(row, dict) and row.get("operation_id") == operation_id \
                    and row.get("status") in statuses:
                return dict(row)
    return None


async def _persist_receipt(session_id: str, receipt: Dict[str, Any]) -> None:
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id) or {}
        ring = [r for r in (state.get(RECEIPTS_KEY) or [])
                if isinstance(r, dict)]
        op_id = receipt.get("operation_id")
        if isinstance(op_id, str):
            ring = [r for r in ring if r.get("operation_id") != op_id]
        ring.insert(0, dict(receipt))
        await session_data_manager.set_map_state(
            session_id, RECEIPTS_KEY, ring[:MAX_RECEIPT_RING])
    except Exception:  # noqa: BLE001 — 回执 best-effort 不阻断提交
        logger.warning("[paper_scale] receipt persist failed", exc_info=True)


__all__ = [
    "SERVICE_SCHEMA_VERSION",
    "PaperScaleError",
    "inspect_paper_scale_generalization",
    "preview_paper_scale_generalization",
    "apply_paper_scale_generalization",
]
