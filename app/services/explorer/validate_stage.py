"""
Validate Stage — Pure async stage runner for final dataset quality validation.
"""
import logging
from typing import Callable, List, Optional

from app.services.explorer.models import StageResult

logger = logging.getLogger(__name__)


async def bridge_geocoded_to_session(
    task_id: str,
    session_id: str,
    geocoded_ref_id: str,
) -> dict:
    """#776: 把 explorer 的 geocoded 结果桥接进 chat session 命名空间。

    此前最终行数据只存在 ``explorer:{task_id}`` 命名空间 —— 会话侧的
    ref: 解析、前端与 agent 都无法消费（探索白跑、付费 geocoding 照烧）。
    桥接 = 同一 payload 存入 chat session + 登记 alias，使后续
    ``add_layer(ref:...)`` / 分析工具可解析。返回桥接描述（失败返回 {}，
    非致命：桥接失败只影响可消费性，不影响探索结果本身）。
    """
    from app.services.session_data_protocol import get_session_store

    store = get_session_store()
    payload = await store.get(f"explorer:{task_id}", geocoded_ref_id)
    if not payload:
        logger.warning(
            f"[Explorer:{task_id}] bridge: geocoded ref {geocoded_ref_id} "
            "unresolvable from explorer namespace — skip session bridge"
        )
        return {}
    ref_id = await store.store(session_id, payload, prefix="explorer_geocoded")
    alias = f"explorer:{task_id}"
    try:
        await store.set_alias(session_id, ref_id, alias)
    except Exception as e:  # noqa: BLE001 — alias 是便利层，登记失败不致命
        logger.warning(f"[Explorer:{task_id}] bridge: alias registration failed: {e}")
    logger.info(
        f"[Explorer:{task_id}] bridged geocoded rows into session "
        f"{session_id} as {ref_id} (alias {alias})"
    )
    return {"session_ref_id": ref_id, "session_ref_alias": alias}


async def run_validate_stage(
    task_id: str,
    geocoded_ref_id: Optional[str] = None,
    total_rows: int = 0,
    fetch_errors: Optional[List[dict]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    session_id: str = "",
) -> StageResult:
    """
    Execute the validation stage.
    Finalizes dataset verification and returns the completion summary.

    #774: ``fetch_errors`` (per-source fetch failures from the fetch stage) are
    included in the final summary so a partial exploration never reports
    survivor-only results as complete.

    #776: 有 chat session 上下文且存在 geocoded 结果时，把结果桥接进
    session 命名空间（``session_ref_id`` / ``session_ref_alias``）—— 探索
    产出从「任务命名空间里的死数据」变成会话可消费的 ref。
    """
    if on_progress:
        on_progress(100)

    bridge: dict = {}
    if session_id and geocoded_ref_id:
        try:
            bridge = await bridge_geocoded_to_session(task_id, session_id, geocoded_ref_id)
        except Exception as e:  # noqa: BLE001 — 桥接失败不判定探索失败
            logger.warning(f"[Explorer:{task_id}] session bridge failed: {e}")

    return StageResult(
        stage="validate",
        data={
            "task_id": task_id,
            "status": "completed",
            "geocoded_ref_id": geocoded_ref_id,
            "total_rows": total_rows,
            "fetch_errors": fetch_errors or [],
            **bridge,
        },
        success=True,
    )
