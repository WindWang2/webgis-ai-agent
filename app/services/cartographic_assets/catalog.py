"""G40 —— 制图符号资产目录服务（创建/版本/发布/退役/解析/引用检查）。

纪律照抄 ``templates/composition_assets.py``（同一套生命周期语义，不建
第二套）：draft → new_version → publish → retire（软退役：新绑定拒绝、
钉版本重放只读放行）→ restore（旧版内容造**新** revision 再发布）。

- **scope/租户**：private/org/project；不可见 = 404 不泄露存在性；写 =
  creator / 同 org admin（403）；匿名不可见任何 DB 资产（builtin 是代码
  定义的公共内容，无需 DB 可见性）。同内容去重（sha256 blob 键）不合并
  owner 权限 —— 元数据行按 owner 独立。
- **内容后端**：durable BlobStore（sha256 内容寻址、put-if-absent、原子
  发布）。版本行同时持有规范形 JSON（服务内确定性消费）与 blob 键
  （下载/回读校验）。版本行在场 = promotion GC 引用保护。
- **内容一致性**：写入时 digest 复算对账；读取时 digest 不符 = typed
  ``asset_content_mismatch``（S23 的服务端前置）。
- **删除**（S18）：硬删除版本前做引用检查（会话 MapSpec 绑定块扫描 +
  导出 manifest）；被引用 → ``asset_in_use`` 409（提示 retire）。
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, select

from app.lib.cartography.symbol_asset import (
    BUILTIN_ASSET_PREFIX,
    DB_ASSET_PREFIX,
    MAX_ASSET_JSON_BYTES,
    SymbolAssetDocument,
    compute_digest,
    get_builtin_asset,
)
from app.models.cartographic_symbol_asset import (
    CartographicSymbolAsset,
    CartographicSymbolAssetVersion,
)

logger = logging.getLogger(__name__)

#: 版本号并发冲突重试（0098 同款：savepoint 内重算）。
_MAX_REVISION_CREATE_ATTEMPTS = 3
#: 列表有界。
MAX_LIST_ASSETS = 50
#: S18 引用扫描的会话目录上限（截断如实披露）。
MAX_REFERENCE_SCAN_SESSIONS = 4000

#: 失败面 reason codes（测试锁；工具层原样转发）。
ASSET_REASON_CODES = (
    "asset_not_found",
    "asset_forbidden",
    "asset_draft",
    "asset_retired",
    "asset_revision_not_found",
    "asset_version_not_published",
    "asset_kind_mismatch",
    "asset_content_mismatch",
    "asset_in_use",
    "asset_conflict",
    "asset_invalid_visibility",
    "asset_document_error",
    "asset_reference_scan_truncated",
)


class CartographicAssetError(Exception):
    """资产生命周期操作非法（reason code 即 message 前缀；路由层映射 4xx）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


class CartographicAssetForbiddenError(CartographicAssetError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("asset_forbidden", detail)


# ── 作用域（纪律同 composition_assets：org 命中不暴露 private） ───────────


def _caller_project_ids(db: Any, user_id: Optional[str], org_id: Any) -> List[str]:
    if user_id is None:
        return []
    from app.models.project import Project
    clauses = [Project.owner_id == user_id]
    if org_id is not None:
        clauses.append(Project.org_id == org_id)
    return [str(r) for r in db.execute(
        select(Project.id).where(or_(*clauses))).scalars().all()]


def asset_scope_clause(user_id: Optional[str], org_id: Any, role: Optional[str],
                       project_ids: Optional[List[str]] = None) -> Any:
    from sqlalchemy import false as sa_false

    if role == "admin":
        admin_clauses = []
        if user_id is not None:
            admin_clauses.append(CartographicSymbolAsset.creator_id == user_id)
        if org_id is not None:
            admin_clauses.append(CartographicSymbolAsset.org_id == org_id)
        if not admin_clauses:
            return sa_false()
        return or_(*admin_clauses)
    if user_id is None:
        return sa_false()
    clauses = [CartographicSymbolAsset.creator_id == user_id]
    if org_id is not None:
        clauses.append(
            (CartographicSymbolAsset.visibility == "org")
            & (CartographicSymbolAsset.org_id == org_id))
    if project_ids:
        clauses.append(
            (CartographicSymbolAsset.visibility == "project")
            & CartographicSymbolAsset.project_id.in_(project_ids))
    return or_(*clauses)


def asset_visible(asset: CartographicSymbolAsset, user_id: Optional[str],
                  org_id: Any, role: Optional[str] = None,
                  project_ids: Optional[List[str]] = None) -> bool:
    if role == "admin":
        if (user_id is not None and asset.creator_id is not None
                and str(asset.creator_id) == str(user_id)):
            return True
        return org_id is not None and asset.org_id == org_id
    if user_id is None:
        return False
    if asset.creator_id is not None and str(asset.creator_id) == str(user_id):
        return True
    if (asset.visibility == "org" and org_id is not None
            and asset.org_id == org_id):
        return True
    if (asset.visibility == "project" and project_ids
            and str(asset.project_id or "") in set(project_ids)):
        return True
    return False


def can_write_asset(asset: CartographicSymbolAsset, user_id: Optional[str],
                    role: Optional[str] = None, org_id: Any = None) -> bool:
    if role == "admin":
        if org_id is not None and asset.org_id == org_id:
            return True
        return (user_id is not None and asset.creator_id is not None
                and str(asset.creator_id) == str(user_id))
    return user_id is not None and asset.creator_id is not None \
        and str(asset.creator_id) == str(user_id)


def _get_asset_or_404(db: Any, asset_id: str, user_id: Optional[str],
                      org_id: Any, role: Optional[str],
                      project_ids: Optional[List[str]] = None,
                      ) -> CartographicSymbolAsset:
    asset = db.get(CartographicSymbolAsset, asset_id)
    if asset is None or not asset_visible(
            asset, user_id, org_id, role, project_ids):
        raise CartographicAssetError("asset_not_found", asset_id[:64])
    return asset


def _require_writable(db: Any, asset_id: str, user_id: Optional[str],
                      org_id: Any = None, role: Optional[str] = None,
                      ) -> CartographicSymbolAsset:
    """404（不可见/不存在）优先于 403（可见不可写）—— 不泄露存在性。"""
    project_ids = _caller_project_ids(db, user_id, org_id)
    asset = _get_asset_or_404(db, asset_id, user_id, org_id, role, project_ids)
    if not can_write_asset(asset, user_id, role, org_id=org_id):
        raise CartographicAssetForbiddenError(asset_id[:64])
    return asset


# ── 内容后端（BlobStore；版本行持有键即受 promotion GC 保护） ─────────────


def _content_store():
    from app.services.durable_blob_store import get_filesystem_blob_store

    return get_filesystem_blob_store()


def _put_document_blob(doc: SymbolAssetDocument) -> Tuple[str, str]:
    """规范形 canonical JSON → BlobStore CAS。返回 (blob_key, digest)。"""
    from app.services.durable_blob_store import sha256_of_bytes

    blob = doc.model_dump_json(exclude={"content_digest"}).encode("utf-8")
    key = sha256_of_bytes(blob)
    _content_store().put_blob(key, blob, "json")
    return key, compute_digest(doc)


def _verify_document(doc: SymbolAssetDocument, *, expected_digest: str) -> None:
    digest = compute_digest(doc)
    if digest != expected_digest:
        raise CartographicAssetError(
            "asset_content_mismatch",
            f"期望 {expected_digest[:24]} 实得 {digest[:24]}（拒绝顶替内容）")


def _load_document(row: CartographicSymbolAssetVersion) -> SymbolAssetDocument:
    """版本行 → 规范形（digest 对账；损坏/不符 = typed 失败，不默认圆）。"""
    try:
        doc = SymbolAssetDocument.model_validate(dict(row.document or {}))
    except Exception as exc:  # pydantic ValidationError / 形状漂移
        raise CartographicAssetError(
            "asset_document_error", f"版本 {row.revision} 规范形损坏: {exc}") from exc
    _verify_document(doc, expected_digest=str(row.content_digest or ""))
    return doc


# ── 生命周期 ───────────────────────────────────────────────────────────────


def create_asset(
    db: Any, *, document: SymbolAssetDocument, visibility: str = "private",
    project_id: Optional[str] = None, creator_id: Optional[str],
    org_id: Any = None, description: str = "", publish: bool = False,
) -> Tuple[CartographicSymbolAsset, CartographicSymbolAssetVersion]:
    """创建资产 + 首版（可选即时发布）。同内容按 owner 各自成行（权限独立）。"""
    if visibility not in ("private", "org", "project"):
        raise CartographicAssetError("asset_invalid_visibility", visibility)
    if visibility == "project":
        if not project_id:
            raise CartographicAssetError(
                "asset_invalid_visibility", "project requires project_id")
        from app.models.project import Project
        project = db.get(Project, project_id)
        if project is None:
            raise CartographicAssetError(
                "asset_invalid_visibility", f"project {project_id} not found")
        if not _project_accessible(project, creator_id, org_id):
            raise CartographicAssetForbiddenError(
                f"project {project_id} not accessible to creator")
    elif project_id:
        raise CartographicAssetError(
            "asset_invalid_visibility",
            "project_id only allowed for visibility=project")
    if creator_id is None:
        raise CartographicAssetForbiddenError("anonymous cannot create assets")

    asset_id = f"{DB_ASSET_PREFIX}{uuid.uuid4().hex}"
    blob_key, digest = _put_document_blob(document)
    asset = CartographicSymbolAsset(
        id=asset_id, org_id=org_id, creator_id=creator_id,
        visibility=visibility, project_id=project_id if visibility == "project" else None,
        name=(document.name or asset_id[-8:])[:255],
        description=description[:2000], kind=document.kind,
        status="draft", latest_revision=0, current_revision=0)
    db.add(asset)
    try:
        db.flush()
    except Exception as exc:
        db.rollback()
        raise CartographicAssetError("asset_conflict", str(exc)[:160]) from exc
    version = _insert_version(
        db, asset, document=document, blob_key=blob_key, digest=digest,
        created_by=creator_id)
    if publish:
        _publish_version_row(db, asset, version)
    return asset, version


def _project_accessible(project: Any, user_id: Optional[str], org_id: Any) -> bool:
    if user_id is None:
        return False
    if project.owner_id is not None and str(project.owner_id) == str(user_id):
        return True
    return org_id is not None and project.org_id == org_id


def _insert_version(
    db: Any, asset: CartographicSymbolAsset, *, document: SymbolAssetDocument,
    blob_key: str, digest: str, created_by: Optional[str],
) -> CartographicSymbolAssetVersion:
    """追加不可变版本（max+1 重试；kind 与资产一致）。"""
    if document.kind != asset.kind:
        raise CartographicAssetError(
            "asset_kind_mismatch", f"{document.kind} != {asset.kind}")
    payload = document.model_dump_json()
    if len(payload.encode("utf-8")) > MAX_ASSET_JSON_BYTES:
        raise CartographicAssetError(
            "asset_document_error", "规范形超字节预算")
    from app.lib.cartography.symbol_asset import validate_digest as _vd
    if not _vd(document):
        raise CartographicAssetError(
            "asset_document_error", "content_digest 与内容不符（写入拒绝）")
    for attempt in range(_MAX_REVISION_CREATE_ATTEMPTS):
        try:
            latest = db.execute(
                select(CartographicSymbolAssetVersion.revision)
                .where(CartographicSymbolAssetVersion.asset_id == asset.id)
                .order_by(CartographicSymbolAssetVersion.revision.desc())
                .limit(1)).scalar() or 0
            version = CartographicSymbolAssetVersion(
                asset_id=asset.id, revision=int(latest) + 1,
                document=json.loads(payload), status="draft",
                content_digest=digest, blob_key=blob_key,
                source_note=json.dumps(document.source, ensure_ascii=False)[:1000],
                created_by=created_by)
            db.add(version)
            asset.latest_revision = version.revision
            db.flush()
            return version
        except Exception:
            db.rollback()
            if attempt == _MAX_REVISION_CREATE_ATTEMPTS - 1:
                raise
            logger.warning("[carto_assets] version insert retry %d", attempt)
    raise CartographicAssetError("asset_conflict", "unreachable")


def _publish_version_row(db: Any, asset: CartographicSymbolAsset,
                         version: CartographicSymbolAssetVersion) -> None:
    version.status = "published"
    asset.status = "published"
    asset.current_revision = version.revision
    db.flush()


def new_version(
    db: Any, asset_id: str, *, document: SymbolAssetDocument,
    user_id: Optional[str], org_id: Any = None, role: Optional[str] = None,
    publish: bool = False,
) -> CartographicSymbolAssetVersion:
    asset = _require_writable(db, asset_id, user_id, org_id, role)
    if asset.status == "retired":
        raise CartographicAssetError(
            "asset_retired", "retired 资产需先 restore 再加版本")
    blob_key, digest = _put_document_blob(document)
    version = _insert_version(
        db, asset, document=document, blob_key=blob_key, digest=digest,
        created_by=user_id)
    if publish:
        _publish_version_row(db, asset, version)
    return version


def publish_version(
    db: Any, asset_id: str, revision: int, *, user_id: Optional[str],
    org_id: Any = None, role: Optional[str] = None,
) -> CartographicSymbolAssetVersion:
    asset = _require_writable(db, asset_id, user_id, org_id, role)
    version = db.query(CartographicSymbolAssetVersion).filter_by(
        asset_id=asset_id, revision=int(revision)).one_or_none()
    if version is None:
        raise CartographicAssetError("asset_revision_not_found", str(revision))
    if version.status == "retired":
        raise CartographicAssetError("asset_retired", f"版本 {revision} 已退役")
    _verify_document(_load_document(version), expected_digest=version.content_digest)
    _publish_version_row(db, asset, version)
    return version


def retire_asset(
    db: Any, asset_id: str, *, user_id: Optional[str], org_id: Any = None,
    role: Optional[str] = None, note: str = "",
) -> CartographicSymbolAsset:
    """软退役：新绑定/新版本拒绝；已发布版本钉 revision 重放只读放行。"""
    asset = _require_writable(db, asset_id, user_id, org_id, role)
    asset.status = "retired"
    now = datetime.now(timezone.utc)
    for version in db.query(CartographicSymbolAssetVersion).filter_by(
            asset_id=asset_id).all():
        if version.status == "published":
            version.status = "retired"
            version.retired_at = now
            version.retired_note = note[:500]
    db.flush()
    return asset


def restore_asset(
    db: Any, asset_id: str, from_revision: int, *, user_id: Optional[str],
    org_id: Any = None, role: Optional[str] = None,
) -> CartographicSymbolAssetVersion:
    """旧版内容造**新** revision 再发布（不重写历史）。"""
    asset = _require_writable(db, asset_id, user_id, org_id, role)
    src = db.query(CartographicSymbolAssetVersion).filter_by(
        asset_id=asset_id, revision=int(from_revision)).one_or_none()
    if src is None:
        raise CartographicAssetError(
            "asset_revision_not_found", str(from_revision))
    doc = _load_document(src)
    asset.status = "draft" if asset.status == "retired" else asset.status
    blob_key, digest = _put_document_blob(doc)
    version = _insert_version(
        db, asset, document=doc, blob_key=blob_key, digest=digest,
        created_by=user_id)
    _publish_version_row(db, asset, version)
    return version


# ── 解析（builtin / DB；钉版本 + digest 对账） ────────────────────────────


def resolve_version_document(
    db: Any, asset_id: str, revision: int, *, expected_digest: Optional[str] = None,
    user_id: Optional[str] = None, org_id: Any = None, role: Optional[str] = None,
    for_apply: bool = True,
) -> Tuple[SymbolAssetDocument, Dict[str, Any]]:
    """解析钉版本规范形。builtin 走代码定义（revision=1）。

    ``expected_digest`` 给出时做内容对账（绑定块的 content_digest pin）。
    ``for_apply=True``：draft/retired 版本拒绝（新绑定只吃 published）；
    ``for_apply=False``（重放/导出）：retired 放行（旧图可开），draft 仍拒。
    scope 检查恒在（content hash 不是授权）。
    """
    asset_id = str(asset_id or "")
    revision = int(revision)
    if asset_id.startswith(BUILTIN_ASSET_PREFIX):
        doc = get_builtin_asset(asset_id)
        if doc is None:
            raise CartographicAssetError("asset_not_found", asset_id)
        if revision != 1:
            raise CartographicAssetError(
                "asset_revision_not_found", "builtin 只有 revision=1")
        if expected_digest is not None and doc.content_digest != expected_digest:
            raise CartographicAssetError(
                "asset_content_mismatch",
                f"builtin {asset_id} digest pin 不符（目录内容已变化？）")
        meta = {
            "asset_id": asset_id, "revision": 1, "kind": doc.kind,
            "name": doc.name, "builtin": True,
            "content_digest": doc.content_digest, "status": "published",
        }
        return doc, meta
    project_ids = _caller_project_ids(db, user_id, org_id)
    asset = _get_asset_or_404(db, asset_id, user_id, org_id, role, project_ids)
    version = db.query(CartographicSymbolAssetVersion).filter_by(
        asset_id=asset_id, revision=revision).one_or_none()
    if version is None:
        raise CartographicAssetError("asset_revision_not_found", str(revision))
    if version.status == "draft":
        raise CartographicAssetError("asset_version_not_published", str(revision))
    if for_apply and version.status == "retired":
        raise CartographicAssetError(
            "asset_retired", f"版本 {revision} 已退役（新绑定拒绝；重放只读放行）")
    if expected_digest is not None and version.content_digest != expected_digest:
        raise CartographicAssetError(
            "asset_content_mismatch",
            f"{asset_id}@{revision} digest pin 不符（拒绝静默换内容）")
    doc = _load_document(version)
    meta = {
        "asset_id": asset_id, "revision": revision, "kind": asset.kind,
        "name": asset.name, "builtin": False,
        "content_digest": version.content_digest, "status": version.status,
    }
    return doc, meta


def read_version_blob(
    db: Any, asset_id: str, revision: int, *, user_id: Optional[str] = None,
    org_id: Any = None, role: Optional[str] = None,
) -> Tuple[bytes, Dict[str, Any]]:
    """受权读取版本内容字节（下载/回读校验面；scope 检查恒在）。"""
    doc, meta = resolve_version_document(
        db, asset_id, revision, user_id=user_id, org_id=org_id, role=role,
        for_apply=False)
    if meta.get("builtin"):
        blob = doc.model_dump_json().encode("utf-8")
    else:
        version = db.query(CartographicSymbolAssetVersion).filter_by(
            asset_id=asset_id, revision=revision).one_or_none()
        blob = _content_store().get_blob(str(version.blob_key))
        if blob is None:
            raise CartographicAssetError(
                "asset_content_mismatch",
                f"blob {str(version.blob_key)[:16]} 不可读（内容缺失）")
        if len(blob) > MAX_ASSET_JSON_BYTES:
            raise CartographicAssetError(
                "asset_document_error", "blob 超字节预算")
    return blob, meta


# ── 列表 ───────────────────────────────────────────────────────────────────


def list_assets(
    db: Any, *, user_id: Optional[str], org_id: Any, role: Optional[str] = None,
    kind: Optional[str] = None, include_retired: bool = False,
    limit: int = MAX_LIST_ASSETS, offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """调用者可见 DB 资产（kind 过滤 + 分页；返回 (页, 总数有界计数)）。"""
    project_ids = _caller_project_ids(db, user_id, org_id)
    scope = asset_scope_clause(user_id, org_id, role, project_ids)
    stmt = select(CartographicSymbolAsset, CartographicSymbolAssetVersion).outerjoin(
        CartographicSymbolAssetVersion,
        (CartographicSymbolAssetVersion.asset_id == CartographicSymbolAsset.id)
        & (CartographicSymbolAssetVersion.revision
           == CartographicSymbolAsset.latest_revision))
    if scope is not None:
        stmt = stmt.where(scope)
    if not include_retired:
        stmt = stmt.where(CartographicSymbolAsset.status != "retired")
    if kind:
        stmt = stmt.where(CartographicSymbolAsset.kind == kind)
    rows = db.execute(stmt.order_by(
        CartographicSymbolAsset.updated_at.desc(),
        CartographicSymbolAsset.id)).all()
    total = len(rows)
    page_limit = max(1, min(int(limit), MAX_LIST_ASSETS))
    page = rows[max(0, int(offset)):max(0, int(offset)) + page_limit]
    out: List[Dict[str, Any]] = []
    for asset, version in page:
        out.append({
            "asset_id": asset.id,
            "name": asset.name,
            "description": (asset.description or "")[:160],
            "kind": asset.kind,
            "visibility": asset.visibility,
            "project_id": asset.project_id,
            "status": asset.status,
            "latest_revision": asset.latest_revision,
            "current_revision": asset.current_revision,
            "content_digest": (version.content_digest or "") if version else "",
            "builtin": False,
            "created_at": asset.created_at.isoformat() if asset.created_at else None,
            "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
        })
    return out, total


def list_versions(db: Any, asset_id: str, *, user_id: Optional[str],
                  org_id: Any, role: Optional[str] = None) -> List[Dict[str, Any]]:
    project_ids = _caller_project_ids(db, user_id, org_id)
    _get_asset_or_404(db, asset_id, user_id, org_id, role, project_ids)
    rows = (
        db.query(CartographicSymbolAssetVersion)
        .filter_by(asset_id=asset_id)
        .order_by(CartographicSymbolAssetVersion.revision.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "revision": r.revision,
            "status": r.status,
            "content_digest": r.content_digest,
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "retired_at": r.retired_at.isoformat() if r.retired_at else None,
        }
        for r in rows
    ]


# ── 引用检查与删除（S18） ──────────────────────────────────────────────────


def find_asset_references(asset_id: str, revision: Optional[int] = None,
                          *, storage_root: Optional[Path] = None,
                          max_sessions: int = MAX_REFERENCE_SCAN_SESSIONS,
                          ) -> Dict[str, Any]:
    """扫描会话 MapSpec 的 symbol_asset 绑定引用（有界 + 截断披露）。

    只读磁盘上的 mapspec.json（后端权威）；Redis-only 未落盘会话不在扫描
    面 —— 删除是显式低频操作，磁盘权威覆盖已保存会话（S21 重开后仍在的
    面）；截断时如实标注 ``truncated``。
    """
    from app.core.config import settings

    base = Path(storage_root or os.environ.get("MAPSPEC_STORAGE_DIR")
                or Path(settings.DATA_DIR))
    refs: List[Dict[str, Any]] = []
    scanned = 0
    truncated = False
    if base.is_dir():
        for session_dir in sorted(base.iterdir()):
            if not session_dir.is_dir():
                continue
            if scanned >= max_sessions:
                truncated = True
                break
            scanned += 1
            spec_path = session_dir / "mapspec.json"
            if not spec_path.is_file():
                continue
            try:
                data = json.loads(spec_path.read_text("utf-8"))
            except Exception:
                continue
            for layer in (data or {}).get("layers") or []:
                binding = layer.get("symbol_asset") if isinstance(layer, dict) else None
                if not isinstance(binding, dict):
                    continue
                if binding.get("asset_id") != asset_id:
                    continue
                if revision is not None and binding.get("revision") != revision:
                    continue
                refs.append({"session_id": session_dir.name,
                             "layer_id": str(layer.get("id") or "")})
                if len(refs) >= 64:
                    return {"refs": refs, "scanned_sessions": scanned,
                            "truncated": True}
    return {"refs": refs, "scanned_sessions": scanned, "truncated": truncated}


def delete_asset_version(
    db: Any, asset_id: str, revision: int, *, user_id: Optional[str],
    org_id: Any = None, role: Optional[str] = None,
) -> Dict[str, Any]:
    """硬删除单个版本行（blob 内容寻址保留 —— 可能被同内容资产共享）。

    被引用（会话绑定 / 版本仍是 current_revision）→ ``asset_in_use`` 409。
    其余版本不受影响；既有成果的 manifest 仍可回读（retired 路径优先）。
    """
    asset = _require_writable(db, asset_id, user_id, org_id, role)
    version = db.query(CartographicSymbolAssetVersion).filter_by(
        asset_id=asset_id, revision=int(revision)).one_or_none()
    if version is None:
        raise CartographicAssetError("asset_revision_not_found", str(revision))
    if asset.current_revision == version.revision:
        raise CartographicAssetError(
            "asset_in_use", f"revision {revision} 是当前发布版本（请先改发布或 retire）")
    scan = find_asset_references(asset_id, revision)
    if scan["refs"]:
        raise CartographicAssetError(
            "asset_in_use",
            f"被 {len(scan['refs'])} 个会话图层引用（如 {scan['refs'][0]['session_id']}）"
            "；请改用 retire（钉版本重放仍可读）")
    db.delete(version)
    db.flush()
    return {
        "deleted": True, "asset_id": asset_id, "revision": revision,
        "reference_scan": {
            "scanned_sessions": scan["scanned_sessions"],
            "truncated": scan["truncated"],
        },
    }


# ── 出口链资产收集（冻结 pin；S27 出版锁版本） ────────────────────────────

def collect_symbol_asset_documents(
    db: Any, mapspec: Optional[Dict[str, Any]], *,
    user_id: Optional[str] = None, org_id: Any = None, role: Optional[str] = None,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    """扫描 MapSpec 绑定块（含类别规则/other 资产）→ {digest: 规范形 dict}。

    解析失败（scope 不可见/版本退役/digest 不符）**不阻断导出**：条目进
    failures，编译面发 ``symbol_asset_unavailable`` 诊断（不画、不伪装）。
    成功条目按 content_digest 去重 —— 同资产多绑定只解析一次（S25）。
    """
    docs: Dict[str, Dict[str, Any]] = {}
    failures: List[Dict[str, str]] = []

    def _try(asset_id: Any, revision: Any, digest: Any) -> None:
        asset_id = str(asset_id or "")
        try:
            revision_i = int(revision or 0)
        except (TypeError, ValueError):
            revision_i = 0
        digest_s = str(digest or "")
        if not asset_id or revision_i < 1 or not digest_s:
            failures.append({"asset_id": asset_id, "reason": "invalid_ref"})
            return
        if digest_s in docs or any(f.get("asset_id") == asset_id
                                   and f.get("reason") != "invalid_ref"
                                   for f in failures):
            return
        try:
            doc, _meta = resolve_version_document(
                db, asset_id, revision_i, expected_digest=digest_s,
                user_id=user_id, org_id=org_id, role=role, for_apply=False)
            docs[doc.content_digest] = doc.model_dump()
        except CartographicAssetError as exc:
            failures.append({"asset_id": asset_id, "reason": exc.reason})

    if not isinstance(mapspec, dict):
        return docs, failures
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        binding = layer.get("symbol_asset")
        if not isinstance(binding, dict):
            continue
        _try(binding.get("asset_id"), binding.get("revision"),
             binding.get("content_digest"))
        cb = binding.get("category_binding")
        if isinstance(cb, dict):
            for rule in cb.get("map") or []:
                if isinstance(rule, dict):
                    _try(rule.get("asset_id"), rule.get("revision"),
                         rule.get("content_digest"))
            if str(cb.get("other_mode") or "") == "fallback_asset":
                _try(cb.get("other_asset"), cb.get("other_revision"),
                     cb.get("other_content_digest"))
    return docs, failures
