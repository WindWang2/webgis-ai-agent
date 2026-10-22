"""G04 —— 组合模板资产生命周期服务（storage face of ADR-0214 留白）。

职责（全部同步 DB；路由/工具层负责线程化与身份）：

- **生命周期状态机**：create（draft）→ new_version（draft 快照，savepoint
  max+1 重试）→ publish（发布门 = 物化 + 单模板校验零 issue；asset.status=
  published，current_revision=max）→ retire（软退役：新 apply 拒绝，**钉
  revision 重放只读放行**——旧图可安全打开/导出）→ restore（旧版 payload
  造**新** revision 再发布；不重写历史）。
- **作用域/租户**：private（creator）/ org / project（项目属主或同 org
  项目）。admin 全见；匿名不可见任何 DB 资产（builtin 资产是代码定义的
  公共内容，无需 DB 可见性）。纪律照抄 ``templates/scope.py``：绝不把
  org_id IS NULL 当 public；不可见 = 404 不泄露存在性。
- **解析**：``resolve_asset_for``（for_apply / for_replay 两态）→ lib 层
  ``TemplateAssetRecord`` + 物化模板 + 派生契约。**注册表纪律**：组合模板
  注册表是进程级无鉴权结构 —— 只有 builtin（公共命名空间）资产 upsert 进
  去；DB 资产**永不**全局注册（跨租户元数据泄漏），apply 走显式钉版本
  模板参数，发现/列表走按调用者 scoped 查询（同样式模板 on-demand 合并
  语义），导出验证走 ``resolve_replay_template`` 的显式模板注入。
- **save-as-my-template**：``derive_payload_from_spec`` 从 live MapSpec 派生
  资产载荷（在系统基底模板上 → 参数绑定差异覆写；否则 inline）。

红线：不建第二注册表/第二 MapSpec 写入口；物化 fail-closed；所有失败面
携带 reason code（``ASSET_REASON_CODES``）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, select

from app.lib.cartography.template_assets import (
    BUILTIN_ASSET_PREFIX,
    TemplateAssetError,
    TemplateAssetPayload,
    TemplateAssetRecord,
    asset_fingerprint,
    derive_contract,
    get_builtin_asset,
    materialize_template,
    validate_asset_template,
)
from app.models.composition_template_asset import (
    CompositionTemplateAsset,
    CompositionTemplateAssetVersion,
)

logger = logging.getLogger(__name__)

#: 版本号并发冲突重试（CP-18 同款：savepoint 内重算）。
_MAX_REVISION_CREATE_ATTEMPTS = 3
#: 列表/派生有界。
MAX_LIST_ASSETS = 50
_MAX_DERIVED_SLOTS = 24

#: 失败面 reason codes（测试锁；工具层原样转发）。
ASSET_REASON_CODES = (
    "asset_not_found",
    "asset_forbidden",
    "asset_draft",
    "asset_retired",
    "asset_revision_not_found",
    "asset_version_not_published",
    "asset_payload_error",
    "asset_publish_gate_failed",
    "asset_conflict",
    "asset_invalid_visibility",
)


class CompositionAssetError(Exception):
    """资产生命周期操作非法（reason code 即 message 前缀；路由层映射 4xx）。"""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


class CompositionAssetForbiddenError(CompositionAssetError):
    """调用者无权读写目标资产（跨租户 IDOR 守卫）—— 403。"""

    def __init__(self, detail: str = ""):
        super().__init__("asset_forbidden", detail)


class CompositionAssetNotFoundError(CompositionAssetError):
    """资产不存在或不可见（不可见与不存在同返 404 语义）—— 404。"""

    def __init__(self, detail: str = ""):
        super().__init__("asset_not_found", detail)


class CompositionAssetConflictError(CompositionAssetError):
    """并发版本创建冲突且重试耗尽 —— 409。"""

    def __init__(self, detail: str = ""):
        super().__init__("asset_conflict", detail)


# ── 作用域 ────────────────────────────────────────────────────────────────


def _caller_project_ids(db: Any, user_id: Optional[str],
                        org_id) -> List[str]:
    """调用者可见的项目 id（属主或同 org 项目；有界 64）。"""
    if user_id is None:
        return []
    from app.models.project import Project
    clauses = [Project.owner_id == user_id]
    if org_id is not None:
        clauses.append(Project.org_id == org_id)
    rows = db.execute(
        select(Project.id).where(or_(*clauses)).limit(64)
    ).scalars().all()
    return [str(r) for r in rows]


def asset_scope_clause(user_id: Optional[str], org_id, role: Optional[str],
                       project_ids: Optional[List[str]] = None):
    """可见性谓词（None = admin 不限；匿名 = 一律不可见 DB 资产）。

    显式 visibility 模型：private → 仅 creator；org → 同 org；
    project → 项目属主/同 org 项目成员。**org 命中不暴露 private 资产**
    （比样式模板的隐式 org 子句更强——private 语义必须成立）。
    """
    from sqlalchemy import false as sa_false

    if role == "admin":
        return None
    if user_id is None:
        return sa_false()
    clauses = [CompositionTemplateAsset.creator_id == user_id]
    if org_id is not None:
        clauses.append(
            (CompositionTemplateAsset.visibility == "org")
            & (CompositionTemplateAsset.org_id == org_id))
    if project_ids:
        clauses.append(
            (CompositionTemplateAsset.visibility == "project")
            & CompositionTemplateAsset.project_id.in_(project_ids))
    return or_(*clauses)


def asset_visible(asset: CompositionTemplateAsset, user_id: Optional[str],
                  org_id, role: Optional[str] = None,
                  project_ids: Optional[List[str]] = None) -> bool:
    if role == "admin":
        return True
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


def can_write_asset(asset: CompositionTemplateAsset, user_id: Optional[str],
                    role: Optional[str] = None) -> bool:
    """写谓词：admin 或 creator（org/project 可见≠可写——共享是只读语义）。"""
    if role == "admin":
        return True
    return user_id is not None and asset.creator_id is not None \
        and str(asset.creator_id) == str(user_id)


def _project_accessible(project: Any, user_id: Optional[str],
                        org_id) -> bool:
    """项目归属：属主本人或同 org 成员（org_id 非 None 时）。"""
    if user_id is None:
        return False
    if project.owner_id is not None and str(project.owner_id) == str(user_id):
        return True
    return org_id is not None and project.org_id == org_id


def _require_writable(db: Any, asset_id: str, user_id: Optional[str],
                      org_id=None, role: Optional[str] = None,
                      ) -> CompositionTemplateAsset:
    """404（不可见/不存在）优先于 403（可见不可写）—— 不泄露存在性。"""
    project_ids = _caller_project_ids(db, user_id, org_id)
    asset = _get_asset_or_404(db, asset_id, user_id, org_id, role,
                              project_ids)
    if not can_write_asset(asset, user_id, role):
        raise CompositionAssetForbiddenError(asset_id[:64])
    return asset


def _get_asset_or_404(db: Any, asset_id: str, user_id: Optional[str], org_id,
                      role: Optional[str],
                      project_ids: Optional[List[str]] = None,
                      ) -> CompositionTemplateAsset:
    asset = db.get(CompositionTemplateAsset, asset_id)
    if asset is None or not asset_visible(
            asset, user_id, org_id, role, project_ids):
        raise CompositionAssetNotFoundError(asset_id[:64])
    return asset


# ── 版本创建（savepoint max+1）────────────────────────────────────────────


def _validate_payload_snapshot(payload: TemplateAssetPayload) -> List[str]:
    """物化 + 单模板校验（发布门同口径；异常转问题清单，不外抛）。"""
    record = TemplateAssetRecord(
        asset_id="asset.__validation__", revision=1, payload=payload)
    try:
        tpl = materialize_template(record)
    except TemplateAssetError as exc:
        return [str(exc)[:160]]
    try:
        return [i[:160] for i in validate_asset_template(tpl)[:16]]
    except Exception as exc:  # noqa: BLE001 — 校验器异常如实入快照
        return [f"asset_validate_error: {exc}"[:160]]


def _new_version(
    db: Any,
    asset: CompositionTemplateAsset,
    payload: Optional[TemplateAssetPayload] = None,
    *,
    created_by: Optional[str] = None,
    source_payload: Optional[Dict[str, Any]] = None,
) -> CompositionTemplateAssetVersion:
    """落新版本快照（draft；revision=max+1；指纹/校验快照随版本落库）。"""
    from sqlalchemy.exc import IntegrityError

    raw = source_payload if source_payload is not None \
        else (payload or TemplateAssetPayload()).model_dump()
    row = None
    next_revision = 0
    for _attempt in range(_MAX_REVISION_CREATE_ATTEMPTS):
        latest = (
            db.query(CompositionTemplateAssetVersion)
            .filter_by(asset_id=asset.id)
            .order_by(CompositionTemplateAssetVersion.revision.desc())
            .first()
        )
        next_revision = (latest.revision + 1) if latest is not None else 1
        record = TemplateAssetRecord(
            asset_id=asset.id, revision=next_revision,
            payload=TemplateAssetPayload.model_validate(raw))
        validation = _validate_payload_snapshot(record.payload)
        candidate = CompositionTemplateAssetVersion(
            asset_id=asset.id,
            revision=next_revision,
            payload=raw,
            status="draft",
            validation=validation or None,
            fingerprint=asset_fingerprint(record),
            created_by=(str(created_by)[:255] if created_by else None),
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
        except IntegrityError:
            logger.info(
                "asset %s revision %s conflict; retrying",
                asset.id, next_revision)
            continue
        row = candidate
        break
    if row is None:
        raise CompositionAssetConflictError(
            f"concurrent revision creation for asset '{asset.id}'")
    asset.latest_revision = next_revision
    db.commit()
    return row


# ── 生命周期 ──────────────────────────────────────────────────────────────


def create_asset(
    db: Any,
    *,
    payload: TemplateAssetPayload,
    visibility: str = "private",
    project_id: Optional[str] = None,
    creator_id: Optional[str],
    org_id=None,
    name: str = "",
    description: str = "",
    publish: bool = False,
) -> Tuple[CompositionTemplateAsset, CompositionTemplateAssetVersion]:
    """创建资产 + 首版 draft（可选即时发布；payload 模式由 lib 层校验）。"""
    if visibility not in ("private", "org", "project"):
        raise CompositionAssetError("asset_invalid_visibility", visibility)
    if visibility == "project":
        if not project_id:
            raise CompositionAssetError(
                "asset_invalid_visibility", "project requires project_id")
        from app.models.project import Project
        project = db.get(Project, project_id)
        if project is None:
            raise CompositionAssetError(
                "asset_invalid_visibility", f"project {project_id} not found")
        # 项目归属校验：creator 必须是项目属主或同 org（防止把资产挂到
        # 外国项目造成跨租户披露）。
        if not _project_accessible(project, creator_id, org_id):
            raise CompositionAssetForbiddenError(
                f"project {project_id} not accessible to creator")
    elif project_id:
        raise CompositionAssetError(
            "asset_invalid_visibility",
            "project_id only allowed for visibility=project")
    if creator_id is None:
        raise CompositionAssetForbiddenError("anonymous cannot create assets")
    try:
        payload.mode()  # fail-closed：恰居其一
    except TemplateAssetError as exc:
        raise CompositionAssetError(
            "asset_payload_error", str(exc)[:160]) from exc
    asset_id = f"asset.{uuid.uuid4().hex}"
    asset = CompositionTemplateAsset(
        id=asset_id,
        org_id=org_id,
        creator_id=str(creator_id)[:255],
        visibility=visibility,
        project_id=project_id if visibility == "project" else None,
        name=(name or payload.name or "未命名资产")[:255],
        description=(description or payload.description or None),
        status="draft",
        latest_revision=0,
        current_revision=0,
    )
    db.add(asset)
    db.flush()
    version = _new_version(db, asset, payload, created_by=creator_id)
    if publish:
        version = _publish_checked(asset, version)
        db.commit()
    return asset, version


def _publish_checked(asset: CompositionTemplateAsset,
                     version: CompositionTemplateAssetVersion,
                     ) -> CompositionTemplateAssetVersion:
    """发布门（内部；调用方负责事务）。校验零 issue 才放行。"""
    if version.status == "retired":
        raise CompositionAssetError(
            "asset_retired", f"revision {version.revision} retired")
    if version.validation:
        raise CompositionAssetError(
            "asset_publish_gate_failed",
            "; ".join(str(i) for i in version.validation[:4]))
    version.status = "published"
    asset.status = "published"
    asset.current_revision = max(int(asset.current_revision or 0),
                                 int(version.revision))
    return version


def publish_version(
    db: Any,
    asset_id: str,
    revision: Optional[int],
    *,
    user_id: Optional[str],
    org_id=None,
    role: Optional[str] = None,
) -> CompositionTemplateAssetVersion:
    """发布指定版本（写谓词 creator/admin；发布门 fail-closed）。"""
    asset = _require_writable(db, asset_id, user_id, org_id, role)
    version = _require_version(db, asset_id, revision)
    _publish_checked(asset, version)
    db.commit()
    return version


def new_version(
    db: Any,
    asset_id: str,
    payload: TemplateAssetPayload,
    *,
    user_id: Optional[str],
    org_id=None,
    role: Optional[str] = None,
    publish: bool = False,
) -> CompositionTemplateAssetVersion:
    """追加新 draft 版本（可选即时发布）。"""
    asset = _require_writable(db, asset_id, user_id, org_id, role)
    try:
        payload.mode()
    except TemplateAssetError as exc:
        raise CompositionAssetError(
            "asset_payload_error", str(exc)[:160]) from exc
    version = _new_version(db, asset, payload, created_by=user_id)
    if publish:
        version = _publish_checked(asset, version)
        db.commit()
    return version


def retire_asset(
    db: Any,
    asset_id: str,
    *,
    user_id: Optional[str],
    org_id=None,
    role: Optional[str] = None,
    note: str = "",
) -> CompositionTemplateAsset:
    """软退役：新 apply 拒绝；钉 revision 重放只读放行；可 restore 复活。"""
    asset = _require_writable(db, asset_id, user_id, org_id, role)
    asset.status = "retired"
    now = datetime.now(timezone.utc)
    for version in db.query(CompositionTemplateAssetVersion).filter_by(
            asset_id=asset.id, status="published").all():
        version.status = "retired"
        version.retired_at = now
        version.retired_note = str(note or "")[:500] or None
    db.commit()
    return asset


def restore_revision(
    db: Any,
    asset_id: str,
    from_revision: int,
    *,
    user_id: Optional[str],
    org_id=None,
    role: Optional[str] = None,
    publish: bool = True,
) -> CompositionTemplateAssetVersion:
    """恢复旧版本：以旧版 payload 造**新** revision（不重写历史）再发布。"""
    asset = _require_writable(db, asset_id, user_id, org_id, role)
    source = _require_version(db, asset_id, int(from_revision))
    version = _new_version(
        db, asset, source_payload=source.payload, created_by=user_id)
    if publish:
        version = _publish_checked(asset, version)
        db.commit()
    return version


def _require_version(db: Any, asset_id: str,
                     revision: Optional[int]
                     ) -> CompositionTemplateAssetVersion:
    stmt = db.query(CompositionTemplateAssetVersion).filter_by(
        asset_id=asset_id)
    row = (
        stmt.order_by(CompositionTemplateAssetVersion.revision.desc()).first()
        if revision is None else
        stmt.filter_by(revision=int(revision)).first()
    )
    if row is None:
        raise CompositionAssetError(
            "asset_revision_not_found",
            f"{asset_id[:48]}@r{revision or 'latest'}")
    return row


# ── 解析（for_apply / for_replay）─────────────────────────────────────────


class ResolvedAsset:
    """解析结果（record + 物化模板 + 派生契约；内存对象，不落库）。"""

    def __init__(self, record: TemplateAssetRecord, template, contract):
        self.record = record
        self.template = template
        self.contract = contract

    @property
    def asset_id(self) -> str:
        return self.record.asset_id

    @property
    def revision(self) -> int:
        return self.record.revision

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.record.asset_id[:64],
            "revision": self.record.revision,
            "status": self.record.status[:12],
            "builtin": self.record.builtin,
            "fingerprint": (self.record.fingerprint or "")[:80],
            "template_id": self.template.id[:64],
            "contract_id": self.contract.contract_id[:80],
            "template_version": self.contract.template_version[:16],
        }


def _record_of(asset: CompositionTemplateAsset,
               version: CompositionTemplateAssetVersion
               ) -> TemplateAssetRecord:
    return TemplateAssetRecord(
        asset_id=asset.id,
        revision=int(version.revision),
        status=str(version.status or "draft"),
        payload=TemplateAssetPayload.model_validate(version.payload),
        builtin=False,
        fingerprint=str(version.fingerprint or ""),
    )


def resolve_asset_for(
    db: Any,
    asset_id: str,
    revision: Optional[int] = None,
    *,
    mode: str = "for_apply",
    user_id: Optional[str] = None,
    org_id=None,
    role: Optional[str] = None,
    project_ids: Optional[List[str]] = None,
) -> ResolvedAsset:
    """资产解析（builtin 代码资产优先；DB 资产过可见性）。

    - ``for_apply``（新应用；含显式钉版本）：DB 资产须 asset.status=
      published 且目标版本 status=published；revision=None →
      current_revision。**draft 永不可应用**（发布门是准入闸，钉版本
      不绕过 —— 否则 org/project 成员可把同事未过校验的草稿写进自己图）。
    - ``for_replay``（旧图打开/导出重放）：版本须 published 或 retired
      （曾发布过、只读重放）；draft 拒绝（未过发布门的载荷不进渲染）。

    注册表纪律：只有 builtin（公共命名空间）资产 upsert 进组合注册表；
    DB 资产不全局注册（防跨租户元数据泄漏），apply/导出走本函数返回的
    显式模板。
    """
    builtin = get_builtin_asset(asset_id)
    if builtin is not None:
        return _resolve_builtin(builtin, revision, mode)
    if asset_id.startswith(BUILTIN_ASSET_PREFIX):
        raise CompositionAssetNotFoundError(asset_id[:64])

    asset = db.get(CompositionTemplateAsset, asset_id)
    if asset is None or not asset_visible(
            asset, user_id, org_id, role, project_ids):
        raise CompositionAssetNotFoundError(asset_id[:64])

    if revision is None:
        if mode == "for_apply":
            if asset.status == "retired":
                raise CompositionAssetError("asset_retired", asset_id[:64])
            if asset.status != "published" \
                    or int(asset.current_revision or 0) < 1:
                raise CompositionAssetError("asset_draft", asset_id[:64])
            revision = int(asset.current_revision)
        else:
            revision = int(asset.latest_revision or 1)
    version = _require_version(db, asset_id, revision)
    record = _record_of(asset, version)
    if mode == "for_apply":
        if asset.status == "retired":
            raise CompositionAssetError("asset_retired", asset_id[:64])
        if version.status != "published":
            raise CompositionAssetError(
                "asset_version_not_published",
                f"{asset_id[:48]}@r{revision}:{version.status}")
    elif version.status == "draft":
        # 重放面也拒绝 draft：未过发布门的载荷不渲染（只读不降低准入）
        raise CompositionAssetError(
            "asset_version_not_published",
            f"{asset_id[:48]}@r{revision}:draft")
    try:
        tpl = materialize_template(record)
    except TemplateAssetError as exc:
        raise CompositionAssetError(
            "asset_payload_error", str(exc)[:160]) from exc
    contract = derive_contract(record, tpl)
    return ResolvedAsset(record, tpl, contract)


def _resolve_builtin(builtin: TemplateAssetRecord,
                     revision: Optional[int],
                     mode: str) -> ResolvedAsset:
    if revision is not None and revision != builtin.revision:
        raise CompositionAssetError(
            "asset_revision_not_found",
            f"{builtin.asset_id}@r{revision}"
            f"（builtin 当前 r{builtin.revision}）")
    if mode == "for_apply" and builtin.status != "published":
        raise CompositionAssetError("asset_retired", builtin.asset_id)
    tpl = materialize_template(builtin)
    contract = derive_contract(builtin, tpl)
    # builtin 是公共内容：惰性合并进组合注册表（单调 revision），使
    # plan/product/manifest 的注册表消费方零改动可见。
    if mode == "for_apply":
        from app.lib.cartography.composition_templates import (
            get_composition_template_registry,
        )
        get_composition_template_registry().upsert_asset(
            tpl, revision=builtin.revision)
    return ResolvedAsset(builtin, tpl, contract)


def tool_caller_identity() -> Tuple[Optional[str], Any]:
    """工具执行上下文 → (user_id, org_id)；缺席 = 匿名（builtin-only）。"""
    try:
        from app.services.provenance.context import get_tool_execution_context
        ctx = get_tool_execution_context()
        if ctx is not None:
            return ctx.user_id, ctx.org_id
    except Exception:  # noqa: BLE001 — 上下文缺席 → 匿名
        pass
    return None, None


def resolve_asset(db: Any, asset_id: str, revision: Optional[int] = None,
                  *, mode: str = "for_apply",
                  user_id: Optional[str] = None, org_id=None,
                  role: Optional[str] = None) -> ResolvedAsset:
    """工具面便捷入口（身份默认取 ToolExecutionContext，可显式覆盖）。"""
    if user_id is None:
        user_id, org_id = tool_caller_identity()
    project_ids = _caller_project_ids(db, user_id, org_id)
    return resolve_asset_for(
        db, asset_id, revision, mode=mode, user_id=user_id, org_id=org_id,
        role=role, project_ids=project_ids)


def resolve_replay_template(
    db: Any,
    asset_id: str,
    revision: Optional[int] = None,
    *,
    user_id: Optional[str] = None,
    org_id=None,
    role: Optional[str] = None,
) -> Optional[Any]:
    """旧图重渲染/导出的显式模板解析（不进全局注册表）。

    会话属主可见性内解析钉版本模板；不可见/不存在 → None（调用方让
    manifest 的 requiredness 诚实降级 not_evaluated，不静默、不越权）。
    """
    try:
        resolved = resolve_asset_for(
            db, asset_id, revision, mode="for_replay",
            user_id=user_id, org_id=org_id, role=role)
        return resolved.template
    except CompositionAssetError:
        return None


# ── 列表 ──────────────────────────────────────────────────────────────────


def list_assets(
    db: Any,
    *,
    user_id: Optional[str],
    org_id,
    role: Optional[str] = None,
    include_retired: bool = False,
    limit: int = MAX_LIST_ASSETS,
) -> List[Dict[str, Any]]:
    """调用者可见的 DB 资产（有界；含最新版本指纹）。"""
    project_ids = _caller_project_ids(db, user_id, org_id)
    scope = asset_scope_clause(user_id, org_id, role, project_ids)
    stmt = select(CompositionTemplateAsset)
    if scope is not None:
        stmt = stmt.where(scope)
    if not include_retired:
        stmt = stmt.where(CompositionTemplateAsset.status != "retired")
    stmt = stmt.order_by(
        CompositionTemplateAsset.updated_at.desc(),
        CompositionTemplateAsset.id,
    ).limit(max(1, min(int(limit), MAX_LIST_ASSETS)))
    out: List[Dict[str, Any]] = []
    for asset in db.execute(stmt).scalars().all():
        version = _require_version(db, asset.id, None) \
            if asset.latest_revision else None
        out.append({
            "asset_id": asset.id,
            "name": asset.name,
            "description": (asset.description or "")[:160],
            "visibility": asset.visibility,
            "project_id": asset.project_id,
            "status": asset.status,
            "latest_revision": asset.latest_revision,
            "current_revision": asset.current_revision,
            "fingerprint": (version.fingerprint or "") if version else "",
            "created_at": asset.created_at.isoformat()
            if asset.created_at else None,
            "updated_at": asset.updated_at.isoformat()
            if asset.updated_at else None,
        })
    return out


def list_versions(db: Any, asset_id: str, *, user_id: Optional[str], org_id,
                  role: Optional[str] = None) -> List[Dict[str, Any]]:
    project_ids = _caller_project_ids(db, user_id, org_id)
    _get_asset_or_404(db, asset_id, user_id, org_id, role, project_ids)
    rows = (
        db.query(CompositionTemplateAssetVersion)
        .filter_by(asset_id=asset_id)
        .order_by(CompositionTemplateAssetVersion.revision.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "revision": r.revision,
            "status": r.status,
            "validation": (r.validation or [])[:8],
            "fingerprint": (r.fingerprint or "")[:80],
            "retired_at": r.retired_at.isoformat() if r.retired_at else None,
            "retired_note": (r.retired_note or "")[:160],
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# ── save-as-my-template（从 live MapSpec 派生资产载荷）────────────────────


def derive_payload_from_spec(spec: Dict[str, Any]) -> TemplateAssetPayload:
    """live MapSpec → 资产载荷（在系统基底模板上 → 参数绑定差异；否则 inline）。

    派生语义：在场且 enabled 的组件 → required 槽（重放可复原用户结构）；
    disabled → optional；position/variant 绑定进 position_zone/
    preferred_templates。apply 预检不带 template（契约面），空会话应用由
    apply_contract 物化空槽满足 —— 不会因 required 预检假红。
    """
    from app.lib.cartography.composition_contract import (
        read_composition_identity,
    )
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    from app.lib.cartography.component_templates import (
        get_component_template_registry,
    )

    layout = spec.get("layout") if isinstance(spec, dict) else None
    raw_components = (
        [c for c in (layout.get("components") or []) if isinstance(c, dict)]
        if isinstance(layout, dict) else [])
    identity = read_composition_identity(spec)
    registry = get_composition_template_registry()
    ct_reg = get_component_template_registry()

    base = None
    if identity is not None and identity.template_id:
        candidate = registry.get(identity.template_id)
        if candidate is not None and not identity.template_id.startswith(
                "asset."):
            base = candidate

    derived_slots: List[Dict[str, Any]] = []
    seen_slot_ids = set()
    for comp in raw_components[:_MAX_DERIVED_SLOTS]:
        ctype = str(comp.get("type") or "")
        if not ctype:
            continue
        # 派生 slot id 优先解析到 base 槽位（实例 id 与槽位 id 不同名：
        # 实例 id 走 INSTANCE_ID_BY_TYPE 约定如 legend→legend-main）。
        # 解析不到 → 用组件自身 slot 身份（组件 id/类型）承载。
        slot_id = _resolve_slot_id(comp, base, ctype)
        if slot_id in seen_slot_ids:
            continue
        seen_slot_ids.add(slot_id)
        enabled = comp.get("enabled") is not False
        position = str(comp.get("position") or "")
        slot: Dict[str, Any] = {
            "id": slot_id,
            "allowed_component_types": [ctype],
            "cardinality": "required" if enabled else "optional",
            "required": enabled,
            "position_zone": position if position and position != "none"
            else "none",
        }
        variant = str(comp.get("variant") or "")
        if variant:
            for ct in ct_reg.find_by_type(ctype):
                if ct.variant == variant:
                    slot["preferred_templates"] = [ct.id]
                    break
        derived_slots.append(slot)

    if base is not None:
        base_slots = {s.id: s.model_dump() for s in base.component_slots}
        overrides: Dict[str, Dict[str, Any]] = {}
        unresolved = 0
        for slot in derived_slots:
            base_slot = base_slots.get(slot["id"])
            if base_slot is None:
                # 用户新增的组件不在基底槽位表中：无法用差异覆写表达，
                # 计入 unresolved → 走 inline（保真，不做半份变体）。
                unresolved += 1
                continue
            diff = {
                k: v for k, v in slot.items()
                if k != "id" and base_slot.get(k) != v
            }
            if diff:
                overrides[slot["id"]] = diff
        if unresolved:
            # 诚实降级：不能把新增组件吞掉（重放必须复原用户结构）
            return _inline_payload_from_slots(derived_slots)
        return TemplateAssetPayload(
            base_template_id=base.id,
            slot_overrides=overrides,
            name=base.name,
            description=f"Saved from session (base {base.id})",
            tags=["saved-from-session"],
        )

    return _inline_payload_from_slots(derived_slots)


def _resolve_slot_id(comp: Dict[str, Any], base: Any, ctype: str) -> str:
    """组件实例 → 基底模板槽位 id（类型匹配首个允许该类型的槽位）。

    匹配不到的实例 → 自身 id（承载用户新增组件；上层 unresolved 逻辑
    决定是否降级 inline）。不猜：多槽位同型时取**声明序首个**（与
    apply_contract 的「allowed_component_types 顺序即偏好序」同口径）。
    """
    if base is not None:
        for slot in base.component_slots:
            if ctype in (slot.allowed_component_types or []):
                return slot.id
    return str(comp.get("id") or ctype)[:32]


def _inline_payload_from_slots(
        derived_slots: List[Dict[str, Any]]) -> TemplateAssetPayload:
    return TemplateAssetPayload(
        inline_template={
            "id": "asset.saved_from_session",
            "name": "Saved from session",
            "description": "Derived from live MapSpec composition",
            "component_slots": derived_slots,
            "output_targets": ["interactive"],
        },
        name="Saved from session",
        tags=["saved-from-session"],
    )


__all__ = [
    "ASSET_REASON_CODES",
    "CompositionAssetError",
    "CompositionAssetForbiddenError",
    "CompositionAssetNotFoundError",
    "CompositionAssetConflictError",
    "ResolvedAsset",
    "MAX_LIST_ASSETS",
    "asset_scope_clause",
    "asset_visible",
    "can_write_asset",
    "create_asset",
    "new_version",
    "publish_version",
    "retire_asset",
    "restore_revision",
    "resolve_asset",
    "resolve_asset_for",
    "resolve_replay_template",
    "tool_caller_identity",
    "list_assets",
    "list_versions",
    "derive_payload_from_spec",
]
