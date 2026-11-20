"""G22-P6 会话恢复可读性摘要（Pi 长会话状态连续性）。

会话恢复（刷新 / 重连 / 压缩后重进）成功把 transcript 装回屏幕之后，用户
看不到「地图恢复到了哪一版 / 哪些数据指针已经死了 / 哪些人工约束还活着」。
本模块把这些**权威源现取**的事实折叠成一个紧凑、诚实、只读的 summary dict
（GET /chat/sessions/{id}?recovery=1 附挂），前端横幅（recovery-banner.tsx）
按字段拼中文短句 —— 绝不虚构源里不存在的记录，也绝不携带载荷或凭据。

事实来源与 G22-P2 的权威恢复目录（context_assembly/assembly.py +
providers.render_gis_state_rebuild_block）同源，口径一致：
- revision / layers / frame_product / workbench 锁 —— MapSpec 现值；
- 失效 source refs —— ``session_data_manager.has(session_id, ref)`` 核对
  （后端未实现 ``has`` 或读失败 → 无法核实 → 空列表，不虚构「失效」）；
- durable jobs —— analysis_tasks 表非终态行（ACTIVE_STATUSES）。

一切都 best-effort：任一阶段失败 → 该字段缺席/为空，绝不抛出。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

#: 对外披露的 id 截断长度（诊断可寻址即可，不放大泄漏面）。
_ID_TRUNC = 32
#: 失效 source refs / durable jobs 的披露上限（摘要是有界的）。
_MAX_INVALID_REFS = 8
_MAX_DURABLE_JOBS = 8


def _layer_is_hidden(layer: dict) -> bool:
    """``layer.layout.visibility == "none"`` 判定。

    镜像 ``app/services/context_assembly/providers.py`` 的同名私有谓词
    （G22-P2 权威口径）：canonical 位置在 ``layer.layout.visibility``，
    缺席回退 legacy 顶层 flags，未知形状 → 可见（不发明源没写的隐藏态）。
    """
    layout = layer.get("layout")
    if isinstance(layout, dict):
        vis = layout.get("visibility")
        if isinstance(vis, str):
            return vis == "none"
    if "visible" in layer:
        return layer.get("visible") is False
    if "visibility" in layer:
        return layer.get("visibility") in (False, "none", "hidden")
    return False


def _invalid_source_refs(session_id: str, mapspec: Dict[str, Any]) -> List[Dict[str, str]]:
    """mapspec sources → session ref 指针存在性核对（仅 ref 型指针）。

    与 assembly._source_ref_invalid 同判定；差异在缺席语义的折叠方向：
    P6 摘要是给用户的「有无」面，sources 缺席 / 无法核实（后端无 ``has``、
    读失败）一律 ``[]`` —— 不虚构失效，也不虚构有效（空 = 无可披露项）。
    url 型指针不走 session ref 生命周期，不在此判定。
    """
    sources = mapspec.get("sources")
    if not isinstance(sources, dict) or not sources:
        return []
    try:
        from app.services.session_data import session_data_manager

        has_fn = getattr(session_data_manager, "has", None)
        if not callable(has_fn):
            return []
        invalid: List[Dict[str, str]] = []
        for source_id, source in sources.items():
            if not isinstance(source, dict):
                continue
            ref = source.get("refId") or source.get("ref_id") or ""
            if not ref or not isinstance(ref, str):
                continue
            if not has_fn(session_id, ref):
                invalid.append(
                    {"source": str(source_id)[:_ID_TRUNC], "ref": ref[:_ID_TRUNC]}
                )
            if len(invalid) >= _MAX_INVALID_REFS:
                break
        return invalid
    except Exception:  # noqa: BLE001 — 无法核实 ≠ 失效；摘要不因单源失败红
        return []


async def _durable_jobs(session_id: str) -> List[Dict[str, str]]:
    """非终态 durable jobs（analysis_tasks，ACTIVE_STATUSES），最新在前。

    状态如实（queued/running 绝不显示为完成）；失败 → 空列表（任务中心
    仍是权威面，摘要缺席不算说谎）。
    """
    try:
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.db_model import AnalysisTask
        from app.services.jobs.lifecycle import ACTIVE_STATUSES

        async with AsyncSessionLocal() as db:
            stmt = (
                select(AnalysisTask)
                .where(
                    AnalysisTask.session_id == session_id,
                    AnalysisTask.status.in_(
                        [s.value for s in ACTIVE_STATUSES]),
                )
                .order_by(AnalysisTask.created_at.desc())
                .limit(_MAX_DURABLE_JOBS)
            )
            rows = list((await db.execute(stmt)).scalars().all())
            return [
                {"id": str(row.id)[:_ID_TRUNC], "status": str(row.status)[:_ID_TRUNC]}
                for row in rows[:_MAX_DURABLE_JOBS]  # SQL LIMIT 之外的二道防线
            ]
    except Exception:  # noqa: BLE001 — DB 不可用 → 摘要不带任务面
        return []


def _user_constraints(mapspec: Dict[str, Any], layers: List[dict]) -> Dict[str, Any]:
    """恢复后必须原样保留的人工约束投影（锁/隐藏/标注/画幅）。

    逐字段 isinstance 守卫：单个形状漂移 → 该项按缺席折叠，不阻断其余。
    """
    locked_layers: List[str] = []
    locked_components = 0
    try:
        from app.contracts.workbench_locks import (
            locked_component_ids_of,
            locked_layer_ids_of,
        )

        locked_layers = [x[:_ID_TRUNC] for x in locked_layer_ids_of(mapspec)]
        locked_components = len(locked_component_ids_of(mapspec))
    except Exception:  # noqa: BLE001 — 锁事实缺席不虚构
        locked_layers, locked_components = [], 0

    hidden_layers: List[str] = []
    for layer in layers:
        lid = str(layer.get("id") or layer.get("layer_id") or "")
        if lid and _layer_is_hidden(layer):
            hidden_layers.append(lid[:_ID_TRUNC])

    label_overrides = 0
    for layer in layers:
        label = layer.get("label")
        if not isinstance(label, dict):
            continue
        authoring = label.get("authoring")
        if not isinstance(authoring, dict):
            continue
        overrides = authoring.get("overrides")
        if isinstance(overrides, (dict, list)):
            label_overrides += len(overrides)

    active_frame: str | None = None
    frame_product = mapspec.get("frame_product")
    if isinstance(frame_product, dict):
        af = frame_product.get("active_frame")
        if isinstance(af, str) and af:
            active_frame = af[:_ID_TRUNC]

    return {
        "locked_layers": locked_layers,
        # 组件锁是紧凑计数（不是 id 列表）：横幅只说「层」，计数供诊断 title。
        "locked_components": locked_components,
        "hidden_layers": hidden_layers,
        "label_overrides": label_overrides,
        "active_frame": active_frame,
    }


def _revision_of(mapspec: Dict[str, Any]) -> Any:
    """MapSpec revision（int 优先；无法解析的形状按 spec 原样透传）。"""
    scene = mapspec.get("scene")
    raw = mapspec.get("revision")
    if raw is None and isinstance(scene, dict):
        raw = scene.get("revision")
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


async def build_recovery_summary(session_id: str) -> Dict[str, Any]:
    """构建会话恢复摘要（只读、有界、绝不抛出）。

    返回形状（恢复成功）::

        {"status": "ok", "session_id": ..., "recovered": True,
         "recovered_to_revision": int|None, "layer_count": int,
         "invalid_source_refs": [{"source", "ref"}...≤8],
         "durable_jobs": [{"id", "status"}...≤8],
         "user_constraints": {"locked_layers": [...], "locked_components": int,
                              "hidden_layers": [...], "label_overrides": int,
                              "active_frame": str|None}}

    MapSpec 缺席（空会话 / 已删除）→ ``{"status": "ok", "recovered": False}``
    ——「无 GIS 状态可恢复」本身是诚实结论，不是错误。
    """
    sid = str(session_id or "")
    if not sid:
        return {"status": "ok", "session_id": sid, "recovered": False}

    try:
        from app.services.mapspec.store import mapspec_store_instance

        mapspec = await mapspec_store_instance.get_mapspec(sid)
    except Exception:  # noqa: BLE001 — spec 读失败按缺席（不虚构恢复事实）
        logger.debug("[recovery_summary] mapspec read failed for %s", sid[:24])
        mapspec = None
    if not isinstance(mapspec, dict):
        return {"status": "ok", "session_id": sid, "recovered": False}

    layers = [x for x in (mapspec.get("layers") or []) if isinstance(x, dict)]
    return {
        "status": "ok",
        "session_id": sid,
        "recovered": True,
        "recovered_to_revision": _revision_of(mapspec),
        "layer_count": len(layers),
        "invalid_source_refs": _invalid_source_refs(sid, mapspec),
        "durable_jobs": await _durable_jobs(sid),
        "user_constraints": _user_constraints(mapspec, layers),
    }


__all__ = ["build_recovery_summary"]
