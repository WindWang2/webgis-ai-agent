"""Workflow Runtime V5 —— WorkflowPackage durable registry（semver 发布态）。

事实源边界：**包内容的事实源是 V4 编译器** —— 注册入口在 service 层做
re-emit 比对指纹（不一致拒绝）；本表只管「哪些包存在、哪个版本已发布、
包↔owner 归属」。同 (package_id, version) 重注册：指纹相同 = 幂等成功；
指纹不同 = typed 冲突（防第二事实源漂移）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError

logger = logging.getLogger(__name__)

PKG_DRAFT = "draft"
PKG_PUBLISHED = "published"
PKG_DEPRECATED = "deprecated"


class PackageConflict(Exception):
    """同 (package_id, version) 指纹不一致（防包漂移红线）。"""

    def __init__(self, package_id: str, version: str,
                 fingerprint: str, existing: str):
        super().__init__(
            f"package {package_id}@{version} fingerprint mismatch: "
            f"new={fingerprint[:12]} existing={existing[:12]}")
        self.package_id = package_id
        self.version = version


def _default_session_factory():
    from app.core.database import SessionLocal

    return SessionLocal()


session_factory: Any = _default_session_factory


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "package_id": row.package_id,
        "version": row.version,
        "schema_version": row.schema_version,
        "compiler_version": row.compiler_version,
        "methodology_family": row.methodology_family,
        "recipe_fingerprint": row.recipe_fingerprint,
        "methodology_fingerprint": row.methodology_fingerprint,
        "environment_fingerprint": row.environment_fingerprint,
        "fingerprint": row.fingerprint,
        "status": row.status,
        "owner_scope": row.owner_scope,
        "project_id": row.project_id or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "published_at": row.published_at.isoformat() if row.published_at else "",
    }


class PackageRegistry:
    """包注册/发布/解析（同步 SQLAlchemy；异步调用方 to_thread 卸载）。"""

    def __init__(self, factory: Optional[Any] = None):
        self._factory = factory or session_factory

    def register(
        self, package: Any, *, owner_scope: str,
        environment_fingerprint: str = "", project_id: str = "",
    ) -> Dict[str, Any]:
        """注册（draft）；同 (id, version) 指纹相同幂等，不同抛冲突。"""
        from app.models.db_model import WorkflowPackageRow

        with self._factory() as db:
            existing = db.query(WorkflowPackageRow).filter(
                WorkflowPackageRow.package_id == package.package_id,
                WorkflowPackageRow.version == package.version,
            ).first()
            if existing is not None:
                if existing.fingerprint != package.fingerprint:
                    raise PackageConflict(
                        package.package_id, package.version,
                        package.fingerprint, existing.fingerprint)
                return _row_to_dict(existing)
            row = WorkflowPackageRow(
                package_id=package.package_id[:64],
                version=package.version[:16],
                schema_version=package.schema_version[:16],
                compiler_version=package.compiler_version[:16],
                methodology_family=(package.methodology_family or "")[:40],
                recipe_fingerprint=(package.recipe_fingerprint or "")[:64],
                methodology_fingerprint=(package.methodology_fingerprint or "")[:64],
                environment_fingerprint=(environment_fingerprint or "")[:64],
                compiled_form=package.to_bounded_dict()["compiled_form"],
                fingerprint=package.fingerprint[:64],
                status=PKG_DRAFT,
                owner_scope=owner_scope[:40],
                project_id=(project_id or "")[:255] or None,
                created_at=_utcnow(),
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                existing = db.query(WorkflowPackageRow).filter(
                    WorkflowPackageRow.package_id == package.package_id,
                    WorkflowPackageRow.version == package.version,
                ).first()
                if existing is not None and existing.fingerprint == package.fingerprint:
                    return _row_to_dict(existing)
                raise PackageConflict(
                    package.package_id, package.version, package.fingerprint,
                    existing.fingerprint if existing is not None else "?")
            return _row_to_dict(row)

    def publish(
        self, package_id: str, version: str, *, owner_scope: str,
    ) -> Optional[Dict[str, Any]]:
        """draft → published（幂等；已 published 原样返回）。"""
        from app.models.db_model import WorkflowPackageRow

        try:
            with self._factory() as db:
                updated = db.execute(
                    sa.update(WorkflowPackageRow)
                    .where(
                        WorkflowPackageRow.package_id == package_id,
                        WorkflowPackageRow.version == version,
                        WorkflowPackageRow.owner_scope == owner_scope,
                        WorkflowPackageRow.status.in_((PKG_DRAFT, PKG_PUBLISHED)),
                    )
                    .values(status=PKG_PUBLISHED, published_at=_utcnow())
                )
                db.commit()
                if updated.rowcount == 0:
                    return None
                row = db.query(WorkflowPackageRow).filter(
                    WorkflowPackageRow.package_id == package_id,
                    WorkflowPackageRow.version == version,
                ).first()
                return _row_to_dict(row) if row is not None else None
        except OperationalError:
            logger.warning("[WorkflowRuntime] publish db busy", exc_info=True)
            return None

    def resolve(
        self, package_id: str, *, owner_scope: str,
        version: str = "", require_published: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """解析包行（version 缺省 = 最新发布版 → 最新 draft）。"""
        from app.models.db_model import WorkflowPackageRow

        with self._factory() as db:
            q = db.query(WorkflowPackageRow).filter(
                WorkflowPackageRow.package_id == package_id,
                WorkflowPackageRow.owner_scope == owner_scope,
            )
            if version:
                q = q.filter(WorkflowPackageRow.version == version[:16])
            elif require_published:
                q = q.filter(WorkflowPackageRow.status == PKG_PUBLISHED)
            rows: List[Any] = q.all()
            if not rows:
                return None
            if version:
                row = rows[0]
            else:
                published = [r for r in rows if r.status == PKG_PUBLISHED]

                def _semver_key(r: Any) -> tuple:
                    from app.services.gis_harness.workflow_v4.package import (
                        parse_semver,
                    )

                    return parse_semver(r.version) or (0, 0, 0)

                pool = published or rows
                row = max(pool, key=lambda r: (_semver_key(r), r.created_at))
            out = _row_to_dict(row)
            out["compiled_form"] = dict(row.compiled_form or {})
            return out

    def get_by_fingerprint(
        self, fingerprint: str, *, owner_scope: str,
    ) -> Optional[Dict[str, Any]]:
        from app.models.db_model import WorkflowPackageRow

        with self._factory() as db:
            row = db.query(WorkflowPackageRow).filter(
                WorkflowPackageRow.fingerprint == fingerprint[:64],
                WorkflowPackageRow.owner_scope == owner_scope,
            ).first()
            if row is None:
                return None
            out = _row_to_dict(row)
            out["compiled_form"] = dict(row.compiled_form or {})
            return out

    def list_packages(
        self, *, owner_scope: str, limit: int = 32,
    ) -> List[Dict[str, Any]]:
        from app.models.db_model import WorkflowPackageRow

        with self._factory() as db:
            rows = db.query(WorkflowPackageRow).filter(
                WorkflowPackageRow.owner_scope == owner_scope,
            ).order_by(WorkflowPackageRow.created_at.desc()) \
                .limit(max(1, min(int(limit), 128))).all()
            return [_row_to_dict(r) for r in rows]

    def list_versions(
        self, package_id: str, *, owner_scope: str,
    ) -> List[Dict[str, Any]]:
        from app.models.db_model import WorkflowPackageRow

        with self._factory() as db:
            rows = db.query(WorkflowPackageRow).filter(
                WorkflowPackageRow.package_id == package_id,
                WorkflowPackageRow.owner_scope == owner_scope,
            ).all()

            def _key(r: Any) -> tuple:
                from app.services.gis_harness.workflow_v4.package import (
                    parse_semver,
                )

                return parse_semver(r.version) or (0, 0, 0)

            return [_row_to_dict(r) for r in sorted(rows, key=_key)]
