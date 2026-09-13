"""SituationCompiler —— GISSituation 唯一生产编译器（方向 2 S2，ADR-0180）。

从权威 stores 读取并编译单一 GISSituation：
- **单次 map_state 全量读**（PERF-08 / #1068(E-5) 纪律）；其余为定向读；
- **固定扇出**：一次 ``asyncio.gather``（5 个 store 调用），无递归读；
- **descriptor-first**：数据集只读 O(1) 描述符（≤MAX_DATASET_FACTS 个），
  绝不 resolve payload —— Zero Big Data in Context；
- **partial source unavailable**：任一权威源读取失败不失败编译 —— 相关
  事实置 ``unavailable`` 并记入 ``evidence.sources_unavailable``；
- **确定性**：同 store 输入同输出；``compiled_at`` 由调用方传入（session
  冻结时钟策略，#388 prefix-cache 纪律），模块内无 wall-clock。

禁止依赖 API route；route 层（chat.py）只调用本模块。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.services.gis_situation.contract import (
    MAX_DATASET_FACTS,
    MAX_INTERACTIONS,
    MAX_LAYER_SUMMARIES,
    MAX_PROGRESS_ROWS,
    MAX_PROVENANCE_EVIDENCE,
    MAX_SOURCE_SUMMARIES,
    AnalysisContext,
    CartographicContext,
    ConstraintsContext,
    DataContext,
    DeliveryContext,
    GeographicContext,
    GISSituation,
    InteractionContext,
    MapContext,
    SituationEvidence,
    SituationIdentity,
    SituationRevision,
    TemporalContext,
    UserGoalContext,
)
from app.services.gis_situation.facts import (
    SitFact,
    known,
    stale,
    unavailable,
    unknown,
)

logger = logging.getLogger(__name__)

#: 与 context_builder._PENDING_STATUSES 同语义（进行中后台任务）。
_PENDING_STATUSES = frozenset({
    "export_task_created",
    "export_batch_task_created",
    "change_detection_task_started",
    "analysis_task_started",
    "started",
})

#: 与 gis_world_state.state 同语义的用户 durable 隐藏决策判定。
_USER_HIDDEN_KIND = "PatchLayerPresentationIntent"


def situation_enabled() -> bool:
    """kill-switch（DC-2）：GIS_SITUATION_CONTEXT=0 回落 legacy env block。"""
    import os

    return os.getenv("GIS_SITUATION_CONTEXT", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _num(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _layer_summary(layer: Dict[str, Any]) -> Dict[str, Any]:
    """有界图层摘要（与 gis_world_state._layer_summary 同投影面）。"""
    layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
    role = layer.get("context_role") or layer.get("role")
    summary: Dict[str, Any] = {
        "id": layer.get("id"),
        "type": layer.get("type"),
        "visible": layout.get("visibility", "visible") != "none",
    }
    if role:
        summary["role"] = role
    return summary


def _scale_from_zoom(zoom: Any) -> str:
    """zoom → 尺度档位（derived 派生事实，非新事实源）。"""
    z = _num(zoom)
    if z is None:
        return "unknown"
    if z >= 13:
        return "street"
    if z >= 9:
        return "city"
    if z >= 5:
        return "regional"
    return "national_or_global"


async def _gather_sources(
    session_id: str, store: Any, mapspec_store: Any,
):
    """固定扇出：5 个权威源一次并发读取。返回 ``(values, ok, unavailable)``；
    失败源以 None 占位并记名（partial source unavailable 降级面）。"""
    names = ("map_state", "mapspec", "session_plan", "refs", "event_log")
    unavailable: List[str] = []

    async def _guard(name: str, coro):
        try:
            return await coro
        except Exception as e:  # noqa: BLE001 — 单源失败不失败编译
            logger.warning(
                "[gis_situation] source %s unavailable for %s: %s",
                name, session_id, e,
            )
            unavailable.append(name)
            return None

    state, mapspec, plan, refs, event_log = await asyncio.gather(
        _guard("map_state", store.get_map_state(session_id)),
        _guard("mapspec", mapspec_store.get_mapspec(session_id)),
        _guard("session_plan", _load_plan(session_id)),
        _guard("refs", store.list_refs(session_id)),
        _guard("event_log", store.get_event_log(session_id)),
    )
    ok = [n for n, v in zip(names, (state, mapspec, plan, refs, event_log))
          if v is not None]
    # mapspec 空字典与读取成功不可区分（store 返回 {} 表示"无 spec"，是
    # 正常态）：仅异常进入 unavailable。plan=None 是"无信封"正常态。
    return (state, mapspec, plan, refs, event_log), ok, unavailable


async def _load_plan(session_id: str):
    from app.services.session_plan import load_session_plan

    return await load_session_plan(session_id)


def _safe(fn, *args, **kwargs):
    """同步派生的兜底：异常 → None（派生面绝不失败编译）。"""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return None


def compile_geographic(
    map_state: Dict[str, Any],
    mapspec: Dict[str, Any],
    pre_turn: Optional[Dict[str, Any]],
    evidence: SituationEvidence,
) -> GeographicContext:
    # viewport 取数优先级：pre-turn 前端快照（本轮最新用户声明）>
    # WS 连续通道 map_state["viewport"]。runtime observation.viewport 是
    # 对账证据，不作"用户在看哪"的第一来源（D1 裁决：pre-turn 观察通道
    # 为 viewport 的 authoritative projection）。
    viewport_value: Any = None
    viewport_source = "map_state.viewport"
    if isinstance(pre_turn, dict) and pre_turn.get("viewport"):
        viewport_value = pre_turn.get("viewport")
        viewport_source = "_cartographic_context_observation.viewport"
    elif map_state.get("viewport"):
        viewport_value = map_state.get("viewport")
    viewport_fact = (
        known(viewport_value, source=viewport_source)
        if viewport_value else unknown(source="map_state.viewport")
    )

    view = mapspec.get("view") if isinstance(mapspec.get("view"), dict) else {}
    framed = (
        known(view, source="mapspec.view", revision=_num(
            map_state.get("_cartographic_mutation_revision")))
        if view else unknown(source="mapspec.view")
    )

    scope_name = unknown(source="viewport_naming")
    center = (viewport_value or {}).get("center") if isinstance(
        viewport_value, dict) else None
    if isinstance(center, (list, tuple)) and len(center) == 2:
        def _lookup():
            from app.services.viewport_naming import lookup

            name = lookup(float(center[0]), float(center[1]))
            if not name:
                raise ValueError("no cached region name")
            return name

        name = _safe(_lookup)  # 只读 lookup；不 schedule_populate（无副作用纪律）
        if name:
            scope_name = known(name, source="viewport_naming.lookup")

    user_location_value = (pre_turn or {}).get("user_location") if isinstance(
        pre_turn, dict) else None
    user_location = (
        known(user_location_value, source="_cartographic_context_observation.user_location")
        if user_location_value else unknown(source="_cartographic_context_observation.user_location")
    )

    zoom = (viewport_value or {}).get("zoom") if isinstance(
        viewport_value, dict) else None
    scale = known(
        _scale_from_zoom(zoom), source="derived.zoom"
    ) if zoom is not None else unknown(source="derived.zoom")

    crs = unknown(source="ref_descriptor.crs")  # 有数据时由 _compile_data 回填
    return GeographicContext(
        viewport=viewport_fact, framed_view=framed, scope_name=scope_name,
        user_location=user_location, scale=scale, crs=crs,
    )


def compile_user_goal(
    plan: Any, mutation_revision: Optional[int],
) -> UserGoalContext:
    goal_source = "session_plan.user_goal"
    if plan is None:
        goal = unknown(source=goal_source)
        plan_id = unknown(source="session_plan.gis_chapter.plan_id")
        recipe_id = unknown(source="session_plan.gis_chapter.recipe_id")
        progress: SitFact = unknown(source="session_plan.progress")
    else:
        goal = known(str(getattr(plan, "user_goal", "") or ""), source=goal_source)
        chapter = getattr(plan, "gis_chapter", None)
        chapter = chapter if isinstance(chapter, dict) else {}
        plan_id = (
            known(str(chapter.get("plan_id") or ""), source="session_plan.gis_chapter.plan_id")
            if chapter.get("plan_id") else unknown(source="session_plan.gis_chapter.plan_id")
        )
        recipe_id = (
            known(str(chapter.get("recipe_id") or ""), source="session_plan.gis_chapter.recipe_id")
            if chapter.get("recipe_id") else unknown(source="session_plan.gis_chapter.recipe_id")
        )
        rows = [
            {"capability": str(row.capability), "status": str(row.status)}
            for row in (getattr(plan, "progress", None) or ())
            if getattr(row, "capability", "")
        ][:MAX_PROGRESS_ROWS]
        progress = (
            known(rows, source="session_plan.progress", revision=mutation_revision)
            if rows else unknown(source="session_plan.progress")
        )
    return UserGoalContext(
        goal=goal, plan_id=plan_id, recipe_id=recipe_id, progress=progress,
    )


def compile_temporal(
    map_state: Dict[str, Any], plan: Any, evidence: SituationEvidence,
) -> TemporalContext:
    started_at = map_state.get("_started_at") or ""
    session_started = (
        known(str(started_at), source="session_store.started_at")
        if started_at else unknown(source="session_store.started_at")
    )
    chapter = getattr(plan, "gis_chapter", None) if plan is not None else None
    chapter = chapter if isinstance(chapter, dict) else {}
    period_raw = chapter.get("time") if isinstance(chapter.get("time"), dict) else None
    requested_period = (
        known(period_raw, source="session_plan.gis_chapter.time")
        if period_raw else unknown(source="session_plan.gis_chapter.time")
    )
    return TemporalContext(
        session_started_at=session_started,
        requested_period=requested_period,
        data_coverage=unknown(source="ref_descriptor"),  # v1：描述符无时间维
        active_time_slice=unknown(source="temporal"),
    )


def compile_map(
    map_state: Dict[str, Any],
    mapspec: Dict[str, Any],
    observation: Optional[Dict[str, Any]],
    evidence: SituationEvidence,
) -> MapContext:
    revision = _num(map_state.get("_cartographic_mutation_revision"))
    rev_fact = (
        known(revision, source="map_state._cartographic_mutation_revision")
        if revision is not None
        else unknown(source="map_state._cartographic_mutation_revision")
    )

    fingerprint = unknown(source="cartographic_fingerprint")
    if isinstance(mapspec, dict) and mapspec:
        fp = _safe(lambda: _cartographic_fingerprint(mapspec))
        if fp:
            fingerprint = known(fp, source="cartographic_fingerprint", revision=revision)

    raw_layers = list(mapspec.get("layers") or []) if isinstance(mapspec, dict) else []
    summaries = [
        s for s in (_safe(_layer_summary, ln) for ln in raw_layers
                    if isinstance(ln, dict))
        if isinstance(s, dict)
    ]
    omitted_layers = max(0, len(summaries) - MAX_LAYER_SUMMARIES)
    layers_value = summaries[:MAX_LAYER_SUMMARIES]
    if omitted_layers:
        evidence.omitted.append(f"map.layers(+{omitted_layers})")
    layers_fact = (
        known(layers_value, source="mapspec.layers", revision=revision)
        if layers_value else unknown(source="mapspec.layers")
    )
    layer_count_fact = known(
        len(raw_layers), source="mapspec.layers", revision=revision,
    ) if raw_layers else unknown(source="mapspec.layers")

    raw_sources = mapspec.get("sources") if isinstance(mapspec, dict) else None
    source_summaries: List[Dict[str, Any]] = []
    if isinstance(raw_sources, dict):
        for sid in sorted(raw_sources):
            entry = raw_sources.get(sid)
            if not isinstance(entry, dict):
                continue
            profile = entry.get("profile") if isinstance(entry.get("profile"), dict) else {}
            summary: Dict[str, Any] = {"id": sid, "type": entry.get("type")}
            ref = entry.get("ref_id") or entry.get("ref")
            if isinstance(ref, str) and ref:
                summary["ref"] = ref
            fc = profile.get("featureCount", profile.get("feature_count"))
            if fc is not None:
                summary["feature_count"] = fc
            source_summaries.append(summary)
    omitted_sources = max(0, len(source_summaries) - MAX_SOURCE_SUMMARIES)
    source_summaries = source_summaries[:MAX_SOURCE_SUMMARIES]
    if omitted_sources:
        evidence.omitted.append(f"map.sources(+{omitted_sources})")
    sources_fact = (
        known(source_summaries, source="mapspec.sources", revision=revision)
        if source_summaries else unknown(source="mapspec.sources")
    )

    basemap_value = map_state.get("base_layer")
    basemap = (
        known(str(basemap_value), source="map_state.base_layer")
        if basemap_value else unknown(source="map_state.base_layer")
    )

    observed = unknown(source="_cartographic_observation")
    if isinstance(observation, dict) and observation:
        summary = _safe(_observation_summary, observation)
        if summary is not None:
            observed = known(
                summary, source="_cartographic_observation",
                revision=_num(observation.get("mapspec_revision")),
                observed_at=str(observation.get("observed_at") or ""),
            )
    return MapContext(
        desired_revision=rev_fact, fingerprint=fingerprint,
        layers=layers_fact, layer_count=layer_count_fact,
        sources=sources_fact, basemap=basemap, observed=observed,
    )


def compile_cartographic(
    chapter: Dict[str, Any],
    review: Any,
    current_fingerprint: Optional[str],
    mutation_revision: Optional[int],
) -> CartographicContext:
    """verdict 三态：门通过=known；有 review 但指纹失配=stale；无 review=unknown。

    复用 ``should_inject_verdict``（单一守卫实现），不重建 verdict 语义。
    product/render/recipe 事实从 chapter.map_product 取（chapter 由调用方
    从 SessionPlan 注入，compiler 不重复 load_session_plan）。
    """
    verdict = unknown(source="map_state._cartographic_review")
    if isinstance(review, dict):
        try:
            from app.lib.cartography.verdict_summary import should_inject_verdict

            if should_inject_verdict(review, current_fingerprint):
                cart = review.get("cartography")
                status = str(
                    cart.get("status") or "not_evaluated"
                ) if isinstance(cart, dict) else "not_evaluated"
                checks = review.get("checks")
                verdict = known(
                    {
                        "status": status,
                        "checked": len(checks) if isinstance(checks, list) else 0,
                    },
                    source="map_state._cartographic_review",
                    revision=mutation_revision,
                )
            else:
                verdict = stale({"status": "stale_fingerprint"},
                                source="map_state._cartographic_review")
        except Exception:  # noqa: BLE001 — verdict 缺席即 unknown
            verdict = unknown(source="map_state._cartographic_review")

    product_status = unknown(source="session_plan.gis_chapter.map_product")
    render_status = unknown(source="session_plan.gis_chapter.map_product.render_status")
    recipe_id = unknown(source="session_plan.gis_chapter.recipe_id")
    product = chapter.get("map_product") if isinstance(chapter, dict) else None
    if isinstance(product, dict):
        if product.get("status"):
            product_status = known(
                str(product["status"]),
                source="session_plan.gis_chapter.map_product")
        if product.get("render_status"):
            render_status = known(
                str(product["render_status"]),
                source="session_plan.gis_chapter.map_product.render_status")
    if isinstance(chapter, dict) and chapter.get("recipe_id"):
        recipe_id = known(
            str(chapter["recipe_id"]),
            source="session_plan.gis_chapter.recipe_id")
    return CartographicContext(
        verdict=verdict, product_status=product_status,
        render_status=render_status, recipe_id=recipe_id,
    )


def compile_interaction(
    map_state: Dict[str, Any],
    event_log: List[Dict[str, Any]],
) -> InteractionContext:
    pre_turn = map_state.get("_cartographic_context_observation")
    pre_turn = pre_turn if isinstance(pre_turn, dict) else {}

    selected = pre_turn.get("selected_feature")
    selected_fact = (
        known(selected, source="_cartographic_context_observation.selected_feature")
        if selected else unknown(source="_cartographic_context_observation.selected_feature")
    )
    focus = pre_turn.get("focus_layer_id")
    focus_fact = (
        known(str(focus), source="_cartographic_context_observation.focus_layer_id")
        if focus else unknown(source="_cartographic_context_observation.focus_layer_id")
    )
    is_3d = pre_turn.get("is_3d")
    if is_3d is None:
        is_3d = map_state.get("is_3d")
    display = (
        known(bool(is_3d), source="_cartographic_context_observation.is_3d")
        if is_3d is not None else unknown(source="map_state.is_3d")
    )

    # durable user 隐藏决策：与 gis_world_state.state 同裁决（全环派生）。
    provenance = map_state.get(_PROVENANCE_KEY)
    provenance = list(provenance) if isinstance(provenance, list) else []
    user_hidden = [
        str(entry.get("target")) for entry in provenance
        if entry.get("origin") == "user"
        and entry.get("kind") == _USER_HIDDEN_KIND
        and entry.get("detail", {}).get("visible") is False
        and entry.get("target")
    ]
    user_hidden_fact = (
        known(sorted(set(user_hidden)), source="map_state._gis_provenance")
        if user_hidden else unknown(source="map_state._gis_provenance")
    )

    pending = []
    for evt in reversed(event_log or []):
        if not isinstance(evt, dict) or evt.get("event") != "tool_executed":
            continue
        data = evt.get("data") or {}
        if data.get("status") in _PENDING_STATUSES:
            pending.append({
                "tool": data.get("tool"),
                "status": data.get("status"),
                "command": str(data.get("command") or "")[:96],
            })
        if len(pending) >= 3:
            break
    pending_fact = (
        known(list(reversed(pending)), source="event_log.tool_executed")
        if pending else unknown(source="event_log.tool_executed")
    )

    ring = map_state.get(_INTERACTIONS_KEY)
    ring = list(ring) if isinstance(ring, list) else []
    recent = ring[-MAX_INTERACTIONS:]
    interactions_fact = (
        known(recent, source="map_state._situation_interactions")
        if recent else unknown(source="map_state._situation_interactions")
    )
    return InteractionContext(
        selected_feature=selected_fact, focus_layer_id=focus_fact,
        user_hidden_layers=user_hidden_fact, pending_mutations=pending_fact,
        recent_interactions=interactions_fact, display_mode=display,
    )


def compile_analysis(
    chapter: Dict[str, Any], plan: Any,
) -> AnalysisContext:
    progress = unknown(source="plan_graph")
    stale_nodes: SitFact = unknown(source="workflow_runtime.stale")
    artifacts = unknown(source="session_plan.progress.bound_ref")
    if isinstance(chapter, dict) and chapter:
        graph = _safe(_build_plan_graph, chapter)
        if graph is not None:
            counts: Dict[str, int] = {}
            for node in graph.nodes:
                key = node.status.value
                counts[key] = counts.get(key, 0) + 1
            progress = known(
                {
                    "total": len(graph.nodes),
                    "ready": counts.get("ready", 0),
                    "running": counts.get("running", 0),
                    "complete": counts.get("complete", 0),
                    "failed": counts.get("failed", 0),
                    "unavailable": counts.get("unavailable", 0),
                },
                source="plan_graph",
            )
        stale = _safe(_stale_runtime_nodes, chapter.get(_workflow_runtime_key()))
        if stale:
            stale_nodes = known(
                sorted(str(s) for s in stale)[:8],
                source="workflow_runtime.stale",
            )
        rows = getattr(plan, "progress", None) or ()
        refs = sorted({
            str(row.bound_ref) for row in rows
            if getattr(row, "status", "") == "complete" and getattr(row, "bound_ref", "")
        })[:MAX_PROGRESS_ROWS]
        if refs:
            artifacts = known(refs, source="session_plan.progress.bound_ref")
    return AnalysisContext(
        plan_progress=progress, stale_nodes=stale_nodes, artifacts=artifacts,
    )


def compile_delivery(chapter: Dict[str, Any], is_3d: SitFact) -> DeliveryContext:
    product = chapter.get("map_product") if isinstance(chapter, dict) else None
    target = unknown(source="session_plan.gis_chapter.map_product")
    export_format = unknown(source="session_plan.gis_chapter.map_product")
    if isinstance(product, dict):
        if product.get("delivery_target"):
            target = known(
                str(product["delivery_target"]),
                source="session_plan.gis_chapter.map_product")
        if product.get("export_format"):
            export_format = known(
                str(product["export_format"]),
                source="session_plan.gis_chapter.map_product")
    return DeliveryContext(
        target=target, display_mode=is_3d, export_format=export_format,
    )


def compile_constraints(chapter: Dict[str, Any]) -> ConstraintsContext:
    raw = chapter.get("constraints") if isinstance(chapter, dict) else None
    explicit = (
        known(raw, source="session_plan.gis_chapter.constraints")
        if raw else unknown(source="session_plan.gis_chapter.constraints")
    )
    return ConstraintsContext(
        explicit=explicit,
        budget=unknown(source="constraints.budget"),
        security=unknown(source="constraints.security"),
    )


async def _bounded_descriptors(
    session_id: str, store: Any, refs: Any, preferred: List[str],
    evidence: SituationEvidence,
) -> Dict[str, Dict[str, Any]]:
    """定向描述符读取（≤MAX_DATASET_FACTS，mapspec 引用优先，其余字典序）。

    绝不读 payload；描述符是 O(1) 预计算缓存（V3 Performance）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(refs, dict) or not refs:
        return out
    ordered = [r for r in preferred if r in refs]
    ordered += sorted(r for r in refs if r not in set(preferred))
    ordered = ordered[:MAX_DATASET_FACTS]
    omitted = len(refs) - len(ordered)
    if omitted > 0:
        evidence.omitted.append(f"data.descriptors(+{omitted})")

    async def _one(ref_id: str):
        try:
            return ref_id, await store.get_ref_descriptor(session_id, ref_id)
        except Exception:  # noqa: BLE001 — 单描述符失败不失败编译
            return ref_id, None

    for ref_id, desc in await asyncio.gather(*(_one(r) for r in ordered)):
        if isinstance(desc, dict):
            out[ref_id] = desc
    return out


def _interaction_sequence(map_state: Dict[str, Any]) -> int:
    ring = map_state.get(_INTERACTIONS_KEY)
    if isinstance(ring, list) and ring and isinstance(ring[-1], dict):
        return _num(ring[-1].get("sequence")) or 0
    return 0


def _observation_sequence(map_state: Dict[str, Any]) -> int:
    obs = map_state.get("_cartographic_observation")
    if isinstance(obs, dict):
        return _num(obs.get("sequence")) or 0
    return 0


#: 事实 source 前缀 → 权威源名（partial source unavailable 时，
#: unknown 且后端失败的派生事实统一升级为 unavailable —— 降级语义可归因）。
_BACKING_PREFIXES: tuple = (
    ("map_state", "map_state"),
    ("_cartographic_context_observation", "map_state"),
    ("_cartographic_observation", "map_state"),
    ("event_log", "event_log"),
    ("mapspec", "mapspec"),
    ("session_plan", "session_plan"),
    ("session_store", "map_state"),
    ("ref_descriptor", "refs"),
    ("cartographic_fingerprint", "mapspec"),
)


def _backing_source(source: str) -> Optional[str]:
    for prefix, backing in _BACKING_PREFIXES:
        if source.startswith(prefix):
            return backing
    return None


def _mark_unavailable_backing(situation: GISSituation, failed: frozenset) -> GISSituation:
    """源级失败传导：unknown 事实的权威源本次读取失败 → status=unavailable。

    已 known 的事实不动（能 known 说明源在过）；派生事实（derived.*/plan_graph
    等，无直接权威源）保持 unknown。
    """
    if not failed:
        return situation
    context_names = (
        "user_goal", "geographic", "temporal", "data", "map",
        "analysis", "cartographic", "interaction", "delivery", "constraints",
    )
    updates: Dict[str, Any] = {}
    for ctx_name in context_names:
        ctx = getattr(situation, ctx_name)
        ctx_updates: Dict[str, Any] = {}
        for fact_name in ctx.model_dump():
            fact = getattr(ctx, fact_name)
            if fact.status != "unknown":
                continue
            backing = _backing_source(fact.source)
            if backing is not None and backing in failed:
                ctx_updates[fact_name] = unavailable(source=fact.source)
        if ctx_updates:
            updates[ctx_name] = ctx.model_copy(update=ctx_updates)
    if not updates:
        return situation
    return situation.model_copy(update=updates)


async def compile_situation(
    session_id: str,
    *,
    turn_id: str = "",
    compiled_at: str = "",
    store: Any = None,
    mapspec_store: Any = None,
) -> GISSituation:
    """编译 session 的 GISSituation（只读；部分源失败降级不失败）。"""
    if store is None:
        from app.services.session_data import session_data_manager as store
    if mapspec_store is None:
        from app.services.mapspec.store import mapspec_store_instance as mapspec_store

    values, sources_ok, sources_unavailable = await _gather_sources(
        session_id, store, mapspec_store,
    )
    state, mapspec, plan, refs, event_log = values
    map_state = state if isinstance(state, dict) else {}
    spec = mapspec if isinstance(mapspec, dict) else {}
    events = event_log if isinstance(event_log, list) else []
    refs_map = refs if isinstance(refs, dict) else {}

    evidence = SituationEvidence(
        sources_ok=sources_ok,
        sources_unavailable=list(sources_unavailable),
    )

    revision = SituationRevision(
        mutation_revision=_num(map_state.get("_cartographic_mutation_revision")) or 0,
        observation_sequence=_observation_sequence(map_state),
        interaction_sequence=_interaction_sequence(map_state),
    )

    pre_turn = map_state.get("_cartographic_context_observation")
    pre_turn = pre_turn if isinstance(pre_turn, dict) else None
    observation = map_state.get("_cartographic_observation")
    observation = observation if isinstance(observation, dict) else None

    geographic = compile_geographic(map_state, spec, pre_turn, evidence)
    user_goal = compile_user_goal(plan, revision.mutation_revision)
    temporal = compile_temporal(map_state, plan, evidence)
    map_ctx = compile_map(map_state, spec, observation, evidence)

    chapter = getattr(plan, "gis_chapter", None) if plan is not None else None
    chapter = chapter if isinstance(chapter, dict) else {}
    interaction = compile_interaction(map_state, events)
    analysis = compile_analysis(chapter, plan)
    cartographic = compile_cartographic(
        chapter,
        map_state.get("_cartographic_review"),
        map_ctx.fingerprint.value if map_ctx.fingerprint.status == STATUS_KNOWN else None,
        revision.mutation_revision,
    )

    datasets_fact, roles_fact, freshness_fact, crs_fact = await _compile_data(
        session_id, store, refs_map, spec, revision.mutation_revision, evidence,
    )
    if crs_fact is not None:
        geographic = geographic.model_copy(update={"crs": crs_fact})
    data_ctx = DataContext(
        datasets=datasets_fact, active_roles=roles_fact,
        quality=unknown(source="data_fabric.facts"),
        freshness=freshness_fact,
    )

    delivery = compile_delivery(chapter, interaction.display_mode)
    constraints = compile_constraints(chapter)

    provenance = map_state.get(_PROVENANCE_KEY)
    provenance = list(provenance) if isinstance(provenance, list) else []
    evidence.provenance_tail = provenance[-MAX_PROVENANCE_EVIDENCE:]

    situation = GISSituation(
        identity=SituationIdentity(
            session_id=session_id, turn_id=turn_id, revision=revision,
            compiled_at=compiled_at,
        ),
        user_goal=user_goal, geographic=geographic, temporal=temporal,
        data=data_ctx, map=map_ctx, analysis=analysis,
        cartographic=cartographic, interaction=interaction,
        delivery=delivery, constraints=constraints, evidence=evidence,
    )
    return _mark_unavailable_backing(situation, frozenset(sources_unavailable))


async def _compile_data(
    session_id: str,
    store: Any,
    refs: Any,
    spec: Dict[str, Any],
    revision: Optional[int],
    evidence: SituationEvidence,
):
    """DataContext 四事实 + Geographic.crs 回填（descriptor-first）。"""
    if not isinstance(refs, dict):
        refs = {}
    # mapspec 引用的 ref 优先获得描述符位（active 数据 > 存量清单）。
    preferred: List[str] = []
    for summary in (spec.get("sources") or {}).values() if isinstance(
            spec.get("sources"), dict) else []:
        if isinstance(summary, dict):
            ref = summary.get("ref_id") or summary.get("ref")
            if isinstance(ref, str) and ref.startswith("ref:"):
                preferred.append(ref)
    descriptors = await _bounded_descriptors(
        session_id, store, refs, preferred, evidence,
    )
    crs_fact = None
    if descriptors:
        dataset_rows = []
        crs_values = []
        for ref_id in sorted(descriptors):
            desc = descriptors[ref_id]
            alias = refs.get(ref_id, "")
            row: Dict[str, Any] = {"ref_id": ref_id}
            if alias:
                row["alias"] = alias
            for key in ("feature_count", "geometry_types", "crs",
                        "raster_capable", "content_revision"):
                if desc.get(key) is not None:
                    row[key] = desc.get(key)
            if isinstance(desc.get("bbox"), list):
                row["bbox"] = desc["bbox"]
            dataset_rows.append(row)
            if desc.get("crs"):
                crs_values.append(str(desc["crs"]))
        datasets_fact = known(
            dataset_rows, source="ref_descriptor", revision=revision,
        )
        if crs_values:
            crs_fact = known(
                crs_values[0] if len(set(crs_values)) == 1 else sorted(set(crs_values)),
                source="ref_descriptor.crs",
            )
        max_rev = max(
            (d.get("content_revision") for d in descriptors.values()),
            default=None,
        )
        freshness_fact = (
            known(max_rev, source="ref_descriptor.content_revision")
            if isinstance(max_rev, int) else unknown(source="ref_descriptor.content_revision")
        )
    else:
        datasets_fact = unknown(source="ref_descriptor")
        freshness_fact = unknown(source="ref_descriptor.content_revision")

    # role → ref（layer.context_role × layer.source × sources.ref）。
    roles: Dict[str, str] = {}
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
    for layer in spec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        role = layer.get("context_role") or layer.get("role")
        src = sources.get(layer.get("source")) if layer.get("source") else None
        ref = (src or {}).get("ref_id") or (src or {}).get("ref") if isinstance(src, dict) else None
        if role and isinstance(ref, str) and ref and role not in roles:
            roles[str(role)] = ref
    roles_fact = (
        known(roles, source="mapspec.layers.context_role", revision=revision)
        if roles else unknown(source="mapspec.layers.context_role")
    )
    return datasets_fact, roles_fact, freshness_fact, crs_fact


# ── 延迟导入的既有投影口（单一事实源复用，非第二实现） ────────────────────

def _cartographic_fingerprint(mapspec: Dict[str, Any]) -> str:
    from app.lib.cartography.quality_loop import cartographic_fingerprint

    return cartographic_fingerprint(mapspec)


def _observation_summary(observation: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.gis_harness.observation_states import build_observation_summary

    return build_observation_summary(observation)


def _build_plan_graph(chapter: Dict[str, Any]):
    from app.services.gis_harness.plan_graph import build_plan_graph

    return build_plan_graph(chapter)


def _workflow_runtime_key() -> str:
    """runtime_bridge.WORKFLOW_RUNTIME_KEY（单一常量源，不硬编码）。"""
    from app.services.gis_harness.runtime_bridge import WORKFLOW_RUNTIME_KEY

    return WORKFLOW_RUNTIME_KEY


def _stale_runtime_nodes(runtime_block: Any):
    from app.services.gis_harness.completion.unified_findings import (
        stale_runtime_nodes,
    )

    return stale_runtime_nodes(runtime_block)


from app.services.gis_situation.facts import STATUS_KNOWN  # noqa: E402
from app.services.gis_world_state.provenance import _PROVENANCE_KEY  # noqa: E402

_INTERACTIONS_KEY = "_situation_interactions"
