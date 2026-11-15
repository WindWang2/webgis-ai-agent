"""G46 单一编排面 —— REST 路由与 Pi 工具共用（UI 与 Pi 同一生产服务）。

生命周期：inspect_source（同代冻结 + 字段/类别候选）→ preview（组合条件
求值：匹配计数 + per-chart facet 统计 + where-scope handle + MapLibre
表达式）→ 视图 CRUD（命名保存 / CAS 修改 / 锚校验重开 / 归档）→
apply（图表组件原子写入 MapSpec）。

授权：HTTP 由路由层 ``require_owned_session``；Pi 由 ToolRegistry 注入
session_id（trusted_dispatch）。每次读取/保存/重开/导出重新冻结与校验
（ref/hash/handle 都不充当权限）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, Dict, List, Optional, Sequence

from app.services.feature_pages import count_matching, evaluate_where
from app.services.linked_exploration import contract
from app.services.linked_exploration.buckets import chart_statistics
from app.services.linked_exploration.errors import LinkedExplorationError
from app.services.linked_exploration.snapshot import FrozenSource, freeze_source_typed

logger = logging.getLogger(__name__)

_PLAN_JSON_MAX = 32 * 1024
_PREVIEW_JSON_MAX = 8 * 1024
#: 类别候选披露上限（inspect 阶段；不含机器值集合全量 —— 全量走 preview）
_INSPECT_CATEGORY_SAMPLE = 24
#: inspect 数值字段抽样行数（min/max 估计）
_INSPECT_SAMPLE_ROWS = 20_000


def _actor_owner_token(actor: Dict[str, Any]) -> Optional[str]:
    return actor.get("owner_token")


def _trusted(actor: Dict[str, Any]) -> bool:
    return bool(actor.get("trusted_session_id"))


# ─── inspect：同代源卡 + 字段/类别候选 ───────────────────────────────────

async def inspect_source(
    session_id: str,
    ref_id: str,
    actor: Dict[str, Any],
) -> Dict[str, Any]:
    """冻结源 → 源卡（字段 schema + 类别候选 top 值 + 数值字段 min/max）。

    预览样本可以有但必须标 sample：类别候选是**抽样估计**（精确机器值集
    合在 preview 的分桶结果里）；``is_sample`` 明示。
    """
    from app.services.session_data import session_data_manager

    frozen = await freeze_source_typed(
        session_id, ref_id,
        owner_token=_actor_owner_token(actor), trusted_dispatch=_trusted(actor))
    descriptor = await session_data_manager.get_ref_descriptor(
        session_id, frozen.ref_id)
    field_schema: Dict[str, Any] = {}
    if isinstance(descriptor, dict):
        raw = descriptor.get("field_schema")
        if isinstance(raw, dict):
            field_schema = raw

    fields_out: List[Dict[str, Any]] = []
    sample = frozen.features[:_INSPECT_SAMPLE_ROWS]
    for name in sorted(field_schema.keys()):
        meta = field_schema.get(name) or {}
        ftype = str(meta.get("type") or "unknown")
        entry: Dict[str, Any] = {
            "name": name,
            "type": ftype,
            "null_count": meta.get("null_count"),
        }
        if ftype in ("string", "boolean") or ftype == "unknown":
            seen: Dict[Any, int] = {}
            for feat in sample:
                props = feat.get("properties") if isinstance(feat, dict) else None
                v = props.get(name) if isinstance(props, dict) else None
                if v is not None and not isinstance(v, (list, dict)):
                    seen[v] = seen.get(v, 0) + 1
            top = sorted(seen.items(), key=lambda kv: (-kv[1], str(kv[0])))[:_INSPECT_CATEGORY_SAMPLE]
            entry["category_sample"] = [
                {"value": k, "count": c} for k, c in top
            ]
            entry["is_sample"] = len(sample) < frozen.feature_count
        if ftype == "number":
            vals = []
            for feat in sample:
                props = feat.get("properties") if isinstance(feat, dict) else None
                v = props.get(name) if isinstance(props, dict) else None
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                    vals.append(float(v))
            if vals:
                entry["min"], entry["max"] = min(vals), max(vals)
                entry["min_max_is_sample"] = len(sample) < frozen.feature_count
        fields_out.append(entry)

    return {
        "anchor": frozen.anchor(),
        "feature_count": frozen.feature_count,
        "fields": fields_out,
        "schema_complete": bool(descriptor.get("field_schema_complete", True))
            if isinstance(descriptor, dict) else False,
        "sample_note": (
            f"类别候选/数值范围基于前 {len(sample)} 行抽样（is_sample=true 时"
            "为估计值；精确值集在图表统计里）"
            if len(sample) < frozen.feature_count else "全量行统计"
        ),
    }


# ─── preview：组合条件求值（同代多视图投影） ─────────────────────────────

async def preview_conditions(
    session_id: str,
    ref_id: str,
    charts: Sequence[contract.ChartSpec],
    conditions: Sequence[Any],
    actor: Dict[str, Any],
    *,
    create_handle: bool = True,
) -> Dict[str, Any]:
    """组合条件求值权威面（facet 口径：每图自排除自身条件，其余保留）。

    返回：源锚、组合 where digest、完整匹配计数（全条件口径）、每图两套
    统计（``facet``=自排除口径 / ``full``=全条件匹配集口径）、MapLibre
    表达式（typed identity 精确编译）、where-scope selection handle。
    """
    frozen = await freeze_source_typed(
        session_id, ref_id,
        owner_token=_actor_owner_token(actor), trusted_dispatch=_trusted(actor))
    parsed = list(conditions)
    combined = contract.compile_combined_where(parsed)
    digest = contract.conditions_digest(parsed)

    match_count = await asyncio.to_thread(
        count_matching, frozen.features, where=combined, bbox=None)

    charts_out: List[Dict[str, Any]] = []
    for chart in charts:
        facet_where = contract.compile_combined_where(
            parsed, exclude_chart_id=chart.chart_id)
        facet_stats = await asyncio.to_thread(
            chart_statistics, frozen.features, chart, where=facet_where)
        full_stats = await asyncio.to_thread(
            chart_statistics, frozen.features, chart, where=combined)
        charts_out.append({
            "chart_id": chart.chart_id,
            "kind": chart.kind,
            "field": chart.field,
            "aggregation": chart.aggregation,
            "title": chart.title,
            "facet": {
                "scope": "excluding-own-condition",
                "note": "其它条件下的可选项（本图自身条件已排除）",
                "statistics": facet_stats,
            },
            "full": {
                "scope": "all-conditions",
                "note": "全部条件后的匹配集统计",
                "statistics": full_stats,
            },
        })

    map_filter = contract.where_to_maplibre(combined)
    handle_entry = None
    if create_handle and combined is not None:
        from app.services.selection_handles import create_selection

        handle_entry = await create_selection(
            session_id, frozen.ref_id, frozen.content_revision,
            {"kind": "where", "where": combined}, frozen.features)

    out: Dict[str, Any] = {
        "anchor": frozen.anchor(),
        "conditions_digest": digest,
        "combined_where": combined,
        "match_count": match_count,
        "match_count_exact": True,
        "total": frozen.feature_count,
        "map_filter": map_filter,
        "charts": charts_out,
        "completeness": "complete",
    }
    if handle_entry is not None:
        out["selection_handle"] = {
            "handle": handle_entry.handle,
            "count": handle_entry.count,
            "count_exact": handle_entry.count_exact,
            "ttl_seconds": 600,
        }
    return out


# ─── 命名视图：持久化 CRUD + 锚校验 ──────────────────────────────────────

def _row_to_view(row: Any, *, include_plan: bool = True) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "view_id": row.id,
        "title": row.title,
        "status": row.status,
        "revision": row.revision,
        "source_ref": row.source_ref,
        "source_revision": row.source_revision,
        "source_fingerprint": row.source_fingerprint,
        "normalized_digest": row.normalized_digest,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if include_plan:
        out["plan"] = row.plan
    if row.preview is not None:
        out["preview"] = row.preview
    return out


def _validate_plan_payload(plan_raw: Dict[str, Any]) -> contract.ExplorationPlan:
    try:
        return contract.ExplorationPlan.model_validate(plan_raw)
    except Exception as e:
        raise LinkedExplorationError(
            "INVALID_PLAN", f"计划校验失败：{e}", status_code=422) from e


def _plan_conditions(plan: contract.ExplorationPlan) -> List[Any]:
    try:
        return contract.parse_conditions(plan.conditions)
    except LinkedExplorationError as e:
        raise LinkedExplorationError(
            "INVALID_PLAN", f"计划内条件非法：{e.message}", status_code=422) from e


def _plan_charts(plan: contract.ExplorationPlan) -> List[contract.ChartSpec]:
    return list(plan.charts)


def _plan_digest_of(plan: contract.ExplorationPlan) -> str:
    return contract.plan_digest(plan.anchor, plan.charts, _plan_conditions(plan))


async def save_view(
    session_id: str,
    actor: Dict[str, Any],
    *,
    title: str,
    plan_raw: Dict[str, Any],
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """保存命名视图：重新冻结核对锚（保存时源必须仍与计划锚同代）。"""
    plan = _validate_plan_payload(plan_raw)
    if not title or len(title) > 200:
        raise LinkedExplorationError(
            "INVALID_TITLE", "标题必须是 1-200 字符", status_code=422)
    frozen = await freeze_source_typed(
        session_id, plan.anchor.ref_id,
        owner_token=_actor_owner_token(actor), trusted_dispatch=_trusted(actor))
    if (frozen.content_revision != plan.anchor.content_revision
            or frozen.content_fingerprint != plan.anchor.content_fingerprint):
        raise LinkedExplorationError(
            "STALE_SOURCE",
            f"计划锚已过期（计划 rev {plan.anchor.content_revision}，当前 "
            f"rev {frozen.content_revision}）—— 保存前请重新预览并更新计划锚",
            status_code=409,
            correction_hint="重新执行 preview 并用新锚保存。")
    blob = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    if len(blob) > _PLAN_JSON_MAX:
        raise LinkedExplorationError(
            "PLAN_OVER_LIMIT",
            f"计划过大（{len(blob)} B > {_PLAN_JSON_MAX}）", status_code=422)

    def _insert() -> Any:
        from app.core.database import SessionLocal
        from app.models.linked_exploration import ExplorationView

        with SessionLocal() as db:
            row = ExplorationView(
                session_id=session_id,
                project_id=project_id,
                created_by=actor.get("user_id"),
                org_id=actor.get("org_id"),
                title=title,
                status="draft",
                revision=1,
                plan=plan.model_dump(mode="json"),
                normalized_digest=_plan_digest_of(plan),
                source_ref=plan.anchor.ref_id,
                source_revision=plan.anchor.content_revision,
                source_fingerprint=plan.anchor.content_fingerprint,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    row = await asyncio.to_thread(_insert)
    return _row_to_view(row)


async def list_views(
    session_id: str,
    actor: Dict[str, Any],
    *,
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    def _query() -> List[Any]:
        from sqlalchemy import select as sa_select
        from app.core.database import SessionLocal
        from app.models.linked_exploration import ExplorationView

        with SessionLocal() as db:
            stmt = sa_select(ExplorationView).where(
                ExplorationView.session_id == session_id,
            ).order_by(ExplorationView.created_at.desc()).limit(100)
            rows = db.scalars(stmt).all()
            return [r for r in rows if include_archived or r.status != "archived"]

    rows = await asyncio.to_thread(_query)
    return [_row_to_view(r, include_plan=False) for r in rows]


async def get_view(
    view_id: str,
    session_id: str,
    actor: Dict[str, Any],
) -> Dict[str, Any]:
    row = await _owned_view(view_id, session_id)
    return _row_to_view(row)


async def _owned_view(view_id: str, session_id: str) -> Any:
    """会话归属校验：不区分『不存在』与『别人的』（无存在性泄漏）。"""
    def _query() -> Any:
        from app.core.database import SessionLocal
        from app.models.linked_exploration import ExplorationView

        with SessionLocal() as db:
            row = db.get(ExplorationView, view_id)
            if row is None or row.session_id != session_id:
                return None
            return row

    row = await asyncio.to_thread(_query)
    if row is None:
        raise LinkedExplorationError(
            "VIEW_NOT_FOUND", "视图不存在或不在当前会话（不区分两者）",
            status_code=404)
    return row


async def update_view(
    view_id: str,
    session_id: str,
    actor: Dict[str, Any],
    *,
    title: Optional[str],
    plan_raw: Optional[Dict[str, Any]],
    status: Optional[str],
    expected_revision: int,
) -> Dict[str, Any]:
    """CAS 修改（E25：并发作者 → stale_view_revision，不盲覆盖）。"""
    if status is not None and status not in ("draft", "archived"):
        raise LinkedExplorationError(
            "INVALID_STATUS", "status 必须是 draft/archived", status_code=422)
    row = await _owned_view(view_id, session_id)
    new_plan_raw = plan_raw if plan_raw is not None else row.plan
    plan = _validate_plan_payload(new_plan_raw)
    digest = _plan_digest_of(plan)
    blob = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    if len(blob) > _PLAN_JSON_MAX:
        raise LinkedExplorationError(
            "PLAN_OVER_LIMIT",
            f"计划过大（{len(blob)} B > {_PLAN_JSON_MAX}）", status_code=422)

    def _update() -> Any:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            fresh = db.get(type(row), row.id)
            if fresh is None:
                return None
            if fresh.revision != expected_revision:
                raise LinkedExplorationError(
                    "STALE_VIEW_REVISION",
                    f"视图已被其他作者修改（current revision={fresh.revision}）",
                    status_code=409,
                    detail={"current_revision": fresh.revision},
                )
            if title is not None:
                if not title or len(title) > 200:
                    raise LinkedExplorationError(
                        "INVALID_TITLE", "标题必须是 1-200 字符", status_code=422)
                fresh.title = title
            if status is not None:
                fresh.status = status
            if plan_raw is not None:
                fresh.plan = plan.model_dump(mode="json")
                fresh.normalized_digest = digest
                fresh.source_ref = plan.anchor.ref_id
                fresh.source_revision = plan.anchor.content_revision
                fresh.source_fingerprint = plan.anchor.content_fingerprint
            fresh.revision = expected_revision + 1
            db.commit()
            db.refresh(fresh)
            return fresh

    fresh = await asyncio.to_thread(_update)
    if fresh is None:
        raise LinkedExplorationError(
            "VIEW_NOT_FOUND", "视图不存在或不在当前会话", status_code=404)
    return _row_to_view(fresh)


async def delete_view(
    view_id: str,
    session_id: str,
    actor: Dict[str, Any],
    *,
    expected_revision: int,
) -> Dict[str, Any]:
    row = await _owned_view(view_id, session_id)

    def _delete() -> bool:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            fresh = db.get(type(row), row.id)
            if fresh is None:
                return False
            if fresh.revision != expected_revision:
                raise LinkedExplorationError(
                    "STALE_VIEW_REVISION",
                    f"视图已被其他作者修改（current revision={fresh.revision}）",
                    status_code=409,
                    detail={"current_revision": fresh.revision},
                )
            db.delete(fresh)
            db.commit()
            return True

    ok = await asyncio.to_thread(_delete)
    if not ok:
        raise LinkedExplorationError(
            "VIEW_NOT_FOUND", "视图不存在或不在当前会话", status_code=404)
    return {"deleted": True, "view_id": view_id}


async def open_view(
    view_id: str,
    session_id: str,
    actor: Dict[str, Any],
    *,
    revalidate: bool = True,
) -> Dict[str, Any]:
    """重开命名视图：按 pinned 锚校验当前源。

    - 可回读旧快照（锚匹配）→ ``state=ready``，返回计划 + 重新预览的
      完整统计（同代）；
    - 源已更新/字段漂移 → ``state=stale``，明确要求重新预览/重新绑定，
      绝不无声转为最新数据（E20/E21）。
    """
    row = await _owned_view(view_id, session_id)
    plan = _validate_plan_payload(row.plan)
    out: Dict[str, Any] = {
        "view": _row_to_view(row),
        "plan": row.plan,
    }
    if not revalidate:
        return out
    try:
        frozen = await freeze_source_typed(
            session_id, plan.anchor.ref_id,
            owner_token=_actor_owner_token(actor), trusted_dispatch=_trusted(actor))
    except LinkedExplorationError as e:
        # 源消失/无强版本 → 视图标记 stale（保留计划，要求重绑）
        out["state"] = "stale"
        out["stale_reason"] = {"code": e.code, "message": e.message}
        return out
    anchor = plan.anchor
    if (frozen.content_revision != anchor.content_revision
            or frozen.content_fingerprint != anchor.content_fingerprint):
        out["state"] = "stale"
        out["stale_reason"] = {
            "code": "SOURCE_ADVANCED",
            "message": (
                f"源已从 rev {anchor.content_revision} 推进到 "
                f"{frozen.content_revision}；视图保留原锚，需重新预览/重新绑定"
            ),
            "current_revision": frozen.content_revision,
        }
        return out
    # 锚匹配：同代重放（预览统计 + handle 都基于当前冻结）
    preview = await preview_conditions(
        session_id, plan.anchor.ref_id, _plan_charts(plan),
        _plan_conditions(plan), actor)
    out["state"] = "ready"
    out["preview"] = _bounded_preview(preview)
    return out


def _bounded_preview(preview: Dict[str, Any]) -> Dict[str, Any]:
    """preview 的有界投影（入行 ≤8KB；完整统计由前端实时 preview 获取）。"""
    charts_brief = []
    for c in preview.get("charts", []):
        facet = c.get("facet", {}).get("statistics", {})
        full = c.get("full", {}).get("statistics", {})
        charts_brief.append({
            "chart_id": c.get("chart_id"),
            "kind": c.get("kind"),
            "field": c.get("field"),
            "facet_matched": facet.get("matched"),
            "full_matched": full.get("matched"),
            "truncated": bool(facet.get("truncated") or full.get("truncated")),
        })
    blob = {
        "anchor": preview.get("anchor"),
        "conditions_digest": preview.get("conditions_digest"),
        "match_count": preview.get("match_count"),
        "total": preview.get("total"),
        "charts": charts_brief,
        "has_handle": "selection_handle" in preview,
    }
    if len(json.dumps(blob, ensure_ascii=False, default=str)) > _PREVIEW_JSON_MAX:
        blob["charts"] = [
            {"chart_id": c.get("chart_id")} for c in charts_brief
        ]
    return blob


# ─── apply：图表组件原子写入 MapSpec ─────────────────────────────────────

async def apply_view_to_mapspec(
    view_id: str,
    session_id: str,
    actor: Dict[str, Any],
    *,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """把视图图表组件写入 committed MapSpec（upsert；分步失败回滚）。

    组件 options 携带 viewId/chartId/field/kind/aggregation/layerId —— 渲染
    数据由前端实时 preview 获取（跟当前源版本），组件本身只存配置。
    apply 不改源数据、不改既有图层 filter（组合过滤是本域活动状态，
    保存不隐含换当前地图，E26）。
    """
    from app.services.mapspec_store import mapspec_store

    row = await _owned_view(view_id, session_id)
    plan = _validate_plan_payload(row.plan)
    if not plan.charts:
        raise LinkedExplorationError(
            "EMPTY_VIEW", "视图没有图表实例，无可应用内容", status_code=422)
    applied: List[str] = []
    revision = expected_revision
    try:
        for chart in plan.charts:
            component_id = f"exploration-chart-{chart.chart_id}"
            res = await mapspec_store.patch_component(
                session_id,
                component_id=component_id,
                component_type="exploration_chart_panel",
                options={
                    "viewId": view_id,
                    "chartId": chart.chart_id,
                    "kind": chart.kind,
                    "field": chart.field,
                    "aggregation": chart.aggregation,
                    "valueField": chart.value_field or "",
                    "bins": chart.bins if chart.kind == "range" else None,
                    "layerId": chart.layer_id or "",
                    "sourceRef": plan.anchor.ref_id,
                    "sourceRevision": plan.anchor.content_revision,
                    "title": chart.title or contract.chart_kind_label(chart.kind),
                },
                upsert=True,
                expected_revision=revision,
                # origin=agent：服务编排写入（用户经面板/Pi 触发，但写入方是
                # 领域服务）；expected_revision 透传可选 CAS（G41 apply 同语义）
                origin="agent",
                actor="linked_exploration",
                mutation_id=f"g46-apply-{view_id}-{chart.chart_id}",
            )
            if res.get("superseded"):
                raise LinkedExplorationError(
                    "MAPSPEC_SUPERSEDED",
                    "MapSpec 已被并发修改（superseded）—— 请刷新后重试",
                    status_code=409,
                    detail={"component_id": component_id})
            if res.get("success") is False or res.get("is_error"):
                raise LinkedExplorationError(
                    "APPLY_REJECTED",
                    f"组件写入被拒绝：{res.get('message') or res.get('warnings') or 'unknown'}",
                    status_code=409,
                    detail={"component_id": component_id,
                            "result": {k: v for k, v in res.items()
                                       if k in ("message", "warnings", "correction_hint")}})
            applied.append(component_id)
            revision = res.get("revision") or revision
    except LinkedExplorationError:
        await _rollback_components(session_id, applied)
        raise
    except Exception as e:  # noqa: BLE001 — 任何底层失败都不得留半套组件
        await _rollback_components(session_id, applied)
        raise LinkedExplorationError(
            "APPLY_FAILED", f"写入 MapSpec 失败：{e}", status_code=502) from e
    return {
        "applied_components": applied,
        "count": len(applied),
        "state": "applied",
    }


async def _rollback_components(session_id: str, component_ids: List[str]) -> None:
    """尽力回滚已写入的组件（失败披露在日志；不掩盖原错误）。"""
    from app.services.mapspec_store import mapspec_store

    for cid in component_ids:
        try:
            await mapspec_store.patch_component(
                session_id, component_id=cid, enabled=False,
                origin="agent", actor="linked_exploration-rollback",
                mutation_id=f"g46-rollback-{cid}",
            )
        except Exception:  # noqa: BLE001 — 回滚尽力而为
            logger.warning("G46 rollback failed for %s", cid, exc_info=True)


__all__ = [
    "inspect_source", "preview_conditions",
    "save_view", "list_views", "get_view", "update_view", "delete_view",
    "open_view", "apply_view_to_mapspec",
]
