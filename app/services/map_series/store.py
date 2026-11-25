"""Map Series 版本化与 CRUD —— 不可变快照 / 继承 / 软退役 / 命名查重（WP3）。

先例对齐 ``app/services/design_specs/versioning.py``（WP1，本身照
templates.versioning 0048 先例）：不可变版本快照、version = max+1 savepoint
重试、同系列内 parent_version_id 深合并继承（深度帽 ≤16 + 环守卫）、
软废弃（兼容读取）。**零新版本化机制**。

SEC-02 纪律：``can_write`` / 可见性谓词由路由层注入，在读写任何行之前
校验；跨租户 IDOR 守卫同 ``templates/scope.py``（builtin ∪ creator ∪ org；
org_id IS NULL 不当 public；写 = creator 或同 org admin）。

设计规范 pin：创建/落版本时解析时点校验存在性（fail-closed）；退役后的
既有 pin 继续可解析（软废弃 —— 解析不校验 status）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sqlalchemy import or_

from app.models.map_series import CartographyMapSeries, CartographyMapSeriesVersion
from app.services.map_series.contract import (
    structural_digest,
    validate_map_series_payload,
)
from app.services.map_series.errors import (
    MapSeriesConflictError,
    MapSeriesForbiddenError,
    MapSeriesNotFoundError,
    MapSeriesValidationError,
)

logger = logging.getLogger(__name__)

_MAX_CHAIN_DEPTH = 16
#: CP-18 同款：version = max+1 并发冲突重试次数。
_MAX_VERSION_CREATE_ATTEMPTS = 3
_NAME_MAX = 255


# ── 租户可见性（templates/scope.py 同款语义） ─────────────────────────


def map_series_scope_clause(user_id: Optional[str], org_id: Any):
    """builtin ∪ creator ∪ org；admin 不跨租户；匿名只看 builtin。"""
    if user_id is None:
        return CartographyMapSeries.is_builtin.is_(True)
    clauses = [
        CartographyMapSeries.is_builtin.is_(True),
        CartographyMapSeries.creator_id == user_id,
    ]
    if org_id is not None:
        clauses.append(CartographyMapSeries.org_id == org_id)
    return or_(*clauses)


def map_series_visible(series: CartographyMapSeries, user_id: Optional[str], org_id: Any) -> bool:
    if series.is_builtin:
        return True
    if org_id is not None and series.org_id == org_id:
        return True
    return user_id is not None and str(series.creator_id) == str(user_id)


def map_series_can_write(
    series: CartographyMapSeries, user_id: Optional[str], org_id: Any, role: Optional[str]
) -> bool:
    """写谓词：同组织 admin，或 creator；builtin 只读。"""
    if series.is_builtin:
        return False
    if (
        role == "admin"
        and org_id is not None
        and series.org_id is not None
        and series.org_id == org_id
    ):
        return True
    return user_id is not None and str(series.creator_id) == str(user_id)


# ── 校验与 pin 解析 ──────────────────────────────────────────────────


def _validated_payload_or_raise(payload: Dict[str, Any]) -> Dict[str, Any]:
    report = validate_map_series_payload(payload)
    if not report["ok"]:
        raise MapSeriesValidationError(
            "map series payload failed validation",
            violations=report["violations"],
        )
    return report


def resolve_spec_pin(
    db: Any, payload: Dict[str, Any]
) -> Tuple[str, Optional[int]]:
    """解析时点校验规范 pin：spec 必须已存在（fail-closed）；版本缺省 =
    spec 当前版（latest 解析时点记录）。退役规范返回值不变（软废弃兼容）。"""
    from app.services.design_specs.versioning import get_spec

    spec_id = str(payload.get("design_spec_id") or "")
    try:
        spec = get_spec(db, spec_id)
    except Exception as exc:  # noqa: BLE001 —— 任何不可解析（不存在/库缺失）都 fail-closed
        raise MapSeriesValidationError(
            f"design spec pin '{spec_id}' is not resolvable",
            violations=[{"field": "design_spec_id", "ref": spec_id[:60], "reason": "SPEC_PIN_UNRESOLVABLE"}],
        ) from exc
    pinned = payload.get("design_spec_version")
    if pinned is None:
        pinned = int(spec.current_version or 1)
    return spec_id, int(pinned)


# ── 系列 CRUD ────────────────────────────────────────────────────────


def create_series(
    db: Any,
    payload: Dict[str, Any],
    *,
    creator_id: Optional[str] = None,
    org_id: Optional[int] = None,
    is_builtin: bool = False,
    created_by: Optional[str] = None,
) -> CartographyMapSeries:
    """新建系列（version 1 + 命名查重 + 校验 fail-closed + pin 解析）。"""
    _validated_payload_or_raise(payload)
    name = str(payload.get("name") or "")[:_NAME_MAX]
    if not name.strip():
        raise MapSeriesValidationError("map series requires a non-empty name")

    # 命名查重：同 scope（builtin / creator+org）内唯一。服务层判定是权威。
    from sqlalchemy import and_

    same_scope = [
        CartographyMapSeries.name == name,
        CartographyMapSeries.is_builtin.is_(True) if is_builtin
        else and_(
            CartographyMapSeries.is_builtin.is_(False),
            CartographyMapSeries.creator_id == (str(creator_id) if creator_id else None),
        ),
    ]
    existing = db.query(CartographyMapSeries).filter(*same_scope).first()
    if existing is not None:
        raise MapSeriesConflictError(
            f"map series name '{name}' already exists in this scope",
            code="MAP_SERIES_NAME_CONFLICT",
        )

    spec_id, spec_version = resolve_spec_pin(db, payload)
    # 缺省 pin 在解析时点回写进 payload —— 之后 latest 前移不影响本 pin。
    payload = dict(payload)
    payload["design_spec_version"] = spec_version

    series = CartographyMapSeries(
        org_id=org_id,
        name=name,
        description=str(payload.get("description") or "")[:2000],
        is_builtin=1 if is_builtin else 0,
        creator_id=(str(creator_id) if creator_id else None),
        status="published" if is_builtin else "draft",
        design_spec_id=spec_id,
        spec_version=spec_version,
        payload=payload,
        current_version=1,
        structural_digest=structural_digest(payload),
    )
    db.add(series)
    db.flush()
    version_row = CartographyMapSeriesVersion(
        org_id=org_id,
        series_id=series.id,
        version=1,
        payload=payload,
        parent_version_id=None,
        structural_digest=structural_digest(payload),
        created_by=(str(created_by)[:255] if created_by else None),
    )
    db.add(version_row)
    db.commit()
    return series


def upsert_member(
    db: Any,
    series_id: str,
    member: Dict[str, Any],
    *,
    can_write: Optional[Callable[[Any], bool]] = None,
) -> CartographyMapSeries:
    """增/改单个成员（按 member_id 替换；整计划重校验；主行前移，不落版本）。"""
    series = get_series(db, series_id)
    if can_write is not None and not can_write(series):
        raise MapSeriesForbiddenError(f"map series '{series_id}' is not writable by this caller")
    payload = dict(series.payload or {})
    members = [m for m in (payload.get("members") or []) if isinstance(m, dict)]
    target_id = str(member.get("member_id") or "")
    if member.get("design_spec_id") or member.get("design_spec_version"):
        # 成员级不允许改系列 pin —— 显式拒绝（pin 是系列级权威）。
        raise MapSeriesValidationError(
            "members may not carry design spec pins",
            violations=[{"field": f"members.{target_id}", "ref": "", "reason": "MEMBER_SPEC_PIN_FORBIDDEN"}],
        )
    replaced = False
    next_members: List[Dict[str, Any]] = []
    for existing_member in members:
        if str(existing_member.get("member_id") or "") == target_id:
            next_members.append(member)
            replaced = True
        else:
            next_members.append(existing_member)
    if not replaced:
        next_members.append(member)
    payload["members"] = next_members
    report = _validated_payload_or_raise(payload)
    series.payload = report["series"] or payload
    series.structural_digest = structural_digest(payload)
    db.commit()
    return series


def remove_member(
    db: Any,
    series_id: str,
    member_id: str,
    *,
    can_write: Optional[Callable[[Any], bool]] = None,
) -> CartographyMapSeries:
    """删单个成员（不存在 = 404 语义由调用方映射；整计划重校验）。"""
    series = get_series(db, series_id)
    if can_write is not None and not can_write(series):
        raise MapSeriesForbiddenError(f"map series '{series_id}' is not writable by this caller")
    payload = dict(series.payload or {})
    members = [m for m in (payload.get("members") or []) if isinstance(m, dict)]
    next_members = [
        m for m in members if str(m.get("member_id") or "") != str(member_id)
    ]
    if len(next_members) == len(members):
        raise MapSeriesNotFoundError(f"member '{member_id}' not found in series '{series_id}'")
    payload["members"] = next_members
    _validated_payload_or_raise(payload)
    series.payload = payload
    series.structural_digest = structural_digest(payload)
    db.commit()
    return series


def create_version(
    db: Any,
    series_id: str,
    payload: Dict[str, Any],
    *,
    parent_version_id: Optional[str] = None,
    created_by: Optional[str] = None,
    can_write: Optional[Callable[[Any], bool]] = None,
    can_read_parent: Optional[Callable[[Any], bool]] = None,
) -> CartographyMapSeriesVersion:
    """落新版本快照（version = max+1 savepoint 重试；校验 + pin 解析 + digest）。"""
    series = get_series(db, series_id)
    if can_write is not None and not can_write(series):
        raise MapSeriesForbiddenError(f"map series '{series_id}' is not writable by this caller")

    _validated_payload_or_raise(payload)
    spec_id, spec_version = resolve_spec_pin(db, payload)
    # 缺省 pin 在解析时点回写进 payload —— 之后 latest 前移不影响本 pin。
    payload = dict(payload)
    payload["design_spec_version"] = spec_version

    if parent_version_id is not None:
        parent = db.get(CartographyMapSeriesVersion, parent_version_id)
        if parent is None:
            raise MapSeriesNotFoundError("parent_version not found")
        if str(parent.series_id) != str(series_id):
            # 跨系列 parent 拒绝（同 #1411 纪律）
            raise MapSeriesValidationError(
                "parent_version_id must belong to the same map series"
            )
        if can_read_parent is not None and not can_read_parent(series):
            raise MapSeriesForbiddenError(
                "parent version belongs to a series not visible to this caller"
            )
        _assert_acyclic(db, parent_version_id)

    from sqlalchemy.exc import IntegrityError

    row = None
    for attempt in range(_MAX_VERSION_CREATE_ATTEMPTS):
        latest = (
            db.query(CartographyMapSeriesVersion)
            .filter_by(series_id=series_id)
            .order_by(CartographyMapSeriesVersion.version.desc())
            .first()
        )
        next_version = (latest.version + 1) if latest is not None else 1
        candidate = CartographyMapSeriesVersion(
            org_id=series.org_id,
            series_id=str(series_id)[:36],
            version=next_version,
            payload=payload,
            parent_version_id=parent_version_id,
            structural_digest=structural_digest(payload),
            created_by=(str(created_by)[:255] if created_by else None),
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
        except IntegrityError:
            logger.info(
                "map series %s version %s conflict (attempt %d); retrying",
                series_id, next_version, attempt + 1,
            )
            continue
        row = candidate
        break
    if row is None:
        raise MapSeriesConflictError(
            f"concurrent version creation for map series '{series_id}'; retry"
        )
    # 主行前移到 effective（深合并结果），并同步 pin 解析时点记录。
    effective, _chain = resolve_payload(db, row)
    series.payload = effective
    series.current_version = next_version
    series.structural_digest = structural_digest(effective)
    series.design_spec_id = spec_id
    series.spec_version = spec_version
    db.commit()
    return row


def _assert_acyclic(db: Any, parent_version_id: str) -> None:
    """深度帽 + 防御性环守卫（同 design_specs.versioning 纪律）。"""
    seen: Set[str] = set()
    cursor: Optional[str] = parent_version_id
    depth = 0
    while cursor is not None and depth <= _MAX_CHAIN_DEPTH:
        if cursor in seen:
            raise MapSeriesValidationError("inheritance cycle detected")
        seen.add(cursor)
        node = db.get(CartographyMapSeriesVersion, cursor)
        if node is None:
            break
        cursor = node.parent_version_id
        depth += 1
    if depth > _MAX_CHAIN_DEPTH:
        raise MapSeriesValidationError(f"inheritance chain > {_MAX_CHAIN_DEPTH}")


def resolve_payload(
    db: Any, version: CartographyMapSeriesVersion
) -> Tuple[Dict[str, Any], List[str]]:
    """版本 → effective payload（沿 parent 链深合并，子覆盖父）+ 链上 series id。"""
    from app.services.templates.versioning import deep_merge

    chain: List[CartographyMapSeriesVersion] = []
    seen: Set[str] = set()
    cursor: Optional[CartographyMapSeriesVersion] = version
    while cursor is not None and len(chain) <= _MAX_CHAIN_DEPTH:
        if cursor.id in seen:
            raise MapSeriesValidationError("inheritance cycle detected")
        seen.add(cursor.id)
        chain.append(cursor)
        cursor = (
            db.get(CartographyMapSeriesVersion, cursor.parent_version_id)
            if cursor.parent_version_id
            else None
        )
    if cursor is not None:
        raise MapSeriesValidationError(f"inheritance chain > {_MAX_CHAIN_DEPTH}")
    effective: Dict[str, Any] = {}
    for node in reversed(chain):
        if isinstance(node.payload, dict):
            effective = deep_merge(effective, node.payload)
    return effective, [n.series_id for n in reversed(chain)]


def get_series(db: Any, series_id: str) -> CartographyMapSeries:
    series = db.get(CartographyMapSeries, series_id)
    if series is None:
        raise MapSeriesNotFoundError(f"map series '{series_id}' not found")
    return series


def get_version(
    db: Any, series_id: str, version: Optional[int] = None
) -> CartographyMapSeriesVersion:
    stmt = db.query(CartographyMapSeriesVersion).filter_by(series_id=series_id)
    row = (
        stmt.order_by(CartographyMapSeriesVersion.version.desc()).first()
        if version is None
        else stmt.filter_by(version=int(version)).first()
    )
    if row is None:
        raise MapSeriesNotFoundError("map series version not found")
    return row


def list_series(
    db: Any,
    user_id: Optional[str],
    org_id: Any,
    *,
    limit: int = 50,
) -> List[CartographyMapSeries]:
    """scope 内列表（builtin ∪ creator ∪ org；按 updated_at 倒序有界）。"""
    return (
        db.query(CartographyMapSeries)
        .filter(map_series_scope_clause(user_id, org_id))
        .order_by(CartographyMapSeries.updated_at.desc())
        .limit(max(1, min(int(limit), 200)))
        .all()
    )


def deprecate_version(
    db: Any,
    series_id: str,
    version: Optional[int],
    *,
    note: str = "",
    can_write: Optional[Callable[[Any], bool]] = None,
) -> CartographyMapSeriesVersion:
    """失效标记（软废弃 —— 兼容读取继续可用，响应带标记）。"""
    series = get_series(db, series_id)
    if can_write is not None and not can_write(series):
        raise MapSeriesForbiddenError(f"map series '{series_id}' is not writable by this caller")
    row = get_version(db, series_id, version)
    row.deprecated_at = datetime.now(timezone.utc)
    row.deprecation_note = str(note or "")[:500]
    db.commit()
    return row


def retire_series(
    db: Any,
    series_id: str,
    *,
    note: str = "",
    can_write: Optional[Callable[[Any], bool]] = None,
) -> CartographyMapSeries:
    """系列整体退役（软 —— 成员会话不受影响，只撤系列计划面）。"""
    series = get_series(db, series_id)
    if can_write is not None and not can_write(series):
        raise MapSeriesForbiddenError(f"map series '{series_id}' is not writable by this caller")
    series.status = "retired"
    series.retired_at = datetime.now(timezone.utc)
    series.retirement_note = str(note or "")[:500]
    db.commit()
    return series


def publish_series(
    db: Any,
    series_id: str,
    *,
    can_write: Optional[Callable[[Any], bool]] = None,
) -> CartographyMapSeries:
    """draft → published（发布门：当前 effective payload 校验零 violation）。"""
    series = get_series(db, series_id)
    if can_write is not None and not can_write(series):
        raise MapSeriesForbiddenError(f"map series '{series_id}' is not writable by this caller")
    report = validate_map_series_payload(series.payload or {})
    if not report["ok"]:
        raise MapSeriesValidationError(
            "cannot publish map series with violations",
            violations=report["violations"],
        )
    series.status = "published"
    db.commit()
    return series


__all__ = [
    "map_series_scope_clause",
    "map_series_visible",
    "map_series_can_write",
    "resolve_spec_pin",
    "create_series",
    "upsert_member",
    "remove_member",
    "create_version",
    "resolve_payload",
    "get_series",
    "get_version",
    "list_series",
    "deprecate_version",
    "retire_series",
    "publish_series",
]
