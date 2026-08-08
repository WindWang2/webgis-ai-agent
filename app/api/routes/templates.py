"""
地图制图模板 API 路由 - 提供模板 CRUD (另存为模板、列表查询、删除用户模板)
"""
import uuid
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
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
    kind: Optional[str] = Query(None, description="按类别过滤: basemap, symbology, layout, thematic"),
    q: Optional[str] = Query(None, description="搜索关键词 (匹配名称、描述或关键字)"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    获取模板列表 (合并数据库内置与用户保存的模板)
    """
    stmt = select(CartographyTemplate)
    if kind:
        stmt = stmt.where(CartographyTemplate.kind == kind)
    
    result = await db.execute(stmt)
    db_templates = result.scalars().all()
    
    db_dicts = [_template_to_dict(t) for t in db_templates]
    db_ids = {t["id"] for t in db_dicts}

    seed_list = list(SEED_TEMPLATES)
    if kind:
        seed_list = [t for t in seed_list if t.get("kind") == kind]
    seed_dicts = [t for t in seed_list if t.get("id") not in db_ids]

    results = seed_dicts + db_dicts

    if q:
        keyword = q.lower()
        results = [
            t for t in results
            if keyword in t.get("name", "").lower()
            or keyword in (t.get("description") or "").lower()
            or any(keyword in kw.lower() for kw in t.get("keywords", []))
        ]

    return results


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

    user_id = _user.get("user_id") if isinstance(_user, dict) else None
    template_id = f"tmpl_user_{uuid.uuid4().hex[:8]}"
    tmpl = CartographyTemplate(
        id=template_id,
        creator_id=user_id,
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


