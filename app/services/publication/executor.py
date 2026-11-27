"""发布物化 executor（WP2）—— 冻结快照 → 有界投影产物 → CAS published。

与 atlas_batch.executor 同构：typed 失败 = job failed（错误证据直写
result_summary）；取消/重复投递沿 durable_job 框架语义。

发布事务（单点）：
1. 冻结快照读取（draft 行 + snapshot JSON）；
2. ref 源水合（复用 publication_export.hydrate_ref_sources —— 与矢量 PDF
   链同一水合事实源）；
3. 逐层有界投影（字段白名单/精度/bbox/预算）→ 原子写产物文件；
4. 产物齐全 → materialized=1 → CAS draft→published。
   任一步 typed 失败 → job failed（行保持 draft，可重试）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class PublicationMaterializeError(RuntimeError):
    """typed 物化失败（reason 机器可读；行保持 draft 可重试）。"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def materialize_publication(publication_id: str) -> Dict[str, Any]:
    """冻结快照 → 产物落盘 → CAS published（同步核心；worker 与测试直调共用）。

    返回 {status, publication_id, layers: {layer_id: bytes}, total_bytes}。
    """
    from app.core.async_runner import run_sync
    from app.tools._utils import db_session

    from app.models.map_publication import (
        PUBLICATION_STATUS_DRAFT,
        MapPublication,
        utcnow_naive,
    )
    from app.services.publication.contract import DataMode, PublicationBlockerReason
    from app.services.publication.projection import (
        PUBLICATION_TOTAL_BUDGET_BYTES,
        project_feature_collection,
    )
    from app.services.publication import store as pub_store
    from app.services.publication.service import hydrate_geojson_sources

    with db_session() as db:
        row = db.get(MapPublication, publication_id)
        if row is None:
            raise PublicationMaterializeError(
                "publication_not_found", f"发布 {publication_id} 不存在"
            )
        if row.status != PUBLICATION_STATUS_DRAFT:
            raise PublicationMaterializeError(
                "not_draft", f"发布 {publication_id} 状态为 {row.status}，仅 draft 可物化"
            )
        snapshot = dict(row.snapshot or {})
        session_id = row.session_id

    spec = snapshot.get("spec")
    if not isinstance(spec, dict):
        raise PublicationMaterializeError("snapshot_invalid", "快照缺少冻结 spec")
    layer_infos = snapshot.get("layers") or []
    targets = [
        lyr for lyr in layer_infos
        if isinstance(lyr, dict) and lyr.get("data_mode") == DataMode.materialized.value
    ]
    if not targets:
        raise PublicationMaterializeError(
            PublicationBlockerReason.FIELD_WHITELIST_EMPTY.value,
            "没有可物化的层（发布选择为空或全部被阻断）",
        )

    # 水合（async 数据面 → 线程安全包装；与矢量 PDF 链同一事实源）。
    try:
        hydrated, failed = run_sync(hydrate_geojson_sources(spec, session_id))
    except Exception as exc:  # noqa: BLE001 — typed 包装（job failed 证据）
        raise PublicationMaterializeError(
            "hydration_failed", f"会话数据水合失败：{type(exc).__name__}"
        ) from exc

    total_bytes = 0
    written: Dict[str, int] = {}
    for lyr in targets:
        layer_id = str(lyr.get("layer_id", ""))
        source_id = str(lyr.get("source_id", ""))
        fc = hydrated.get(source_id)
        if fc is None:
            raise PublicationMaterializeError(
                "source_data_unavailable",
                f"层 {layer_id} 的源 {source_id} 数据不可用（会话数据可能已失效）",
            )
        projected, blocker = project_feature_collection(
            fc,
            field_whitelist=list(lyr.get("field_whitelist") or []),
            precision=int((snapshot.get("scope") or {}).get("geometry_precision") or 6),
            bbox=None,
        )
        if blocker is not None or projected is None:
            raise PublicationMaterializeError(
                (blocker.reason.value if blocker else "projection_failed"),
                f"层 {layer_id} 投影失败：{blocker.message if blocker else '未知'}",
            )
        size = pub_store.write_layer_artifact(publication_id, layer_id, projected)
        written[layer_id] = size
        total_bytes += size
        if total_bytes > PUBLICATION_TOTAL_BUDGET_BYTES:
            raise PublicationMaterializeError(
                PublicationBlockerReason.SNAPSHOT_BUDGET_EXCEEDED.value,
                f"物化累计 {total_bytes} 字节超过发布预算",
            )

    # 产物快照文件（巡检/恢复）+ 发布 CAS（draft→published；发布事务终点）。
    pub_store.write_snapshot_file(publication_id, snapshot)

    with db_session() as db:
        row = db.get(MapPublication, publication_id)
        if row is None:
            raise PublicationMaterializeError("publication_not_found", "发布行已消失")
        if row.status != PUBLICATION_STATUS_DRAFT:
            # 并发/重放：已 published/revoked —— 不回退、不重复 CAS。
            logger.info(
                "[publication] publish CAS skipped job publication_id=%s status=%s",
                publication_id, row.status,
            )
        else:
            row.materialized = 1
            row.status = "published"
            row.published_at = utcnow_naive()
            db.commit()

    return {
        "status": "published",
        "publication_id": publication_id,
        "layers": written,
        "total_bytes": total_bytes,
    }


__all__ = [
    "PublicationMaterializeError",
    "materialize_publication",
]
