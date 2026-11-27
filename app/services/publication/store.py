"""发布存储（WP2）—— DB 行 + ``DATA_DIR/publications/{publication_id}/`` 产物。

路径派生单一真相（同 ``export_paths`` 惯例）：调用时取值，DATA_DIR 运行时
覆写即刻全链生效。``publications`` 目录名加入 ``scoped_raster_paths`` 的
reserved 词表，避免与会话作用域目录混读。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.map_publication import (
    ALLOWED_ACCESS_POLICIES,
    PUBLICATION_STATUS_DRAFT,
    PUBLICATION_STATUS_PUBLISHED,
    PUBLICATION_STATUS_REVOKED,
    MapPublication,
    utcnow_naive,
)

#: publication id 形态（uuid4；路径段安全）。
_PUBLICATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

SNAPSHOT_FILE_NAME = "snapshot.json"
LAYERS_DIR_NAME = "layers"


def publications_root() -> Path:
    """发布产物根目录（调用时取值 —— 单一真相）。"""
    return Path(settings.DATA_DIR) / "publications"


def publication_dir(publication_id: str) -> Path:
    if not _PUBLICATION_ID_RE.fullmatch(str(publication_id)):
        raise ValueError("invalid publication id")
    return publications_root() / publication_id


def layer_artifact_path(publication_id: str, layer_id: str) -> Path:
    """层物化产物路径。layer_id 经严格白名单（与 MapSpec 层 id 词表一致）。"""
    if not re.fullmatch(r"[A-Za-z0-9_\-.]{1,200}", str(layer_id)):
        raise ValueError("invalid layer id")
    return publication_dir(publication_id) / LAYERS_DIR_NAME / f"{layer_id}.geojson"


def write_layer_artifact(publication_id: str, layer_id: str, fc: Dict[str, Any]) -> int:
    """原子写层产物（temp+replace）；返回字节数。同步（物化在线程/worker 内）。"""
    import json
    import os

    path = layer_artifact_path(publication_id, layer_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(fc, ensure_ascii=False, separators=(",", ":"))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)
    return len(body.encode("utf-8"))


def load_layer_artifact(publication_id: str, layer_id: str) -> Optional[Dict[str, Any]]:
    """读层产物；缺失/损坏返回 None（reader 端点转为 typed 404/503）。"""
    import json

    try:
        path = layer_artifact_path(publication_id, layer_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_snapshot_file(publication_id: str, snapshot_doc: Dict[str, Any]) -> None:
    """冻结快照 JSON 落盘（与 DB snapshot 列同内容；恢复/巡检用）。"""
    import json
    import os

    path = publication_dir(publication_id) / SNAPSHOT_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(snapshot_doc, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def load_snapshot_file(publication_id: str) -> Optional[Dict[str, Any]]:
    import json

    try:
        path = publication_dir(publication_id) / SNAPSHOT_FILE_NAME
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def artifacts_ready(publication_id: str, layer_ids: list[str]) -> bool:
    """产物就绪 = 所有 materialized 层文件存在（恢复/巡检判据）。"""
    return all(layer_artifact_path(publication_id, lid).is_file() for lid in layer_ids)


# ── DB 行操作 ─────────────────────────────────────────────────────────


async def get_publication(db: AsyncSession, publication_id: str) -> Optional[MapPublication]:
    result = await db.execute(
        select(MapPublication).where(MapPublication.id == publication_id)
    )
    return result.scalar_one_or_none()


async def get_publication_by_share_code(
    db: AsyncSession, share_code: str
) -> Optional[MapPublication]:
    result = await db.execute(
        select(MapPublication).where(MapPublication.share_code == share_code)
    )
    return result.scalar_one_or_none()


async def get_active_version_for_product(
    db: AsyncSession, product_id: str
) -> Optional[MapPublication]:
    """产品当前 published 版本（幂等发布/新版本查询用）。"""
    result = await db.execute(
        select(MapPublication)
        .where(
            MapPublication.product_id == product_id,
            MapPublication.status == PUBLICATION_STATUS_PUBLISHED,
        )
        .order_by(MapPublication.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_product_versions(
    db: AsyncSession, product_id: str
) -> list[MapPublication]:
    result = await db.execute(
        select(MapPublication)
        .where(MapPublication.product_id == product_id)
        .order_by(MapPublication.version.desc())
    )
    return list(result.scalars().all())


async def list_session_publications(
    db: AsyncSession, session_id: str
) -> list[MapPublication]:
    result = await db.execute(
        select(MapPublication)
        .where(MapPublication.session_id == session_id)
        .order_by(MapPublication.created_at.desc())
        .limit(100)
    )
    return list(result.scalars().all())


async def next_version_for_product(db: AsyncSession, product_id: str) -> int:
    result = await db.execute(
        select(MapPublication.version)
        .where(MapPublication.product_id == product_id)
        .order_by(MapPublication.version.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    return int(latest or 0) + 1


async def transition_status(
    db: AsyncSession,
    publication: MapPublication,
    *,
    expected: tuple[str, ...],
    target: str,
) -> bool:
    """CAS 状态迁移（行级乐观锁语义：仅当当前 status ∈ expected 时落 target）。"""
    if publication.status not in expected or target not in (
        PUBLICATION_STATUS_DRAFT, PUBLICATION_STATUS_PUBLISHED, PUBLICATION_STATUS_REVOKED
    ):
        return False
    publication.status = target
    if target == PUBLICATION_STATUS_PUBLISHED:
        publication.published_at = utcnow_naive()
    if target == PUBLICATION_STATUS_REVOKED:
        publication.revoked_at = utcnow_naive()
        # 撤销即断链（读者侧立即 404，与 report revoke 同语义）。
        publication.share_code = None
        publication.share_expires_at = None
    await db.commit()
    return True


def validate_access_policy(policy: str) -> bool:
    return policy in ALLOWED_ACCESS_POLICIES


__all__ = [
    "publications_root",
    "publication_dir",
    "layer_artifact_path",
    "write_layer_artifact",
    "load_layer_artifact",
    "write_snapshot_file",
    "load_snapshot_file",
    "artifacts_ready",
    "get_publication",
    "get_publication_by_share_code",
    "get_active_version_for_product",
    "list_product_versions",
    "list_session_publications",
    "next_version_for_product",
    "transition_status",
    "validate_access_policy",
]
