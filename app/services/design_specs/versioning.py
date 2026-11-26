"""Design Specs 版本化 —— 不可变快照 / 继承 / 软退役 / 命名查重（WP1）。

先例对齐 ``app/services/templates/versioning.py``（0048）与 G04 资产目录
（0098）：不可变版本快照、version = max+1 savepoint 重试、同规范内
parent_version_id 深合并继承（深度帽 ≤16 + 环守卫）、软废弃（兼容读取）。
**零新版本化机制** —— 唯一新增是引用校验投影（对照权威 registry）与
结构 digest 随版本行落库。

SEC-02 纪律：``can_write`` / ``can_read_parent`` / 可见性谓词由路由层
注入，在读写任何行之前校验；跨租户 IDOR 守卫同 ``templates/scope.py``
（builtin ∪ creator ∪ org；org_id IS NULL 不当 public）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sqlalchemy import or_

from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion
from app.services.design_specs.contract import (
    canonical_json,
    structural_digest,
    validate_design_spec_payload,
)
from app.services.design_specs.errors import (
    DesignSpecConflictError,
    DesignSpecForbiddenError,
    DesignSpecNotFoundError,
    DesignSpecValidationError,
)

logger = logging.getLogger(__name__)

_MAX_CHAIN_DEPTH = 16
#: CP-18 同款：version = max+1 并发冲突重试次数。
_MAX_VERSION_CREATE_ATTEMPTS = 3
_NAME_MAX = 255


# ── 租户可见性（templates/scope.py 同款语义） ─────────────────────────


def design_spec_scope_clause(user_id: Optional[str], org_id: Any):
    """builtin ∪ creator ∪ org；admin 不跨租户；匿名只看 builtin。"""
    if user_id is None:
        return CartographyDesignSpec.is_builtin.is_(True)
    clauses = [
        CartographyDesignSpec.is_builtin.is_(True),
        CartographyDesignSpec.creator_id == user_id,
    ]
    if org_id is not None:
        clauses.append(CartographyDesignSpec.org_id == org_id)
    return or_(*clauses)


def design_spec_visible(spec: CartographyDesignSpec, user_id: Optional[str], org_id: Any) -> bool:
    if spec.is_builtin:
        return True
    if org_id is not None and spec.org_id == org_id:
        return True
    return user_id is not None and str(spec.creator_id) == str(user_id)


def design_spec_can_write(spec: CartographyDesignSpec, user_id: Optional[str], org_id: Any, role: Optional[str]) -> bool:
    """写谓词：同组织 admin，或 creator；builtin 只读。"""
    if spec.is_builtin:
        return False
    if (
        role == "admin"
        and org_id is not None
        and spec.org_id is not None
        and spec.org_id == org_id
    ):
        return True
    return user_id is not None and str(spec.creator_id) == str(user_id)


# ── 校验入口 ─────────────────────────────────────────────────────────


def _validated_payload_or_raise(payload: Dict[str, Any]) -> Dict[str, Any]:
    report = validate_design_spec_payload(payload)
    if not report["ok"]:
        raise DesignSpecValidationError(
            "design spec payload failed validation",
            violations=report["violations"],
        )
    return report


# ── 版本 CRUD ────────────────────────────────────────────────────────


def create_spec(
    db: Any,
    payload: Dict[str, Any],
    *,
    creator_id: Optional[str] = None,
    org_id: Optional[int] = None,
    is_builtin: bool = False,
    created_by: Optional[str] = None,
    can_write: Optional[Callable[[Any], bool]] = None,
) -> CartographyDesignSpec:
    """新建规范（version 1 + 命名查重 + 校验 fail-closed）。"""
    _validated_payload_or_raise(payload)
    name = str(payload.get("name") or "")[:_NAME_MAX]
    if not name.strip():
        raise DesignSpecValidationError("design spec requires a non-empty name")

    # 命名查重：同 scope（builtin / creator+org）内唯一。DB 唯一约束在
    # NULL 列上不生效（SQLite/PG 语义），服务层判定是权威；uq 约束仅兜底。
    from sqlalchemy import and_

    same_scope = [
        CartographyDesignSpec.name == name,
        CartographyDesignSpec.is_builtin.is_(True) if is_builtin
        else and_(
            CartographyDesignSpec.is_builtin.is_(False),
            CartographyDesignSpec.creator_id == (str(creator_id) if creator_id else None),
        ),
    ]
    existing = (
        db.query(CartographyDesignSpec).filter(*same_scope).first()
    )
    if existing is not None:
        raise DesignSpecConflictError(
            f"design spec name '{name}' already exists in this scope",
            code="DESIGN_SPEC_NAME_CONFLICT",
        )

    report = validate_design_spec_payload(payload)
    spec = CartographyDesignSpec(
        org_id=org_id,
        name=name,
        description=str(payload.get("description") or "")[:2000],
        is_builtin=1 if is_builtin else 0,
        creator_id=(str(creator_id) if creator_id else None),
        status="published" if is_builtin else "draft",
        payload=payload,
        current_version=1,
        structural_digest=structural_digest(payload),
    )
    db.add(spec)
    db.flush()
    version_row = CartographyDesignSpecVersion(
        org_id=org_id,
        spec_id=spec.id,
        version=1,
        payload=payload,
        parent_version_id=None,
        reference_violations=report["violations"] or None,
        reference_warnings=report["warnings"] or None,
        structural_digest=structural_digest(payload),
        created_by=(str(created_by)[:255] if created_by else None),
    )
    db.add(version_row)
    db.commit()
    return spec


def create_version(
    db: Any,
    spec_id: str,
    payload: Dict[str, Any],
    *,
    parent_version_id: Optional[str] = None,
    created_by: Optional[str] = None,
    commit_current: bool = True,
    can_write: Optional[Callable[[Any], bool]] = None,
    can_read_parent: Optional[Callable[[Any], bool]] = None,
) -> CartographyDesignSpecVersion:
    """落新版本快照（version = max+1 savepoint 重试；引用校验 + digest 快照）。"""
    spec = db.get(CartographyDesignSpec, spec_id)
    if spec is None:
        raise DesignSpecNotFoundError(f"design spec '{spec_id}' not found")
    if can_write is not None and not can_write(spec):
        raise DesignSpecForbiddenError(
            f"design spec '{spec_id}' is not writable by this caller"
        )

    report = _validated_payload_or_raise(payload)

    if parent_version_id is not None:
        parent = db.get(CartographyDesignSpecVersion, parent_version_id)
        if parent is None:
            raise DesignSpecNotFoundError("parent_version not found")
        if str(parent.spec_id) != str(spec_id):
            raise DesignSpecValidationError(
                "parent_version_id must belong to the same design spec"
            )
        if can_read_parent is not None and not can_read_parent(spec):
            raise DesignSpecForbiddenError(
                "parent version belongs to a spec not visible to this caller"
            )
        _assert_acyclic(db, parent_version_id)

    from sqlalchemy.exc import IntegrityError

    row = None
    for attempt in range(_MAX_VERSION_CREATE_ATTEMPTS):
        latest = (
            db.query(CartographyDesignSpecVersion)
            .filter_by(spec_id=spec_id)
            .order_by(CartographyDesignSpecVersion.version.desc())
            .first()
        )
        next_version = (latest.version + 1) if latest is not None else 1
        candidate = CartographyDesignSpecVersion(
            org_id=spec.org_id,
            spec_id=str(spec_id)[:36],
            version=next_version,
            payload=payload,
            parent_version_id=parent_version_id,
            reference_violations=report["violations"] or None,
            reference_warnings=report["warnings"] or None,
            structural_digest=structural_digest(payload),
            created_by=(str(created_by)[:255] if created_by else None),
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
        except IntegrityError:
            logger.info(
                "design spec %s version %s conflict (attempt %d); retrying",
                spec_id, next_version, attempt + 1,
            )
            continue
        row = candidate
        break
    if row is None:
        raise DesignSpecConflictError(
            f"concurrent version creation for design spec '{spec_id}'; retry"
        )
    if commit_current:
        effective, _chain = resolve_payload(db, row)
        spec.payload = effective
        spec.current_version = next_version
        spec.structural_digest = structural_digest(effective)
    db.commit()
    return row


def _assert_acyclic(db: Any, parent_version_id: str) -> None:
    """深度帽 + 防御性环守卫（同 templates.versioning 纪律）。"""
    seen: Set[str] = set()
    cursor: Optional[str] = parent_version_id
    depth = 0
    while cursor is not None and depth <= _MAX_CHAIN_DEPTH:
        if cursor in seen:
            raise DesignSpecValidationError("inheritance cycle detected")
        seen.add(cursor)
        node = db.get(CartographyDesignSpecVersion, cursor)
        if node is None:
            break
        cursor = node.parent_version_id
        depth += 1
    if depth > _MAX_CHAIN_DEPTH:
        raise DesignSpecValidationError(
            f"inheritance chain > {_MAX_CHAIN_DEPTH}"
        )


def resolve_payload(
    db: Any, version: CartographyDesignSpecVersion
) -> Tuple[Dict[str, Any], List[str]]:
    """版本 → effective payload（沿 parent 链深合并，子覆盖父）+ 链上 spec id。"""
    from app.services.templates.versioning import deep_merge

    chain: List[CartographyDesignSpecVersion] = []
    seen: Set[str] = set()
    cursor: Optional[CartographyDesignSpecVersion] = version
    while cursor is not None and len(chain) <= _MAX_CHAIN_DEPTH:
        if cursor.id in seen:
            raise DesignSpecValidationError("inheritance cycle detected")
        seen.add(cursor.id)
        chain.append(cursor)
        cursor = (
            db.get(CartographyDesignSpecVersion, cursor.parent_version_id)
            if cursor.parent_version_id
            else None
        )
    if cursor is not None:
        raise DesignSpecValidationError(f"inheritance chain > {_MAX_CHAIN_DEPTH}")
    effective: Dict[str, Any] = {}
    for node in reversed(chain):
        if isinstance(node.payload, dict):
            effective = deep_merge(effective, node.payload)
    return effective, [n.spec_id for n in reversed(chain)]


def get_spec(db: Any, spec_id: str) -> CartographyDesignSpec:
    spec = db.get(CartographyDesignSpec, spec_id)
    if spec is None:
        raise DesignSpecNotFoundError(f"design spec '{spec_id}' not found")
    return spec


def get_version(
    db: Any, spec_id: str, version: Optional[int] = None
) -> CartographyDesignSpecVersion:
    stmt = db.query(CartographyDesignSpecVersion).filter_by(spec_id=spec_id)
    row = (
        stmt.order_by(CartographyDesignSpecVersion.version.desc()).first()
        if version is None
        else stmt.filter_by(version=int(version)).first()
    )
    if row is None:
        raise DesignSpecNotFoundError("design spec version not found")
    return row


def list_specs(
    db: Any,
    user_id: Optional[str],
    org_id: Any,
    *,
    limit: int = 50,
) -> List[CartographyDesignSpec]:
    """scope 内列表（builtin ∪ creator ∪ org；按 updated_at 倒序有界）。"""
    return (
        db.query(CartographyDesignSpec)
        .filter(design_spec_scope_clause(user_id, org_id))
        .order_by(CartographyDesignSpec.updated_at.desc())
        .limit(max(1, min(int(limit), 200)))
        .all()
    )


def deprecate_version(
    db: Any,
    spec_id: str,
    version: Optional[int],
    *,
    note: str = "",
    actor: Optional[str] = None,
    can_write: Optional[Callable[[Any], bool]] = None,
) -> CartographyDesignSpecVersion:
    """失效标记（软废弃 —— 兼容读取继续可用，响应带标记）。"""
    spec = get_spec(db, spec_id)
    if can_write is not None and not can_write(spec):
        raise DesignSpecForbiddenError(
            f"design spec '{spec_id}' is not writable by this caller"
        )
    row = get_version(db, spec_id, version)
    row.deprecated_at = datetime.now(timezone.utc)
    row.deprecation_note = str(note or "")[:500]
    db.commit()
    return row


def retire_spec(
    db: Any,
    spec_id: str,
    *,
    note: str = "",
    can_write: Optional[Callable[[Any], bool]] = None,
) -> CartographyDesignSpec:
    """规范整体退役（软 —— 既有系列 pin 继续可解析，新绑定建议拒绝）。"""
    spec = get_spec(db, spec_id)
    if can_write is not None and not can_write(spec):
        raise DesignSpecForbiddenError(
            f"design spec '{spec_id}' is not writable by this caller"
        )
    spec.status = "retired"
    spec.retired_at = datetime.now(timezone.utc)
    spec.retirement_note = str(note or "")[:500]
    db.commit()
    return spec


def publish_spec(
    db: Any,
    spec_id: str,
    *,
    can_write: Optional[Callable[[Any], bool]] = None,
) -> CartographyDesignSpec:
    """draft → published（发布门：当前 effective payload 校验零 violation）。"""
    spec = get_spec(db, spec_id)
    if can_write is not None and not can_write(spec):
        raise DesignSpecForbiddenError(
            f"design spec '{spec_id}' is not writable by this caller"
        )
    report = validate_design_spec_payload(spec.payload or {})
    if not report["ok"]:
        raise DesignSpecValidationError(
            "cannot publish design spec with reference violations",
            violations=report["violations"],
        )
    spec.status = "published"
    db.commit()
    return spec


__all__ = [
    "design_spec_scope_clause",
    "design_spec_visible",
    "design_spec_can_write",
    "create_spec",
    "create_version",
    "resolve_payload",
    "get_spec",
    "get_version",
    "list_specs",
    "deprecate_version",
    "retire_spec",
    "publish_spec",
    "canonical_json",
]
