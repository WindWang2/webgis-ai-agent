"""Export Artifact Lineage（ADR-0211）—— 导出成品的一等血缘与交付回执。

把「用户实际导出了什么」接进既有事实记录层，不建第二真相：

- **血缘**：`ref:export/<filename>` 注册进 artifact_registry（ADR-0082
  ledger；inputs = 导出时 MapSpec 的 source/chartRef/tableRef 数据 refs），
  metadata 携带 format/dpi/pages/spec_digest/mapspec_revision/降级码摘要。
  注册是增值记录：任何失败降级为返回 None，绝不阻断导出路径。
- **回执**：`gis_chapter["export_receipts"]`（goal_satisfaction 既有证据
  契约键 `[{format, revision, created_at}]`）—— 此前生产零写方，导出需求
  评估恒 absent；本模块是其唯一生产方。按 format 去重保最新（幂等）。

安全：``session_id`` 是客户端声称值，写路径前必须过 ``verify_session_owner``
（DB 元数据属主查询，SEC-08 同款）；不匹配/无会话 → 整体跳过（不泄露
存在性，导出成功语义不变）。``db`` 由调用方以 ``Depends(get_async_db)``
注入（生命周期归 FastAPI；测试可注入等效对象或 monkeypatch 本函数）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

MAX_RECEIPTS = 8
_MAX_DEGRADATION_CODES = 8
_MAX_SOURCE_REFS = 16
#: 成品文件名边界（artifact_id = ref:export/<filename> 直接入 ledger，
#: 超长名在边界拒绝 —— 不静默截断，截断会造成 id 碰撞）。路由生成名
#: （map_export_<ts>_<hex>.<ext>）≈ 40 字符，裕量充足。
_MAX_FILENAME_CHARS = 200


def export_ref(filename: str) -> str:
    """成品文件名 → 会话内血缘 ref（artifact_id）。"""
    return f"ref:export/{filename}"


def receipt_format(ext: str) -> str:
    """扩展名 → 回执 format 词（jpg 归一 jpeg；goal 契约按小写比对）。"""
    fmt = str(ext or "").lower().lstrip(".")
    return "jpeg" if fmt == "jpg" else fmt


def _source_refs(mapspec: Any) -> List[str]:
    """导出时 MapSpec 的数据 refs（血缘 inputs；与 registry 活集合同口径）。"""
    if not isinstance(mapspec, dict):
        return []
    out: List[str] = []

    def _add(v: Any) -> None:
        if isinstance(v, str) and v.startswith("ref:") and v not in out:
            out.append(v)

    raw_sources = mapspec.get("sources")
    if isinstance(raw_sources, dict):
        source_defs: Iterable[Any] = raw_sources.values()
    else:
        source_defs = raw_sources or []
    for src in source_defs:
        if isinstance(src, dict):
            for key in ("ref", "ref_id", "image_ref", "imageRef", "result_ref"):
                _add(src.get(key))
    for comp in (mapspec.get("layout") or {}).get("components") or []:
        if isinstance(comp, dict):
            opts = comp.get("options") or {}
            _add(opts.get("chartRef"))
            _add(opts.get("tableRef"))
    return out[:_MAX_SOURCE_REFS]


def _receipt(revision: int, filename: str, fmt: str) -> Dict[str, Any]:
    return {
        "format": fmt,
        "revision": str(int(revision or 0)),
        "created_at": time.time(),
        "filename": str(filename)[:96],
    }


def _merge_receipts(existing: Any, receipt: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按 format 去重保最新、有界（≤8；同 format 重复导出幂等 = 覆盖写）。"""
    fmt = str(receipt.get("format") or "")
    kept = [
        dict(r) for r in (existing or [])
        if isinstance(r, dict)
        and str(r.get("format") or "").strip().lower()
        and str(r.get("format") or "").strip().lower() != fmt
    ][: MAX_RECEIPTS - 1]
    return [receipt] + kept


async def record_export_lineage(
    session_id: str,
    *,
    filename: str,
    ext: str,
    user_id: str,
    db: Any = None,
    owner_token: Optional[str] = None,
    title: str = "",
    vector: bool = False,
    pages: int = 0,
    target_dpi: int = 0,
    degradation_codes: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """导出落盘后记录血缘 + 回执（全路径 best-effort；失败 → None）。

    返回 ``{"ref", "artifact_recorded", "receipt_recorded", "format"}``；
    ``receipt_recorded=False`` 表示会话无 GIS 章节（纯聊天导出诚实无回执）。
    ``db``：AsyncSession（路由 Depends 注入）；None 时按无 DB 上下文跳过
    （属主守卫无法执行 = 不写任何跨会话状态，fail-closed）。
    """
    if not session_id or not filename or db is None:
        return None
    if len(filename) > _MAX_FILENAME_CHARS:
        logger.warning("[export-lineage] filename too long (%d) — skipped",
                       len(filename))
        return None

    from app.core.auth import verify_session_owner
    from app.services.artifact_registry import register_artifact
    from app.services.mapspec_store import mapspec_store
    from app.services.session_data import session_data_manager
    from app.services.session_plan import load_session_plan

    # 1) 跨租户写守卫：session_id 必须属于当前 user/owner_token。
    try:
        await verify_session_owner(
            db, session_id,
            user_id=user_id or None, owner_token=owner_token or None,
        )
    except Exception as exc:  # noqa: BLE001 — 404/403/DB 瞬态一律诚实跳过
        logger.info(
            "[export-lineage] ownership guard skipped session=%s reason=%s",
            session_id, type(exc).__name__,
        )
        return None

    # 2) 导出时点事实：MapSpec 数据 refs + 服务端权威 revision + spec digest。
    fmt = receipt_format(ext)
    try:
        mapspec = await mapspec_store.get_mapspec(session_id)
    except Exception:  # noqa: BLE001
        mapspec = None
    try:
        state = await session_data_manager.get_map_state(session_id)
        revision = int((state or {}).get("_cartographic_mutation_revision") or 0)
    except Exception:  # noqa: BLE001
        revision = 0

    # 3) 血缘注册（registry 内部自取 per-session lock；增值，绝不阻断）。
    metadata: Dict[str, Any] = {
        "format": fmt,
        "vector": bool(vector),
        "title": str(title or "")[:80],
        "mapspec_revision": int(revision),
    }
    if pages:
        metadata["pages"] = int(pages)
    if target_dpi:
        metadata["dpi"] = int(target_dpi)
    codes = [str(c)[:32] for c in (degradation_codes or []) if c][
        :_MAX_DEGRADATION_CODES]
    if codes:
        metadata["degradation_codes"] = codes
    artifact_recorded = False
    receipt_recorded = False
    try:
        plan = await load_session_plan(session_id)
        chapter = plan.gis_chapter if plan is not None else None
        if isinstance(chapter, dict):
            raw_spec = chapter.get("product_spec")
            if isinstance(raw_spec, dict) and isinstance(raw_spec.get("digest"), str):
                metadata["spec_digest"] = raw_spec["digest"][:32]
        rec = await register_artifact(
            session_id,
            artifact_id=export_ref(filename),
            artifact_type="map_export",
            producer_tool="map_export_route",
            inputs=_source_refs(mapspec),
            revision=int(revision),
            metadata=metadata,
        )
        artifact_recorded = rec is not None
    except Exception:  # noqa: BLE001 — 注册失败不影响回执/导出
        logger.info("[export-lineage] artifact register failed session=%s",
                    session_id, exc_info=True)

    # 4) 回执落章（与 finalizer 持久化同锁纪律；无 GIS 章节 = 无回执）。
    try:
        from app.services.distributed_lock import session_lock_registry
        from app.services.session_plan import save_session_plan

        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
            if not lock.lost:
                fresh = await load_session_plan(session_id)
                if fresh is not None and isinstance(fresh.gis_chapter, dict):
                    fresh.gis_chapter["export_receipts"] = _merge_receipts(
                        fresh.gis_chapter.get("export_receipts"),
                        _receipt(revision, filename, fmt),
                    )
                    await save_session_plan(fresh)
                    receipt_recorded = True
    except Exception:  # noqa: BLE001 — 回执失败下一触发点重试
        logger.info("[export-lineage] receipt persist failed session=%s",
                    session_id, exc_info=True)

    return {
        "ref": export_ref(filename),
        "artifact_recorded": artifact_recorded,
        "receipt_recorded": receipt_recorded,
        "format": fmt,
    }


__all__ = [
    "export_ref",
    "receipt_format",
    "record_export_lineage",
]
