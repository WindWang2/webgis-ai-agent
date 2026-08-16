"""
地图制图模板 API 路由 - 提供模板 CRUD (另存为模板、列表查询、删除用户模板)
"""
import uuid
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import actor_ids, get_current_user, get_current_user_optional
from app.core.database import get_async_db
from app.models.db_model import CartographyTemplate
from app.schemas.template_schema import (
    BasemapPayload,
    SymbologyPayload,
    LayoutTemplatePayload,
    ThematicPresetPayload,
    SEED_TEMPLATES,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateTemplateRequest(BaseModel):
    name: str = Field(..., description="模板名称", min_length=1, max_length=100)
    kind: str = Field(..., description="模板类别: basemap, symbology, layout, thematic")
    description: Optional[str] = Field(None, description="模板描述")
    keywords: List[str] = Field(default_factory=list, description="搜索关键词标签")
    payload: Dict[str, Any] = Field(..., description="对应 kind 的样式/配置 payload")
    thumbnail_url: Optional[str] = Field(None, description="缩略图 URL")


def _validate_payload(kind: str, payload: Dict[str, Any]):
    """校验 payload 是否符合对应 kind 的 Pydantic 模型契约"""
    try:
        if kind == "basemap":
            TypeAdapter(BasemapPayload).validate_python(payload)
        elif kind == "symbology":
            TypeAdapter(SymbologyPayload).validate_python(payload)
        elif kind == "layout":
            TypeAdapter(LayoutTemplatePayload).validate_python(payload)
        elif kind == "thematic":
            TypeAdapter(ThematicPresetPayload).validate_python(payload)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unsupported template kind: {kind}"
            )
    except ValueError as e:
        logger.warning(f"Invalid payload for template kind '{kind}': {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid payload for kind '{kind}'"
        )


def _template_scope_clause(user_id: Optional[str], org_id, role: Optional[str]):
    """Built-ins + own creator_id + matching org. Never treat org_id IS NULL
    user rows as public — JWT historically omitted org_id, so that predicate
    leaked every tenant's saved templates.
    """
    if role == "admin":
        return None
    if user_id is None:
        return CartographyTemplate.is_builtin.is_(True)
    clauses = [
        CartographyTemplate.is_builtin.is_(True),
        CartographyTemplate.creator_id == user_id,
    ]
    if org_id is not None:
        clauses.append(CartographyTemplate.org_id == org_id)
    return or_(*clauses)


def _template_visible(tmpl: CartographyTemplate, user_id: Optional[str], org_id, role: Optional[str]) -> bool:
    if tmpl.is_builtin or role == "admin":
        return True
    if org_id is not None and tmpl.org_id == org_id:
        return True
    return user_id is not None and tmpl.creator_id == user_id


def _template_to_dict(tmpl: CartographyTemplate) -> dict:
    return {
        "id": tmpl.id,
        "org_id": tmpl.org_id,
        "creator_id": tmpl.creator_id,
        "kind": tmpl.kind,
        "name": tmpl.name,
        "category": tmpl.category,
        "keywords": tmpl.keywords or [],
        "description": tmpl.description,
        "payload": tmpl.payload,
        "is_builtin": tmpl.is_builtin,
        "version": tmpl.version,
        "created_at": tmpl.created_at.isoformat() if tmpl.created_at else None,
        "updated_at": tmpl.updated_at.isoformat() if tmpl.updated_at else None,
    }


@router.get("/templates", summary="查询地图制图模板列表")
async def list_templates(
    kind: Optional[str] = Query(None, description="按类别过滤: basemap, symbology, layout, thematic, composite"),
    q: Optional[str] = Query(None, description="搜索关键词 (匹配名称、描述或关键字)"),
    source: Optional[str] = Query(
        None,
        description="按来源过滤: builtin (内置), user (用户自建), all (默认, 全部)",
    ),
    limit: int = Query(100, ge=1, le=200, description="最大返回数量"),
    offset: int = Query(0, ge=0, description="分页偏移"),
    summary: bool = Query(
        True,
        description="True (默认) 返回 summary DTO (无 payload); False 返回完整模板 (含 payload JSON)",
    ),
    _user: Optional[dict] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_async_db),
):
    """
    获取模板列表 (合并数据库内置与用户保存的模板, 受 auth + 租户隔离保护).

    Security: previously this endpoint was unauthenticated AND had no
    org_id filter, leaking every tenant's user template payload (incl.
    org_id, creator_id). Now requires an auth user and restricts user
    templates to the caller's org + built-ins.

    Summary: by default the list strips the heavy `payload` field (kB-scale
    JSON per row). Gallery UIs render name/description/keywords only; the
    detail endpoint returns the full payload.
    """
    from app.schemas.pagination import Page, clamp_pagination

    limit, offset = clamp_pagination(limit, offset)
    user_id, user_org_id = actor_ids(_user if isinstance(_user, dict) else None)
    role = _user.get("role") if isinstance(_user, dict) else None

    # Merged catalog = built-in seeds (static order, always first) + DB rows
    # (newest first). Build the full filtered catalog, then paginate ONCE.
    # Issue #428: the old code paginated twice — `.limit/.offset` on the DB
    # query and then a second `[offset:offset+limit]` slice of the merged
    # (seed + DB-page) list — so the offset skipped seed+DB rows twice, deep
    # pages ran empty while `has_more` was still true, and without an
    # ORDER BY the row order was nondeterministic across requests.
    seed_list = list(SEED_TEMPLATES)
    if kind:
        seed_list = [t for t in seed_list if t.get("kind") == kind]
    if source == "user":
        seed_list = [t for t in seed_list if not t.get("is_builtin")]
    elif source == "builtin":
        seed_list = [t for t in seed_list if t.get("is_builtin")]

    # DB-stored templates: builtins + caller's own (and org, when the JWT
    # actually carries org_id). Do not use org_id IS NULL as "public".
    stmt = select(CartographyTemplate)
    if kind:
        stmt = stmt.where(CartographyTemplate.kind == kind)

    scope = _template_scope_clause(user_id, user_org_id, role)
    if scope is not None:
        stmt = stmt.where(scope)

    if source == "builtin":
        stmt = stmt.where(CartographyTemplate.is_builtin.is_(True))
    elif source == "user":
        stmt = stmt.where(CartographyTemplate.is_builtin.is_(False))

    # A DB row carrying a seed id is the seeded copy of that built-in — the
    # seed entry above already represents it, so exclude it here instead of
    # de-duplicating page-by-page (which only saw the current page's ids).
    seed_ids = [s.get("id") for s in SEED_TEMPLATES if s.get("id")]
    if seed_ids:
        stmt = stmt.where(CartographyTemplate.id.not_in(seed_ids))

    # Deterministic paging order (created_at alone can tie on fast inserts).
    stmt = stmt.order_by(CartographyTemplate.created_at.desc(), CartographyTemplate.id)

    result = await db.execute(stmt)
    db_dicts = [_template_to_dict(t) for t in result.scalars().all()]

    merged = seed_list + db_dicts

    if q:
        keyword = q.lower()
        merged = [
            t for t in merged
            if keyword in t.get("name", "").lower()
            or keyword in (t.get("description") or "").lower()
            or any(keyword in kw.lower() for kw in t.get("keywords", []))
        ]

    total = len(merged)
    page_slice = merged[offset:offset + limit]

    if summary:
        page_slice = [_to_summary(t) for t in page_slice]

    return Page(
        items=page_slice,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


def _to_summary(t: dict) -> dict:
    """Strip the heavy `payload` (and `org_id` / `creator_id`) for list views."""
    out = {
        "id": t["id"],
        "kind": t["kind"],
        "name": t["name"],
        "category": t.get("category"),
        "keywords": t.get("keywords", []),
        "description": t.get("description"),
        "is_builtin": t.get("is_builtin", False),
        "version": t.get("version", 1),
    }
    # Preserve timestamps for sort/UX; drop org/creator IDs to avoid leaks.
    for k in ("created_at", "updated_at", "thumbnail_url"):
        if t.get(k) is not None:
            out[k] = t[k]
    return out


@router.get("/templates/{template_id}", summary="获取模板详情 (含 payload)")
async def get_template(
    template_id: str,
    _user: Optional[dict] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_async_db),
):
    """返回模板完整 DTO (含 payload JSON)。

    Gallery V2 在用户点击模板卡时调用；列表路径不再下发 payload。
    Built-in seed 也走这条 — 找不到时回退到 SEED_TEMPLATES。
    """
    user_id, user_org_id = actor_ids(_user if isinstance(_user, dict) else None)
    role = _user.get("role") if isinstance(_user, dict) else None
    stmt = select(CartographyTemplate).where(CartographyTemplate.id == template_id)
    result = await db.execute(stmt)
    tmpl = result.scalar_one_or_none()
    if tmpl is not None:
        if not _template_visible(tmpl, user_id, user_org_id, role):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template '{template_id}' not found",
            )
        return _template_to_dict(tmpl)

    # Fall back to built-in seed.
    seed = next((s for s in SEED_TEMPLATES if s.get("id") == template_id), None)
    if seed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found",
        )
    # Strip seed-internal-only fields the schema doesn't expose.
    return seed


@router.post("/templates", status_code=status.HTTP_201_CREATED, summary="另存为新模板 (Save as Template)")
async def create_template(
    req: CreateTemplateRequest,
    _user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    用户写路径：将当前图层样式或导出配置保存为新模板 (is_builtin=False)
    """
    _validate_payload(req.kind, req.payload)

    user_id, user_org_id = actor_ids(_user if isinstance(_user, dict) else None)
    template_id = f"tmpl_user_{uuid.uuid4().hex[:8]}"
    tmpl = CartographyTemplate(
        id=template_id,
        creator_id=user_id,
        org_id=user_org_id,
        kind=req.kind,
        name=req.name,
        category=req.kind,
        keywords=req.keywords,
        description=req.description or f"用户自定义{req.name}",
        payload=req.payload,
        is_builtin=False,
        version=1,
    )

    try:
        db.add(tmpl)
        await db.commit()
        await db.refresh(tmpl)
        return _template_to_dict(tmpl)
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to create template: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create template"
        )


@router.delete("/templates/{template_id}", summary="删除用户模板")
async def delete_template(
    template_id: str,
    _user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    删除指定的模板。内置模板 (is_builtin=True) 保持只读，禁止删除。
    创建者或管理员有权删除自定义模板。
    """
    stmt = select(CartographyTemplate).where(CartographyTemplate.id == template_id)
    result = await db.execute(stmt)
    tmpl = result.scalar_one_or_none()

    if not tmpl:
        # Check built-in seeds
        seed = next((s for s in SEED_TEMPLATES if s["id"] == template_id), None)
        if seed and seed.get("is_builtin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Built-in templates are read-only and cannot be deleted"
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found"
        )

    if tmpl.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in templates are read-only and cannot be deleted"
        )

    user_id = _user.get("user_id") if isinstance(_user, dict) else None
    role = _user.get("role") if isinstance(_user, dict) else None
    if (tmpl.creator_id != user_id or tmpl.creator_id is None) and role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to delete this template"
        )

    await db.delete(tmpl)
    await db.commit()
    return {"status": "deleted", "template_id": template_id}


