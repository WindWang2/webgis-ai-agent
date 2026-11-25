"""模型草稿持久层（CAS 修订；project-scoped；不复制执行态）。

并发模型：``revision_no`` 乐观锁 —— 更新/删除/发布指针写入必须携带
``expected_revision``，0 行更新 = 他人先写 → ``REVISION_CONFLICT``（调用
方重读重试）。owner 隔离经 project 归属（路由层先过项目鉴权，本层按
project_id 过滤；不信任调用方转述的 owner）。

边界：本层不理解文档语义（validate/compile 的职责），只做有界校验
（文档字节数）与 CAS；发布仅记指针（INV-MA1）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import sqlalchemy as sa

from app.models.model_authoring import ModelDraft

logger = logging.getLogger(__name__)

#: 单份草稿文档字节上限（canonical JSON；超界拒绝——模型是声明不是数据）。
MAX_DOCUMENT_BYTES = 256 * 1024
#: 列表单页上限（防全量拖库）。
MAX_LIST_LIMIT = 100

_SLUG_ERR = "model_slug must match ^[a-z][a-z0-9_]{2,40}$"


class ModelStoreError(Exception):
    """草稿存储 typed 错误（code 稳定词表；HTTP/Pi 面直接映射）。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _default_session_factory():
    from app.core.database import SessionLocal

    return SessionLocal()


def _slug_ok(slug: str) -> bool:
    import re

    return bool(re.fullmatch(r"[a-z][a-z0-9_]{2,40}", slug or ""))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: ModelDraft) -> Dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "model_slug": row.model_slug,
        "name": row.name,
        "description": row.description or "",
        "document": dict(row.document or {}),
        "revision_no": int(row.revision_no),
        "document_fingerprint": row.document_fingerprint,
        "source_kind": row.source_kind,
        "source_ref": row.source_ref or "",
        "template_id": row.template_id or "",
        "latest_published_package_id": row.latest_published_package_id or "",
        "latest_published_version": row.latest_published_version or "",
        "created_by": row.created_by or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


class ModelDraftStore:
    """草稿 CRUD + CAS（同步 SQLAlchemy；工厂可注入便于测试）。"""

    def __init__(self, session_factory: Optional[Callable[[], Any]] = None):
        self._factory = session_factory or _default_session_factory

    def create_draft(
        self, *, project_id: str, model_slug: str, document: Dict[str, Any],
        document_fingerprint: str, name: str, description: str = "",
        source_kind: str = "manual", source_ref: str = "",
        template_id: str = "", created_by: str = "",
    ) -> Dict[str, Any]:
        if not _slug_ok(model_slug):
            raise ModelStoreError("INVALID_SLUG", _SLUG_ERR)
        import json as _json

        doc_bytes = len(_json.dumps(document, ensure_ascii=False, default=str))
        if doc_bytes > MAX_DOCUMENT_BYTES:
            raise ModelStoreError(
                "DOCUMENT_TOO_LARGE",
                f"{doc_bytes} bytes exceeds {MAX_DOCUMENT_BYTES}")
        with self._factory() as db:
            existing = db.execute(
                sa.select(ModelDraft).where(
                    ModelDraft.project_id == project_id,
                    ModelDraft.model_slug == model_slug,
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ModelStoreError(
                    "DRAFT_EXISTS",
                    f"model_slug {model_slug!r} already exists in project")
            row = ModelDraft(
                id=str(uuid.uuid4()),
                project_id=project_id,
                model_slug=model_slug,
                name=name[:120],
                description=description[:2000],
                document=document,
                revision_no=1,
                document_fingerprint=document_fingerprint[:64],
                source_kind=source_kind[:20],
                source_ref=source_ref[:255] or None,
                template_id=template_id[:120] or None,
                created_by=created_by[:255] or None,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            db.add(row)
            db.commit()
            return _row_to_dict(row)

    def get_draft(self, project_id: str, model_slug: str) -> Optional[Dict[str, Any]]:
        with self._factory() as db:
            row = db.execute(
                sa.select(ModelDraft).where(
                    ModelDraft.project_id == project_id,
                    ModelDraft.model_slug == model_slug,
                )
            ).scalar_one_or_none()
            return _row_to_dict(row) if row is not None else None

    def list_drafts(
        self, project_id: str, *, limit: int = 50, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        with self._factory() as db:
            rows = db.execute(
                sa.select(ModelDraft)
                .where(ModelDraft.project_id == project_id)
                .order_by(ModelDraft.updated_at.desc())
                .offset(max(0, int(offset)))
                .limit(max(1, min(int(limit), MAX_LIST_LIMIT)))
            ).scalars().all()
            return [_row_to_dict(r) for r in rows]

    def update_document(
        self, *, project_id: str, model_slug: str, document: Dict[str, Any],
        document_fingerprint: str, name: Optional[str] = None,
        description: Optional[str] = None, expected_revision: int,
    ) -> Dict[str, Any]:
        """CAS 文档更新；``expected_revision`` 不符 → REVISION_CONFLICT。"""
        import json as _json

        doc_bytes = len(_json.dumps(document, ensure_ascii=False, default=str))
        if doc_bytes > MAX_DOCUMENT_BYTES:
            raise ModelStoreError(
                "DOCUMENT_TOO_LARGE",
                f"{doc_bytes} bytes exceeds {MAX_DOCUMENT_BYTES}")
        with self._factory() as db:
            values: Dict[str, Any] = {
                "document": document,
                "document_fingerprint": document_fingerprint[:64],
                "revision_no": int(expected_revision) + 1,
                "updated_at": _utcnow(),
            }
            if name is not None:
                values["name"] = name[:120]
            if description is not None:
                values["description"] = description[:2000]
            updated = db.execute(
                sa.update(ModelDraft)
                .where(
                    ModelDraft.project_id == project_id,
                    ModelDraft.model_slug == model_slug,
                    ModelDraft.revision_no == int(expected_revision),
                )
                .values(**values)
            )
            db.commit()
            if updated.rowcount == 0:
                raise ModelStoreError(
                    "REVISION_CONFLICT",
                    f"expected_revision={expected_revision} no longer current")
            row = db.execute(
                sa.select(ModelDraft).where(
                    ModelDraft.project_id == project_id,
                    ModelDraft.model_slug == model_slug,
                )
            ).scalar_one()
            return _row_to_dict(row)

    def record_publication(
        self, *, project_id: str, model_slug: str, package_id: str,
        version: str, expected_revision: int,
    ) -> Dict[str, Any]:
        """发布指针写入（CAS；INV-MA1 —— 只记指针不复制包）。"""
        with self._factory() as db:
            updated = db.execute(
                sa.update(ModelDraft)
                .where(
                    ModelDraft.project_id == project_id,
                    ModelDraft.model_slug == model_slug,
                    ModelDraft.revision_no == int(expected_revision),
                )
                .values(
                    latest_published_package_id=package_id[:64],
                    latest_published_version=version[:16],
                    revision_no=int(expected_revision) + 1,
                    updated_at=_utcnow(),
                )
            )
            db.commit()
            if updated.rowcount == 0:
                raise ModelStoreError(
                    "REVISION_CONFLICT",
                    f"expected_revision={expected_revision} no longer current")
            row = db.execute(
                sa.select(ModelDraft).where(
                    ModelDraft.project_id == project_id,
                    ModelDraft.model_slug == model_slug,
                )
            ).scalar_one()
            return _row_to_dict(row)

    def delete_draft(
        self, *, project_id: str, model_slug: str, expected_revision: int,
    ) -> bool:
        """CAS 删除（已发布指针不阻删——包不可变，历史版本仍可解析）。"""
        with self._factory() as db:
            updated = db.execute(
                sa.delete(ModelDraft).where(
                    ModelDraft.project_id == project_id,
                    ModelDraft.model_slug == model_slug,
                    ModelDraft.revision_no == int(expected_revision),
                )
            )
            db.commit()
            if updated.rowcount == 0:
                raise ModelStoreError(
                    "REVISION_CONFLICT",
                    f"expected_revision={expected_revision} no longer current")
            return True


__all__ = [
    "ModelDraftStore",
    "ModelStoreError",
    "MAX_DOCUMENT_BYTES",
    "MAX_LIST_LIMIT",
]
