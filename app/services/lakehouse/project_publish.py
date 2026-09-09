"""Project publishing — Spatial Lakehouse V7 (ADR-0119, Scope H).

session → project 的**零字节发布**：blobs 已在 CAS（内容寻址），发布 =
(owner 校验后的) 身份登记 —— find-or-create 项目 Artifact +
``artifact_revisions`` 修订行（内容历史/GC refcount 真相）+ catalog
投影行（检索面）。不建并行内容副本（V6 红线）。

owner 链（R0-9，绝不泄漏存在性）：

1. ``verify_session_owner``（async，SEC-08）—— session 归请求者；
2. project 查无 / 他人 project → ``PublishError``（404 同族语义）；
3. 对象 manifest owner 校验（virtual children 同 owner 归解析层）。

事务/并发形态：DB 变更走**同步单会话单事务**
（``asyncio.to_thread(_publish_sync)`` —— promote 同款），verify 在
async 侧先行；全链幂等（revision/catalog 唯一键双保险），部分失败
重试安全。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


class PublishError(ValueError):
    """发布契约违例（owner 不符 / 对象不可解析 / ref 形态非法）。"""

    code = "LAKEHOUSE_PUBLISH_INVALID"

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code

    def to_dict(self) -> dict:
        return {"success": False, "code": self.code, "message": self.message}


async def publish_to_project(
    db,
    *,
    session_id: str,
    project_id: str,
    object_ids: Sequence[str],
    actor_id: Optional[str],
    owner_token: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    store: Optional[Any] = None,
) -> Dict[str, Any]:
    """发布一组 DataObject / ref:cube 到项目（零字节复制）。

    ``db`` 是 async 会话（verify_session_owner 专用）；事务性变更在
    同步会话内完成。返回
    ``{"published": [...], "unknown": [...], "forbidden": [...]}``。
    """
    from app.core.auth import verify_session_owner
    from app.services.artifact_registry import get_artifact

    if not object_ids or len(object_ids) > 200:
        raise PublishError("object_ids must be 1..200 entries")
    # owner 链 1：session 归请求者（fail-closed 404 语义由守卫给出）。
    await verify_session_owner(
        db, session_id, user_id=actor_id, owner_token=owner_token,
    )
    # ref:cube 的台账解析在 **async 阶段**完成（评审 R1-17：worker 线程
    # 内 asyncio.run 会新建事件循环 —— 与主 loop 绑定的 async 客户端
    # （session store/Redis）可能失效）。
    ref_manifest_ids: Dict[str, Optional[str]] = {}
    for oid in list(object_ids)[:200]:
        oid = str(oid)
        if oid.startswith("ref:cube/"):
            record = await get_artifact(session_id, oid)
            ref_manifest_ids[oid] = (
                str((record.metadata or {}).get("data_object_id"))
                if record is not None and (record.metadata or {}).get(
                    "data_object_id"
                )
                else None
            )
    return await asyncio.to_thread(
        _publish_sync,
        session_id=session_id,
        project_id=project_id,
        object_ids=list(object_ids)[:200],
        actor_id=actor_id,
        tags=list(tags) if tags else None,
        store=store,
        ref_manifest_ids=ref_manifest_ids,
    )


def _publish_sync(
    *,
    session_id: str,
    project_id: str,
    object_ids: List[str],
    actor_id: Optional[str],
    tags: Optional[List[str]],
    store: Optional[Any],
    ref_manifest_ids: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, Any]:
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.project import Project
    from app.services.artifact_revisions import record_revision
    from app.services.lakehouse.catalog_service import (
        derive_entry_from_manifest,
        upsert_catalog_entry,
    )
    from app.services.lakehouse.data_object import (
        is_data_object_id,
        owner_scope_allows,
        resolve_data_object,
    )

    with SessionLocal() as db:
        # owner 链 2：project 归请求者（查无/他人 → 404 同族，不泄漏）。
        project = (
            db.execute(
                select(Project).where(
                    Project.id == project_id, Project.status == "active"
                )
            )
        ).scalar_one_or_none()
        if project is None or (
            actor_id is not None and str(project.owner_id) != str(actor_id)
        ):
            raise PublishError(
                "project not found",
                code="LAKEHOUSE_PUBLISH_PROJECT_MISSING",
            )

        published: List[Dict[str, Any]] = []
        unknown: List[str] = []
        forbidden: List[str] = []
        for oid in object_ids:
            oid = str(oid)
            manifest_blob_id: Optional[str] = None
            if is_data_object_id(oid):
                manifest = resolve_data_object(oid, store=store)
                manifest_blob_id = oid
            elif oid.startswith("ref:cube/"):
                # 台账预解析结果（async 阶段注入 —— R1-17）。manifest
                # blob 键 = 台账登记的 data_object_id（R2-2：绝不以内容
                # 根冒充 —— 幻影 location 使恢复与 GC 保护双双失效）。
                did = (ref_manifest_ids or {}).get(oid)
                manifest = (
                    resolve_data_object(str(did), store=store)
                    if did
                    else None
                )
                manifest_blob_id = str(did) if did else None
            else:
                unknown.append(oid)
                continue
            if manifest is None:
                unknown.append(oid)
                continue
            if not owner_scope_allows(manifest, session_id=session_id):
                forbidden.append(oid)
                continue
            content_sha256 = str(manifest.get("content_sha256") or "")
            if not content_sha256:
                unknown.append(oid)
                continue

            # find-or-create 项目 Artifact（storage_ref = 数据对象身份；
            # record_revision 的 FK 前提 —— R0-8）。
            artifact_id = _find_or_create_artifact(
                db, project_id=project_id, oid=oid, manifest=manifest,
            )
            if not manifest_blob_id:
                unknown.append(oid)
                continue
            revision, created = record_revision(
                db,
                artifact_id=artifact_id,
                content_sha256=manifest_blob_id,
                content_location=manifest_blob_location(manifest_blob_id),
                content_type="json",
                byte_size=int(manifest.get("byte_size") or 0),
                metadata={
                    "lakehouse": True,
                    "data_object_id": oid,
                    "manifest_content_root": content_sha256,
                    "kind": manifest.get("kind"),
                    "published_from_session": session_id,
                },
            )
            fields = derive_entry_from_manifest(
                manifest,
                owner_type="project",
                owner_id=project_id,
                content_sha256=manifest_blob_id,
                byte_size=int(manifest.get("byte_size") or 0),
                ref=oid,
                tags=tags,
            )
            if manifest.get("kind") == "virtual":
                fields["tags_json"] = list(
                    dict.fromkeys([*(tags or []), "virtual"])
                )
            upsert_catalog_entry(db, fields)
            db.commit()
            published.append({
                "object_id": oid,
                "artifact_id": artifact_id,
                "revision_no": int(
                    getattr(revision, "revision_no", 1) or 1
                ),
                "revision_created": bool(created),
                "deduped": not created,
            })
        return {
            "published": published,
            "unknown": sorted(set(unknown)),
            "forbidden": sorted(set(forbidden)),
        }


def manifest_blob_location(manifest_blob_id: str) -> str:
    """manifest blob 的后端无关 location（与 BlobStore.location 同布局）。"""
    digest = str(manifest_blob_id)
    return f"{digest[:4]}/{digest}.json"


def _find_or_create_artifact(
    db: Any, *, project_id: str, oid: str, manifest: Mapping[str, Any]
) -> str:
    from sqlalchemy import select

    from app.models.project import Artifact

    existing = (
        db.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.storage_ref == oid,
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing.id
    artifact = Artifact(
        id=f"art_lh_{uuid.uuid4().hex[:16]}",
        project_id=project_id,
        name=str((manifest.get("payload") or {}).get("title") or oid[:24]),
        artifact_type="lakehouse_object",
        storage_ref=oid,
        content_fingerprint=str(manifest.get("content_sha256") or "")[:64],
        metadata_json={
            "content_status": "promoted",
            "lakehouse": True,
            "kind": manifest.get("kind"),
        },
    )
    # 并发 find-or-create（评审 R1-10）：savepoint 内插入，撞唯一/冲突
    # 即回退 savepoint 重查（既有行胜出 —— 幂等语义）。
    nested = db.begin_nested()
    db.add(artifact)
    try:
        db.flush()
        nested.commit()
    except Exception:
        nested.rollback()
        existing = (
            db.execute(
                select(Artifact).where(
                    Artifact.project_id == project_id,
                    Artifact.storage_ref == oid,
                )
            )
        ).scalars().first()
        if existing is None:
            raise
        return existing.id
    return artifact.id


async def revoke_project_objects(
    db, *, project_id: str, object_ids: Sequence[str], actor_id: Optional[str],
) -> Dict[str, Any]:
    """撤销项目内的发布（catalog tombstone；owner 校验同发布）。"""
    from sqlalchemy import select

    from app.models.project import Project

    def _revoke_sync() -> Dict[str, Any]:
        from app.core.database import SessionLocal
        from app.services.lakehouse.catalog_service import revoke_catalog_entries

        with SessionLocal() as sync_db:
            project = (
                sync_db.execute(
                    select(Project).where(Project.id == project_id)
                )
            ).scalar_one_or_none()
            if project is None or (
                actor_id is not None
                and str(project.owner_id) != str(actor_id)
            ):
                raise PublishError(
                    "project not found",
                    code="LAKEHOUSE_PUBLISH_PROJECT_MISSING",
                )
            result = revoke_catalog_entries(
                sync_db, owner_type="project", owner_id=project_id,
                object_ids=object_ids,
            )
            sync_db.commit()
            return result

    return await asyncio.to_thread(_revoke_sync)


async def resolve_project_object(
    db, *, project_id: str, object_id: str, store: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """项目域解析（R0-9）：catalog ``status=active`` 行授权读取
    session 出身的 manifest。无授权行 → None（404 语义，不泄漏存在性）。"""
    from sqlalchemy import select

    from app.models.lakehouse_catalog import LakehouseCatalogItem

    def _resolve_sync() -> Optional[Dict[str, Any]]:
        from app.services.lakehouse.data_object import resolve_data_object

        with SessionLocal() as sync_db:
            row = (
                sync_db.execute(
                    select(LakehouseCatalogItem).where(
                        LakehouseCatalogItem.owner_type == "project",
                        LakehouseCatalogItem.owner_id == project_id,
                        LakehouseCatalogItem.object_id == object_id,
                        LakehouseCatalogItem.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            manifest = None
            did = str(row.object_id)
            candidates = [did]
            if did.startswith("ref:cube/"):
                # ref cursor → 同内容Sha 的 64-hex manifest id 兜底。
                candidates.append(str(row.content_sha256))
            for cand in candidates:
                if len(cand) == 64:
                    manifest = resolve_data_object(cand, store=store)
                    if manifest is not None:
                        break
            return {"manifest": manifest, "catalog": row.to_dict()}

    from app.core.database import SessionLocal

    return await asyncio.to_thread(_resolve_sync)
