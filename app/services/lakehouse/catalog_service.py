"""Lakehouse catalog service — Spatial Lakehouse V7 (ADR-0119, Scope F).

可检索投影的**写入/查询/对账**层（表见 ``app/models/lakehouse_catalog``；
事实源边界见该模型 docstring）。

契约：

- **同步 DAO**（``artifact_revisions`` 同款：只写不 commit，事务边界
  归调用方；REST 层经 ``asyncio.to_thread`` 包装）；
- **upsert 幂等**：唯一键 ``(owner_type, owner_id, content_sha256)``
  —— 同内容重复投影 = 更新不新增；
- **查询有界**：``limit`` ≤200、``total`` 计数扫描 ≤``MAX_TOTAL_SCAN``
  （超出报 ``>=`` 下界 —— 诚实而非全表计数）；无 owner 的查询 typed
  拒绝（不存在全局目录 —— 租户边界）；
- **对账（R0-10）**：``reconcile`` 以 manifest 可解析性为准把孤儿行标记
  revoked（投影漂移收敛；绝不删字节 —— 删除归 GC）；
- **bbox/时间过滤**走索引列（SQL 谓词）；tags 过滤在 SQL 命中的有界
  行集上内存过滤（SQLite 方言无 GIN 的诚实退化 —— 行集受 limit 与
  MAX_TOTAL_SCAN 双闸）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy import func, select, or_, and_, not_

from app.models.lakehouse_catalog import LakehouseCatalogItem

logger = logging.getLogger(__name__)

#: 查询边界（scope F：no unbounded catalog response）。
MAX_LIMIT = 200
MAX_TOTAL_SCAN = 10_000

_BBOX_KEYS = ("minx", "miny", "maxx", "maxy")


class CatalogError(ValueError):
    """catalog 投影契约违例。"""

    code = "LAKEHOUSE_CATALOG_INVALID"


def derive_entry_from_manifest(
    manifest: Mapping[str, Any],
    *,
    owner_type: str,
    owner_id: str,
    content_sha256: str,
    byte_size: int = 0,
    ref: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """manifest → 投影行字段（纯函数；producer/payload 的有界投影）。"""
    if owner_type not in ("session", "project"):
        raise CatalogError(f"invalid owner_type {owner_type!r}")
    payload = manifest.get("payload") or {}
    producer = manifest.get("producer") or {}
    bbox = payload.get("bbox") or payload.get("labeled", {}).get("bbox")
    minx = miny = maxx = maxy = None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            minx, miny, maxx, maxy = (float(v) for v in bbox)
        except (TypeError, ValueError):
            minx = miny = maxx = maxy = None
    time_start = _parse_dt(payload.get("time_start") or payload.get("start_datetime"))
    time_end = _parse_dt(payload.get("time_end") or payload.get("end_datetime"))
    labeled = payload.get("labeled") or {}
    descriptor: Dict[str, Any] = {}
    if labeled:
        descriptor["dims"] = labeled.get("dims")
        descriptor["variables"] = sorted((labeled.get("variables") or {}))
        descriptor["shape"] = labeled.get("shape")
    return {
        "object_id": str(ref or manifest.get("content_sha256") or content_sha256),
        "owner_type": owner_type,
        "owner_id": owner_id,
        "kind": str(manifest.get("kind") or "unknown"),
        "title": title or payload.get("title"),
        "producer_capability": producer.get("capability"),
        "producer_tool": producer.get("tool"),
        "workflow_run_id": workflow_run_id,
        "tags_json": list(tags or payload.get("tags") or []),
        "descriptor_json": descriptor,
        "minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy,
        "time_start": time_start,
        "time_end": time_end,
        "content_sha256": content_sha256,
        "byte_size": int(byte_size or manifest.get("byte_size") or 0),
        "status": "active",
    }


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def upsert_catalog_entry(db, fields: Mapping[str, Any]) -> Dict[str, Any]:
    """幂等投影（唯一键冲突 = 更新既有行；同步 DAO —— 只写不 commit，
    事务边界归调用方）。"""
    import uuid as _uuid

    owner_type = str(fields["owner_type"])
    owner_id = str(fields["owner_id"])
    content_sha256 = str(fields["content_sha256"])
    existing = (
        db.execute(
            select(LakehouseCatalogItem).where(
                LakehouseCatalogItem.owner_type == owner_type,
                LakehouseCatalogItem.owner_id == owner_id,
                LakehouseCatalogItem.content_sha256 == content_sha256,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        for key, value in fields.items():
            if key in ("id", "created_at"):
                continue
            setattr(existing, key, value)
        return {"status": "updated", "id": existing.id,
                "object_id": existing.object_id}
    row = LakehouseCatalogItem(id=str(_uuid.uuid4()), **dict(fields))
    db.add(row)
    db.flush()
    return {"status": "created", "id": row.id, "object_id": row.object_id}


def search_catalog(
    db,
    *,
    owner_type: str,
    owner_id: str,
    kind: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
    time_from: Optional[Any] = None,
    time_to: Optional[Any] = None,
    tags: Optional[Sequence[str]] = None,
    producer: Optional[str] = None,
    include_revoked: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """域内检索（强制分页；结构预算见模块 docstring）。"""
    if owner_type not in ("session", "project") or not owner_id:
        raise CatalogError("search requires an owner scope (session/project)")
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))

    conds = [
        LakehouseCatalogItem.owner_type == owner_type,
        LakehouseCatalogItem.owner_id == owner_id,
    ]
    if not include_revoked:
        conds.append(LakehouseCatalogItem.status == "active")
    if kind:
        conds.append(LakehouseCatalogItem.kind == str(kind))
    if producer:
        like = f"%{str(producer)[:64]}%"
        conds.append(
            LakehouseCatalogItem.producer_capability.ilike(like)
            | LakehouseCatalogItem.producer_tool.ilike(like)
        )
    if bbox is not None:
        if len(bbox) != 4:
            raise CatalogError("bbox must be [minx, miny, maxx, maxy]")
        minx, miny, maxx, maxy = (float(v) for v in bbox)
        conds += [
            LakehouseCatalogItem.minx.is_not(None),
            LakehouseCatalogItem.minx <= maxx,
            LakehouseCatalogItem.maxx >= minx,
            LakehouseCatalogItem.miny <= maxy,
            LakehouseCatalogItem.maxy >= miny,
        ]
    if time_from is not None:
        parsed_from = _parse_dt(time_from)
        if parsed_from is not None:
            has_end = LakehouseCatalogItem.time_end.is_not(None)
            conds.append(or_(
                and_(has_end, LakehouseCatalogItem.time_end >= parsed_from),
                and_(
                    not_(has_end),
                    LakehouseCatalogItem.time_start >= parsed_from,
                ),
            ))
    if time_to is not None:
        parsed_to = _parse_dt(time_to)
        if parsed_to is not None:
            has_start = LakehouseCatalogItem.time_start.is_not(None)
            conds.append(or_(
                and_(has_start, LakehouseCatalogItem.time_start <= parsed_to),
                and_(
                    not_(has_start),
                    LakehouseCatalogItem.time_end <= parsed_to,
                ),
            ))
    base = select(LakehouseCatalogItem).where(*conds)
    rows = (
        db.execute(
            base.order_by(LakehouseCatalogItem.created_at.desc())
            .limit(limit).offset(offset)
        )
    ).scalars().all()
    # 有界计数（超出 MAX_TOTAL_SCAN 报下界 —— 绝不全表 COUNT）。
    total_rows = (db.execute(
        select(func.count())
        .select_from(
            select(LakehouseCatalogItem.id)
            .where(*conds).limit(MAX_TOTAL_SCAN + 1).subquery()
        )
    )).scalar_one()
    total = int(total_rows or 0)
    total_bounded = total > MAX_TOTAL_SCAN
    items = [row.to_dict() for row in rows]
    wanted = [str(t) for t in (tags or [])]
    if wanted:
        # 行集级 tags 过滤（方言退化诚实化：SQL 已命中 ≤ limit 行）。
        items = [
            it for it in items
            if all(t in set(it["tags"]) for t in wanted)
        ]
    return {
        "items": items,
        "count": len(items),
        "total": (f">={total}" if total_bounded else str(total)),
        "total_bounded": total_bounded,
        "limit": limit,
        "offset": offset,
        "next_offset": (
            offset + limit if len(items) == limit and not total_bounded else None
        ),
    }


def revoke_catalog_entries(
    db, *, owner_type: str, owner_id: str, object_ids: Sequence[str]
) -> Dict[str, Any]:
    """撤销（tombstone；既有引用仍可解析，检索默认不可见）。"""
    if not object_ids:
        return {"revoked": [], "unknown": []}
    rows = (
        db.execute(
            select(LakehouseCatalogItem).where(
                LakehouseCatalogItem.owner_type == owner_type,
                LakehouseCatalogItem.owner_id == owner_id,
                LakehouseCatalogItem.object_id.in_(
                    [str(o) for o in object_ids[:200]]
                ),
            )
        )
    ).scalars().all()
    revoked: List[str] = []
    for row in rows:
        row.status = "revoked"
        revoked.append(row.object_id)
    db.flush()
    wanted = {str(o) for o in object_ids}
    return {
        "revoked": sorted(set(revoked)),
        "unknown": sorted(wanted - set(revoked)),
    }


def reconcile_catalog(
    db, *, owner_type: str, owner_id: str, cap: int = 500
) -> Dict[str, Any]:
    """投影对账（R0-10）：manifest 不可解析的 active 行 → revoked。

    有界扫描（cap）；幂等 —— 可反复运行至收敛。绝不删字节。"""
    from app.services.lakehouse.data_object import (
        is_data_object_id,
        resolve_data_object,
    )

    rows = (
        db.execute(
            select(LakehouseCatalogItem).where(
                LakehouseCatalogItem.owner_type == owner_type,
                LakehouseCatalogItem.owner_id == owner_id,
                LakehouseCatalogItem.status == "active",
            ).limit(max(1, min(int(cap), 1000)))
        )
    ).scalars().all()
    reaped: List[str] = []
    checked = 0
    for row in rows:
        checked += 1
        oid = str(row.object_id)
        if not is_data_object_id(oid):
            continue  # ref cursor 形态的活性归 probe 通道（不归本对账）
        if resolve_data_object(oid) is None:
            row.status = "revoked"
            reaped.append(oid)
    return {"checked": checked, "reaped": sorted(set(reaped))}
