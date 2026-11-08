"""
Validate Stage — Pure async stage runner for final dataset quality validation.
"""
import logging
import math
from typing import Any, Callable, List, Optional

from app.services.explorer.models import StageResult

logger = logging.getLogger(__name__)


def rows_to_feature_collection(rows: List[dict]) -> Optional[dict]:
    """geocoded rows → GeoJSON FC（G03 GD-01 / ADR-0286 D3，纯函数）。

    只有坐标有限的行成为要素（geocode 失败/跳过的行不进 FC，不虚构点位）；
    ``_`` 前缀 magic keys（_lat/_lon/_geocode_status…）剥离出 properties。
    零有效行 → None（诚实零要素，由调用方决定不铸语义身份）。

    评审 F11：字符串坐标行（既有坐标列原样拷入的形态）按无坐标跳过并
    debug 日志——诚实缺席，不静默不痕。
    """
    features: List[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lat, lon = row.get("_lat"), row.get("_lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            if lat is not None or lon is not None:
                logger.debug(
                    "[rows_to_fc] row with non-numeric _lat/_lon skipped "
                    "(types: %s/%s)", type(lat).__name__, type(lon).__name__)
            continue
        if isinstance(lat, bool) or isinstance(lon, bool):
            continue
        if not (math.isfinite(float(lat)) and math.isfinite(float(lon))):
            continue
        props = {k: v for k, v in row.items() if not str(k).startswith("_")}
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            "properties": props,
        })
    if not features:
        return None
    return {"type": "FeatureCollection", "features": features}


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

    G03（ADR-0286 D3）：桥接同时把有效坐标行转 FC 经 IngestPipeline 铸
    **语义身份**（descriptor）——role binding/资格裁决从此能消费 explorer
    数据（此前对 explorer 数据只能 not_found）。全部行 geocode 失败 →
    不铸 FC（诚实零要素），``semantic_ref`` 缺席；铸链失败非致命（沿用
    #776 纪律，探索结果本身不受影响）。
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
    bridge: dict[str, Any] = {"session_ref_id": ref_id, "session_ref_alias": alias}

    # ── 语义身份铸造（G03；additive，失败只缺席不致命）─────────────────
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if isinstance(rows, list) and rows:
        fc = rows_to_feature_collection(rows)
        if fc is not None:
            try:
                from app.services.data_ingest.pipeline import get_ingest_pipeline

                ingested = await get_ingest_pipeline().ingest(
                    session_id,
                    fc,
                    name=f"explorer:{task_id}",
                    crs="EPSG:4326",
                    source_type="explorer_geocode",
                    prefix="explorer_geocoded",
                )
                if ingested.ok:
                    bridge["semantic_ref"] = ingested.ref_id
                else:
                    logger.warning(
                        "[Explorer:%s] semantic bridge declined: %s: %s",
                        task_id, ingested.error_code, ingested.error,
                    )
            except Exception as e:  # noqa: BLE001 — 语义桥是增值面，失败不致命
                logger.warning(
                    f"[Explorer:{task_id}] semantic bridge failed: {e}")
    return bridge


async def run_validate_stage(
    task_id: str,
    geocoded_ref_id: Optional[str] = None,
    total_rows: int = 0,
    fetch_errors: Optional[List[dict]] = None,
    parse_errors: Optional[List[dict]] = None,
    missing_refs: Optional[List[str]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    session_id: str = "",
) -> StageResult:
    """
    Execute the validation stage.
    Finalizes dataset verification and returns the completion summary.

    #774: ``fetch_errors`` (per-source fetch failures from the fetch stage) are
    included in the final summary so a partial exploration never reports
    survivor-only results as complete.

    #1740: ``parse_errors`` / ``missing_refs`` (the parse stage's per-source
    failures and its unresolved *fetch* refs — not the geocode summary's own
    ``missing_refs``) ride along on the same track: a partial exploration must
    not hide its own parse failures either.

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
            "parse_errors": parse_errors or [],
            "missing_refs": missing_refs or [],
            **bridge,
        },
        success=True,
    )
