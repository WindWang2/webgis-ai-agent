"""SessionPlan — Pi-path host-plan envelope (ADR-0076).

Keyed by ``session_id`` in SessionStore (alias ``session-plan``), never by a
Pi tree entry. GIS chapter is an embedded MapProductPlan dump; progress is
capability completion, not a tool-call sequence. ChatEngine does not read or
write this object.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from app.services.distributed_lock import session_lock_registry
from app.services.session_data import session_data_manager
from app.utils.sse import sse_event

logger = logging.getLogger(__name__)

CURRENT_ALIAS = "session-plan"
STORE_PREFIX = "sessionplan"
REF_PREFIX = f"ref:{STORE_PREFIX}-"
HISTORY_ALIAS_PREFIX = "session-plan-id:"


def is_session_plan_listing(ref_id: str, alias: str = "") -> bool:
    """True for SessionPlan store rows — not GIS data the model should reuse."""
    rid = str(ref_id or "")
    al = str(alias or "")
    return (
        rid.startswith(REF_PREFIX)
        or al == CURRENT_ALIAS
        or al.startswith(HISTORY_ALIAS_PREFIX)
    )


def public_data_refs(refs: dict) -> dict:
    """Drop SessionPlan envelope rows from an LLM-facing ref inventory."""
    return {
        rid: alias
        for rid, alias in (refs or {}).items()
        if not is_session_plan_listing(str(rid), str(alias or ""))
    }


SESSION_PLAN_UPDATED = "session_plan_updated"
SESSION_PLAN_PROGRESS = "session_plan_progress"
SESSION_PLAN_SUPERSEDED = "session_plan_superseded"
CANONICAL_PLAN_EVENT_NAMES = frozenset(
    {"plan_ready", "plan_step_done", "plan_finalized"}
)

ProgressStatus = Literal["pending", "complete", "voided", "unavailable", "failed"]


class CapabilityProgress(BaseModel):
    """One capability / data-requirement row in the progress chapter."""

    capability: str
    status: ProgressStatus = "pending"
    bound_ref: str = ""


class SessionPlan(BaseModel):
    """Current host-plan envelope for one Session."""

    envelope_id: str
    session_id: str
    user_goal: str = ""
    gis_chapter: Optional[dict[str, Any]] = None
    progress: list[CapabilityProgress] = Field(default_factory=list)
    replaced: bool = False
    superseded: bool = False
    previous_goal: str = ""
    updated_at: float = 0.0


class SessionPlanEvent(BaseModel):
    """One SessionPlan SSE payload (new event names only)."""

    event: str
    data: dict[str, Any]


def _new_envelope_id() -> str:
    return f"sp-{uuid.uuid4().hex[:12]}"


def _history_alias(envelope_id: str) -> str:
    return f"{HISTORY_ALIAS_PREFIX}{envelope_id}"


def goal_key(gis_chapter: Optional[dict[str, Any]], query: str = "") -> str:
    """Stable same-goal vs new-goal key from a MapProductPlan dump."""
    if not gis_chapter:
        return (query or "").strip()
    intent = gis_chapter.get("intent") or {}
    if not isinstance(intent, dict):
        intent = {}
    scope = intent.get("scope") if isinstance(intent.get("scope"), dict) else {}
    subject = intent.get("subject") if isinstance(intent.get("subject"), dict) else {}
    scope_name = str(scope.get("name") or "").strip().rstrip("市")
    subject_name = str(subject.get("category") or "").strip()
    task = str(intent.get("task") or "").strip()
    if scope_name or subject_name or task:
        return f"{scope_name}|{subject_name}|{task}"
    return str(gis_chapter.get("query") or query or "").strip()


def _init_progress(gis_chapter: dict[str, Any]) -> list[CapabilityProgress]:
    seen: list[str] = []
    for row in list(gis_chapter.get("data_requirements") or []) + list(
        gis_chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        cap = str(row.get("capability") or "").strip()
        if cap and cap not in seen:
            seen.append(cap)
    return [CapabilityProgress(capability=cap, status="pending") for cap in seen]


def _merge_progress(
    rows: list[CapabilityProgress],
    gis_chapter: dict[str, Any],
) -> list[CapabilityProgress]:
    """Same-goal replace: voided rows survive only while the replacement
    chapter still tracks that capability; new capabilities join as pending.

    Keeps the stored progress consistent with the void SSE — the store is the
    plan truth (ADR-0076), so rows must not silently flip back to pending.
    """
    fresh = _init_progress(gis_chapter)
    chapter_caps = {row.capability for row in fresh}
    merged = [row for row in rows if row.capability in chapter_caps]
    known = {row.capability for row in merged}
    merged.extend(row for row in fresh if row.capability not in known)
    return merged


def open_capabilities(plan: Optional[SessionPlan]) -> list[str]:
    """Voided rows are open too — their completion is gone, the chapter
    requirement is pending again. Failed rows are open in the retry sense:
    execution was attempted, no artifact was produced (v3 Phase E)."""
    if plan is None:
        return []
    return [
        row.capability
        for row in plan.progress
        if row.status in ("pending", "voided", "failed")
    ]


def session_plan_stale(plan: Optional[SessionPlan]) -> bool:
    """#1084（v2 Phase 4）：持久计划的 registry 指纹与当前 manifest 不一致。

    部署升级改变 registry 语义（工具改绑/候选重排/模板变更）后，旧计划按
    新 registry 静默重放会错归 capability 或引用消失的工具。判 stale 的
    计划在投影中标注 STALE_PLAN 并建议 replan；不自动作废（agent 可判断
    剩余步骤是否受影响）。历史计划（无指纹）不判 stale。
    """
    if plan is None or not plan.gis_chapter:
        return False
    stored_fp = plan.gis_chapter.get("manifest_fingerprint")
    if not stored_fp:
        return False
    try:
        from app.lib.gis.runtime_manifest import get_runtime_manifest
        return get_runtime_manifest().is_stale_plan(str(stored_fp))
    except Exception:  # noqa: BLE001 — 指纹比对失败不阻断投影
        return False


def format_session_plan_projection(
    plan: Optional[SessionPlan],
    mapspec: Optional[Dict[str, Any]] = None,
) -> str:
    """Bounded next-turn note. Not a Cartography Verdict block.

    v3(Phase F)：首行契约不变（既有调用方/测试锁定）；其后追加有界
    [GIS Plan] DAG 投影 —— Ready/Waiting/Completed/Unavailable/Recommended
    next，由 gis_chapter 扁平行**派生**（plan_graph 纯投影，非第二事实源：
    节点状态仍由 _mark_progress 的行状态推进，这里只读评估）。无数据需求
    的章节（空 envelope / 组件-only）不输出图块。
    """
    if plan is None or plan.gis_chapter is None:
        return (
            "[SessionPlan] recipe=none open= (call webgis_map_intent) "
            "replaced=false superseded=false"
        )
    recipe = str(plan.gis_chapter.get("recipe_id") or "none")
    open_caps = ",".join(open_capabilities(plan)) or "none"
    stale_note = ""
    if session_plan_stale(plan):
        stale_note = (
            " STALE_PLAN=true"
            "（计划编制于不同 registry 世代，工具/能力绑定可能已变；"
            "续跑前优先 webgis_map_intent 重规划或逐能力核验 resolved_tool）"
        )
    head = (
        f"[SessionPlan] recipe={recipe} open={open_caps} "
        f"replaced={'true' if plan.replaced else 'false'} "
        f"superseded={'true' if plan.superseded else 'false'}"
        f"{stale_note}"
    )
    # ADR-0081：地图成品完成度行（bounded、单行、派生自章节 map_product
    # 块 —— finalizer 写入，这里只读投影；DAG 完成 ≠ 地图成品完成）。
    # 先算再走任何提前返回 —— 组件-only / analysis-only 章节（无
    # data_requirements）与图构建失败路径都保留该行（review P2）。
    product = plan.gis_chapter.get("map_product")
    product_line = ""
    if isinstance(product, dict):
        line = str(product.get("projection") or "")
        if line:
            product_line = "\n" + line
    if not plan.gis_chapter.get("data_requirements"):
        return head + product_line
    try:
        from app.services.gis_harness.plan_graph import (
            build_plan_graph,
            project_graph_block,
        )
        graph = build_plan_graph(plan.gis_chapter)
        block = project_graph_block(graph)
    except Exception:  # noqa: BLE001 — 图投影是增值信号，绝不阻断 turn 上下文
        return head + product_line
    if not block:
        return head + product_line
    # ADR-0085：目标→产品 facets 投影行（纯派生、单行有界；章节/MapSpec
    # 之外零新状态 —— 让 Pi 看见"产品 = facets 集合"而非单个 heatmap）。
    products_line = ""
    try:
        from app.services.gis_harness.product_graph import build_product_graph

        products_line = "\n" + build_product_graph(
            plan.gis_chapter, mapspec
        ).summary_line()
    except Exception:  # noqa: BLE001 — 投影失败只少一行
        products_line = ""
    # P9 §17/§18 + ADR-0088 P1：执行债/产品债 → 统一确定性下一动作建议行
    # （零 LLM/零 IO 的纯投影；不构成 agent loop —— 执行仍归 Pi + harness）。
    # GISActionIntent 并轨 ProductActionAdvisor 与 PlanGraph.recommended_next
    # （ADR-0087 Future work 落地）：执行债优先于产品债，observation/血缘
    # 输入缺席时诚实降级（只少一行，绝不阻断 turn 上下文）。
    next_action_line = ""
    try:
        from app.services.gis_harness.action_intent import action_intent_projection
        from app.services.gis_harness.product_graph import build_facet_completion
        from app.services.gis_harness.product_lineage import build_facet_lineage

        facets = build_facet_completion(plan.gis_chapter, mapspec)
        # P4 最小重计算：血缘投影（纯函数零 IO，无 descriptor 时 liveness
        # 诚实降级为 unknown —— 可复用 ref 仍随动作披露，死 ref 不虚构）。
        lineage = build_facet_lineage(plan.gis_chapter, mapspec)
        next_action_line = "\n" + action_intent_projection(
            plan.gis_chapter,
            facets,
            graph=graph,
            lineage=lineage,
        )
    except Exception:  # noqa: BLE001 — 投影失败只少一行
        next_action_line = ""
    if not products_line.strip():
        return head + "\n" + block + product_line
    return head + "\n" + block + products_line + next_action_line + product_line


def events_to_sse(events: list[SessionPlanEvent], session_id: str = "") -> str:
    """Serialize SessionPlan events. Never uses CanonicalPlan event names."""
    chunks: list[str] = []
    for item in events:
        if item.event in CANONICAL_PLAN_EVENT_NAMES:
            raise ValueError(f"CanonicalPlan event name forbidden on Pi path: {item.event}")
        payload = dict(item.data)
        if session_id and "session_id" not in payload:
            payload["session_id"] = session_id
        chunks.append(sse_event(item.event, payload))
    return "".join(chunks)


async def load_session_plan(
    session_id: str,
    *,
    store: Any = None,
) -> Optional[SessionPlan]:
    backend = store if store is not None else session_data_manager
    try:
        ref_id = await backend.resolve_alias(session_id, CURRENT_ALIAS)
        if ref_id == CURRENT_ALIAS:
            return None
        data = await backend.get(session_id, ref_id)
    except Exception:
        logger.exception("[SessionPlan] load failed session=%s", session_id)
        return None
    if not isinstance(data, dict):
        return None
    try:
        return SessionPlan.model_validate(data)
    except Exception:
        logger.warning("[SessionPlan] invalid envelope session=%s", session_id)
        return None


async def save_session_plan(
    plan: SessionPlan,
    *,
    store: Any = None,
) -> None:
    backend = store if store is not None else session_data_manager
    plan.updated_at = time.time()
    payload = plan.model_dump()
    ref_id = await backend.resolve_alias(plan.session_id, CURRENT_ALIAS)
    if ref_id != CURRENT_ALIAS:
        if await backend.overwrite(plan.session_id, ref_id, payload):
            return
    new_ref = await backend.store(plan.session_id, payload, prefix=STORE_PREFIX)
    await backend.set_alias(plan.session_id, new_ref, CURRENT_ALIAS)


async def ensure_session_plan_slot(
    session_id: str,
    *,
    store: Any = None,
) -> SessionPlan:
    """Host opens an empty envelope before tools run. No SSE (GIS chapter empty).

    Read-mostly and called on every Pi tool callback, so the fast path is
    lockless: only a miss (envelope absent) takes the session lock and
    re-checks inside — double-checked locking, keeping per-callback slot
    checks free of lock traffic while first-creation stays serialized."""
    current = await load_session_plan(session_id, store=store)
    if current is not None:
        return current
    # v2(audit F2): 计划 envelope 是共享 Redis 状态（alias/refs/history），
    # 降级锁（两 pod 各持进程内锁）下创建会交叉覆盖 —— fail-closed。
    async with session_lock_registry.lock(session_id, fail_on_degraded=True):
        return await _ensure_slot_unlocked(session_id, store=store)


async def _ensure_slot_unlocked(session_id: str, *, store: Any) -> SessionPlan:
    current = await load_session_plan(session_id, store=store)
    if current is not None:
        return current
    plan = SessionPlan(
        envelope_id=_new_envelope_id(),
        session_id=session_id,
        updated_at=time.time(),
    )
    await save_session_plan(plan, store=store)
    return plan


async def _archive_envelope(plan: SessionPlan, *, store: Any) -> None:
    backend = store
    payload = plan.model_dump()
    alias = _history_alias(plan.envelope_id)
    ref_id = await backend.resolve_alias(plan.session_id, alias)
    if ref_id != alias:
        if await backend.overwrite(plan.session_id, ref_id, payload):
            return
    new_ref = await backend.store(plan.session_id, payload, prefix=STORE_PREFIX)
    await backend.set_alias(plan.session_id, new_ref, alias)


def _updated_event(plan: SessionPlan) -> SessionPlanEvent:
    gis = plan.gis_chapter or {}
    return SessionPlanEvent(
        event=SESSION_PLAN_UPDATED,
        data={
            "session_id": plan.session_id,
            "envelope_id": plan.envelope_id,
            "plan_id": gis.get("plan_id") or "",
            "recipe_id": gis.get("recipe_id") or "",
            "query": gis.get("query") or plan.user_goal,
            "replaced": plan.replaced,
        },
    )


def _progress_event(plan: SessionPlan, row: CapabilityProgress) -> SessionPlanEvent:
    return SessionPlanEvent(
        event=SESSION_PLAN_PROGRESS,
        data={
            "session_id": plan.session_id,
            "envelope_id": plan.envelope_id,
            "capability": row.capability,
            "status": row.status,
            "bound_ref": row.bound_ref,
        },
    )


def _superseded_event(old: SessionPlan, new: SessionPlan) -> SessionPlanEvent:
    return SessionPlanEvent(
        event=SESSION_PLAN_SUPERSEDED,
        data={
            "session_id": new.session_id,
            "old_envelope_id": old.envelope_id,
            "envelope_id": new.envelope_id,
            "previous_query": old.user_goal,
            "query": new.user_goal,
        },
    )


def _tool_to_capability() -> dict[str, str]:
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        return dict(get_algorithm_registry().tool_to_capability())
    except Exception:
        return {}


def capabilities_hit_by_tool(
    plan: SessionPlan,
    tool_name: str,
) -> list[str]:
    hits: list[str] = []
    mapped = _tool_to_capability().get(tool_name)
    if mapped:
        hits.append(mapped)
    gis = plan.gis_chapter or {}
    for row in list(gis.get("data_requirements") or []) + list(
        gis.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        if row.get("resolved_tool") == tool_name:
            cap = str(row.get("capability") or "").strip()
            if cap:
                hits.append(cap)
    seen: list[str] = []
    for cap in hits:
        if cap not in seen:
            seen.append(cap)
    return seen


def _mark_progress(
    plan: SessionPlan,
    capabilities: list[str],
    *,
    status: ProgressStatus,
    bound_ref: str = "",
) -> list[CapabilityProgress]:
    changed: list[CapabilityProgress] = []
    pending = {row.capability for row in plan.progress}
    for cap in capabilities:
        if cap not in pending:
            plan.progress.append(CapabilityProgress(capability=cap, status="pending"))
            pending.add(cap)
    for row in plan.progress:
        if row.capability in capabilities and row.status != status:
            row.status = status
            if bound_ref:
                row.bound_ref = bound_ref
            changed.append(row)
    gis = plan.gis_chapter or {}
    req_status = "available" if status == "complete" else (
        "pending" if status == "voided" else status
    )
    for row in gis.get("data_requirements") or []:
        if isinstance(row, dict) and row.get("capability") in capabilities:
            row["status"] = req_status
            if bound_ref:
                row["bound_ref"] = bound_ref
    step_status = "done" if status == "complete" else (
        "pending" if status == "voided" else status
    )
    for row in gis.get("analysis_steps") or []:
        if isinstance(row, dict) and row.get("capability") in capabilities:
            row["status"] = step_status
            if bound_ref:
                row["bound_ref"] = bound_ref
    return changed


async def apply_tool_result(
    session_id: str,
    tool_name: str,
    raw_result: Any,
    *,
    success: bool,
    geojson_ref: Optional[str] = None,
    store: Any = None,
) -> list[SessionPlanEvent]:
    """Mutate the SessionPlan after a unified dispatch.

    Intent replaces / supersedes the GIS chapter. Product updates the same
    envelope. Other tools complete matching capabilities. A failed dispatch
    (v3 Phase E) marks the capabilities it would have served as ``failed`` —
    retryable, disclosed in the projection, and blocking its DAG downstream
    until retried or voided. The whole load→mutate→save runs under the
    per-session lock: a Pi turn may issue parallel tool callbacks and the
    supersede branch must not be lost to a last-write-wins interleave
    (ADR-0051 lock pattern).
    """
    if not session_id:
        return []
    # v2(audit F2): envelope 变更是共享 Redis 写（supersede 归档 + 进度
    # append），降级锁下两 pod last-write-wins —— fail-closed（lost 检查
    # 已由 _apply_tool_result_unlocked 的 lock.lost 守卫覆盖）。
    async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
        return await _apply_tool_result_unlocked(
            session_id,
            tool_name,
            raw_result,
            success=success,
            geojson_ref=geojson_ref,
            store=store,
            lock=lock,
        )


async def _apply_tool_result_unlocked(
    session_id: str,
    tool_name: str,
    raw_result: Any,
    *,
    success: bool = True,
    geojson_ref: Optional[str] = None,
    store: Any = None,
    lock: Any = None,
) -> list[SessionPlanEvent]:
    backend = store if store is not None else session_data_manager
    plan = await _ensure_slot_unlocked(session_id, store=backend)
    raw = raw_result if isinstance(raw_result, dict) else {}
    events: list[SessionPlanEvent] = []

    if lock is not None and lock.lost:
        logger.error(
            "[SessionPlan] Lock ownership for session %s was lost; aborting envelope mutation",
            session_id,
        )
        return []

    if not success:
        # v3(Phase E)：失败对计划可见 —— 命中的能力行标 failed（可重试，
        # 下次成功调用覆写为 complete；DAG 下游在重试前被阻塞）。规划入口
        # 工具（webgis_*）失败不映射：没有确定受害的能力行，章节保持原状
        # （意图/产品调用本身的失败由调用方 retry 语义处理）。
        if tool_name.startswith("webgis_") or plan.gis_chapter is None:
            return []
        hits = capabilities_hit_by_tool(plan, tool_name)
        if not hits:
            return []
        changed = _mark_progress(plan, hits, status="failed")
        if not changed:
            return []
        if lock is not None and lock.lost:
            return []
        await save_session_plan(plan, store=backend)
        return [_progress_event(plan, row) for row in changed]

    if tool_name == "webgis_map_intent":
        gis = raw.get("plan")
        if not isinstance(gis, dict):
            return []
        query = str(gis.get("query") or (raw.get("intent") or {}).get("query") or "")
        new_key = goal_key(gis, query)
        old_key = goal_key(plan.gis_chapter, plan.user_goal)
        if plan.gis_chapter and old_key and new_key and old_key != new_key:
            if lock is not None and lock.lost:
                return []
            old = plan.model_copy(deep=True)
            old.superseded = True
            await _archive_envelope(old, store=backend)
            new = SessionPlan(
                envelope_id=_new_envelope_id(),
                session_id=session_id,
                user_goal=query,
                gis_chapter=gis,
                progress=_init_progress(gis),
                previous_goal=old.user_goal,
            )
            if lock is not None and lock.lost:
                return []
            await save_session_plan(new, store=backend)
            events.append(_superseded_event(old, new))
            events.append(_updated_event(new))
            return events

        replaced = plan.gis_chapter is not None
        if replaced:
            for row in plan.progress:
                if row.status != "voided":
                    row.status = "voided"
                    events.append(_progress_event(plan, row))
        plan.gis_chapter = gis
        plan.user_goal = query or plan.user_goal
        plan.progress = _merge_progress(plan.progress, gis)
        plan.replaced = replaced
        if lock is not None and lock.lost:
            return []
        await save_session_plan(plan, store=backend)
        events.append(_updated_event(plan))
        return events

    if tool_name == "webgis_map_product" and plan.gis_chapter is not None:
        if raw.get("completeness") is not None:
            plan.gis_chapter["completeness"] = raw["completeness"]
        if raw.get("status"):
            plan.gis_chapter["status"] = raw["status"]
        if raw.get("recipe_id"):
            plan.gis_chapter["recipe_id"] = raw["recipe_id"]
        evidence = raw.get("map_product_evidence") or {}
        resolution = evidence.get("capability_resolution") or []
        done_caps = [
            str(item.get("capability"))
            for item in resolution
            if isinstance(item, dict)
            and item.get("capability")
            and item.get("status") in ("available", "resolved", "done")
        ]
        # completeness.missing == [] says the *product outputs* are complete;
        # it is not evidence that never-run capabilities executed.
        changed = _mark_progress(
            plan, done_caps, status="complete", bound_ref=geojson_ref or ""
        )
        if lock is not None and lock.lost:
            return []
        await save_session_plan(plan, store=backend)
        events.append(_updated_event(plan))
        events.extend(_progress_event(plan, row) for row in changed)
        return events

    if plan.gis_chapter is None:
        return []
    hits = capabilities_hit_by_tool(plan, tool_name)
    if not hits:
        return []
    changed = _mark_progress(
        plan, hits, status="complete", bound_ref=geojson_ref or ""
    )
    if not changed:
        return []
    if lock is not None and lock.lost:
        return []
    await save_session_plan(plan, store=backend)
    # P1（ADR-0082）：成功绑定 ref 的能力行同步注册产物记录 —— capability/
    # tool/血缘上下文在此最完整（dispatch seam 只登记 ref 本身）。锁内
    # 直通（lock 透传跳过重取，避免非重入自锁）；失败降级为日志。
    if geojson_ref and str(geojson_ref).startswith("ref:"):
        await _register_plan_artifacts(
            session_id, plan, hits, tool_name, geojson_ref, lock=lock
        )
    return [_progress_event(plan, row) for row in changed]


async def _register_plan_artifacts(
    session_id: str,
    plan: SessionPlan,
    hits: list[str],
    tool_name: str,
    geojson_ref: str,
    *,
    lock: Any = None,
) -> None:
    """plan-apply seam 的产物注册（ADR-0082）：capability 上下文 + 实例级血缘。

    lineage inputs = 依赖能力行的当前 bound_ref（depends_on → 实例映射；
    plan_graph 的 depends_on 是类型级，这里落到具体产物）。
    """
    from app.services.artifact_registry import register_artifact

    chapter = plan.gis_chapter or {}
    bound: dict[str, str] = {}
    depends: dict[str, list[str]] = {}
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        cap = str(row.get("capability") or "")
        if not cap:
            continue
        ref = str(row.get("bound_ref") or "")
        if ref.startswith("ref:"):
            bound[cap] = ref
        deps = row.get("depends_on")
        if isinstance(deps, list):
            depends.setdefault(cap, []).extend(
                str(d) for d in deps if isinstance(d, str)
            )
    if not depends:
        # 旧行无 depends_on 字段 → 类型级推断兜底（与 plan_graph 同源）
        try:
            from app.services.gis_harness.plan_graph import infer_dependency_edges

            depends = infer_dependency_edges(list(bound.keys()) or list(hits))
        except Exception:  # noqa: BLE001
            depends = {}
    inputs: list[str] = []
    for cap in hits:
        for dep in depends.get(cap) or ():
            dep_ref = bound.get(dep)
            if dep_ref and dep_ref != geojson_ref and dep_ref not in inputs:
                inputs.append(dep_ref)

    outputs: list[str] = []
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        desc = get_capability_registry().get(hits[0])
        if desc is not None:
            outputs = [str(t) for t in (desc.output_artifact_types or [])]
    except Exception:  # noqa: BLE001
        outputs = []
    try:
        descriptor = await session_data_manager.get_ref_descriptor(
            session_id, geojson_ref
        )
    except Exception:  # noqa: BLE001
        descriptor = None
    # V2(P2) 输出契约验证（§10）：声明 output_artifact_types vs 实况画像。
    # 纯函数 + descriptor O(1) 画像，findings 只进 metadata["contract_check"]
    # 与结构化日志 —— 注册是增值记录，验证绝不阻断 plan 路径。
    contract_check = None
    if outputs:
        try:
            from app.lib.gis.contract_validation import (
                contract_check_metadata,
                log_contract_findings,
                validate_output_contract,
            )
            from app.lib.gis.dataset_profile import DatasetProfile

            profile = DatasetProfile.from_ref_descriptor(descriptor)
            findings = validate_output_contract(outputs, profile)
            contract_check = contract_check_metadata(outputs, profile)
            log_contract_findings(findings, session_id=session_id, ref=geojson_ref)
        except Exception:  # noqa: BLE001 — 验证失败降级为无 findings
            contract_check = None
    # 单次注册（review 终审 F1：N 个命中能力此前循环 N 次全量账本
    # read-modify-save，且标量 producer_capability 只留最后一个）——
    # 主能力作 producer，全部命中能力入 metadata（血统能力清单保留）。
    plan_meta: dict = {"seam": "plan_apply", "capabilities": list(hits)[:8]}
    if contract_check:
        plan_meta["contract_check"] = contract_check
    await register_artifact(
        session_id,
        artifact_id=geojson_ref,
        artifact_type=outputs[0] if outputs else None,
        producer_capability=hits[0],
        producer_tool=tool_name,
        producer_node=hits[0],
        inputs=inputs,
        descriptor=descriptor,
        metadata=plan_meta,
        lock=lock,
    )
