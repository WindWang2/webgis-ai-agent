"""Data Delivery Store — 目录布局、原子发布与 durable 登记（G18 WP08）。

布局（任务独占；不新建公共裸目录）::

    DATA_DIR/data-delivery/.staging/<delivery_id>/…   # 写入+校验中（不可下载）
    DATA_DIR/data-delivery/<delivery_id>/…             # 原子发布后的终态包
        <filename>.<ext>          # 主交付文件（或多文件成员）
        manifest.json             # 成员/哈希/CRS/变换/损失清单
        receipt.json              # 完整回执（下载与 UI 恢复的事实源）

发布纪律：staging 内完成校验（成员 sha256 + 反读 oracle）后，同卷
``os.replace`` 目录原子更名 —— 中断绝不产生「半成品 + 正确扩展名」的
终态目录。durable 身份经既有 ``publish_data_object``（CAS；预算内），
台账经 ``register_artifact``；项目产物登记写入 ``artifacts`` +
``artifact_lineages``（父源 lineage，如有）。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DELIVERY_DIRNAME = "data-delivery"
_STAGING_DIRNAME = ".staging"


def delivery_root() -> Path:
    from app.core.config import settings

    root = Path(settings.DATA_DIR) / _DELIVERY_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def staging_dir(delivery_id: str) -> Path:
    d = delivery_root() / _STAGING_DIRNAME / delivery_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def final_dir(delivery_id: str) -> Path:
    return delivery_root() / delivery_id


def new_delivery_id() -> str:
    """32 hex —— 不可枚举的交付目录名（staging 不可被下载路径猜中）。"""
    return secrets.token_hex(16)


def discard_staging(delivery_id: str) -> None:
    """清理本任务的 staging（只删自己目录；绝不触碰源数据/其它产物）。"""
    d = delivery_root() / _STAGING_DIRNAME / delivery_id
    shutil.rmtree(d, ignore_errors=True)


def discard_final(delivery_id: str) -> None:
    """清理本任务的终态交付目录（仅取消收敛/补偿路径；不碰源数据/共享 blob）。"""
    shutil.rmtree(final_dir(delivery_id), ignore_errors=True)


def atomic_publish(delivery_id: str) -> Path:
    """staging → 终态目录原子更名（同卷 os.replace；目标已存在 → typed 失败）。"""
    src = delivery_root() / _STAGING_DIRNAME / delivery_id
    dst = final_dir(delivery_id)
    if not src.is_dir():
        raise FileNotFoundError(f"staging missing for delivery {delivery_id}")
    os.replace(src, dst)
    return dst


def write_manifest(delivery_id_dir: Path, manifest: Dict[str, Any]) -> Path:
    p = delivery_id_dir / "manifest.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def write_receipt(delivery_id_dir: Path, receipt: Dict[str, Any]) -> Path:
    p = delivery_id_dir / "receipt.json"
    p.write_text(json.dumps(receipt, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def read_receipt(delivery_id: str) -> Optional[Dict[str, Any]]:
    p = final_dir(delivery_id) / "receipt.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 不可读按无回执处理（诚实）
        return None


def delivery_files(delivery_id: str) -> List[Path]:
    """终态目录的数据成员（manifest/receipt 之外）。"""
    d = final_dir(delivery_id)
    if not d.is_dir():
        return []
    return [p for p in sorted(d.iterdir())
            if p.is_file() and p.name not in ("manifest.json", "receipt.json")]


def publish_data_object_for_delivery(
    delivery_id: str, *, owner_scope: Dict[str, str],
    source_refs: Optional[List[str]] = None,
    input_fingerprint: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    budget: int = 256 * 1024 * 1024,
) -> Dict[str, Any]:
    """终态包 → durable DataObject（CAS 去重）。

    返回 ``{"state": "published"|"oversized"|"failed", ...}`` —— 三种终态
    可区分（评审 P3：oversized 与 failed 曾被混为 None）。失败/超预算均
    诚实降级，交付本体（磁盘 + 下载）不受影响。

    单一内容后端纪律：经既有 ``publish_data_object``，不建第二 store。
    """
    from app.services.data_delivery.store import delivery_files as _files
    from app.services.lakehouse.data_object import (
        DataObjectTooLargeError,
        publish_data_object,
    )

    d = final_dir(delivery_id)
    files = _files(delivery_id)
    if not files:
        return {"state": "failed", "error": "no delivery files"}
    source_files = {p.name: p for p in files}
    try:
        identity = publish_data_object(
            source_files,
            kind="data_delivery",
            owner_scope=owner_scope,
            payload=payload or {"delivery_id": delivery_id},
            producer={"tool": "data_delivery", "delivery_id": delivery_id},
            source_refs=source_refs,
            input_fingerprint=input_fingerprint,
            max_total_bytes=budget,
        )
        return {
            "data_object_id": identity.data_object_id,
            "manifest_location": identity.manifest_location,
            "content_sha256": identity.content_sha256,
            "byte_size": identity.byte_size,
            "deduped": identity.deduped,
        }
    except DataObjectTooLargeError:
        return {"state": "oversized"}
    except Exception as e:  # noqa: BLE001 — durable 发布失败诚实降级
        logger.warning("[data-delivery] data object publish failed for %s: %s",
                       delivery_id, e)
        return {"state": "failed", "error": str(e)[:200]}


async def register_session_artifact(
    session_id: str, delivery_id: str, title: str,
    descriptor: Dict[str, Any], metadata: Dict[str, Any],
) -> Optional[str]:
    """session 台账登记（ref:delivery/<id>；增值记录，绝不阻断交付）。"""
    try:
        from app.services.artifact_registry import register_artifact

        ref = f"ref:delivery/{delivery_id}"
        record = await register_artifact(
            session_id,
            artifact_id=ref,
            artifact_type="data_delivery",
            producer_capability="data_delivery",
            producer_tool="submit_data_delivery",
            descriptor=descriptor,
            metadata=metadata,
        )
        return ref if record is not None else ref
    except Exception as e:  # noqa: BLE001
        logger.warning("[data-delivery] ledger registration skipped for %s: %s",
                       delivery_id, e)
        return None


def register_project_artifact(
    project_id: str, delivery_id: str, name: str, fmt: str,
    content_sha256: Optional[str], byte_size: int,
    row_count: int, parent_artifact_id: Optional[str],
) -> Optional[str]:
    """项目产物登记（可选；失败不回滚已完成交付 —— 下载以 receipt 为准）。

    父源 lineage：源为项目产物时挂 parent → child（既有 ArtifactLineage）。
    """
    try:
        from sqlalchemy import select as sa_select

        from app.core.database import SessionLocal
        from app.models.project import Artifact, ArtifactLineage, Project

        with SessionLocal() as db:
            project = db.execute(
                sa_select(Project.id).where(Project.id == project_id)
            ).first()
            if project is None:
                return None
            artifact = Artifact(
                project_id=project_id,
                name=(name or "数据交付")[:255],
                artifact_type="data_delivery",
                format=fmt,
                content_fingerprint=content_sha256,
                metadata_json={
                    "delivery_id": delivery_id,
                    "content_sha256": content_sha256,
                    "byte_size": byte_size,
                    "row_count": row_count,
                    "content_status": "promoted",
                    "content_location": f"data-delivery/{delivery_id}/",
                },
            )
            db.add(artifact)
            db.flush()
            if parent_artifact_id:
                db.add(ArtifactLineage(
                    artifact_id=artifact.id,
                    parent_artifact_id=parent_artifact_id,
                    producing_tool="submit_data_delivery",
                ))
            db.commit()
            return artifact.id
    except Exception as e:  # noqa: BLE001
        logger.warning("[data-delivery] project artifact registration failed "
                       "for %s: %s", delivery_id, e)
        return None
