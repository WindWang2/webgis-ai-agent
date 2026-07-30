"""
地图制图模板 API 路由 - 提供模板 CRUD (另存为模板、列表查询、删除用户模板)
"""
import uuid
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, TypeAdapter

from app.core.database import SessionLocal
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
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported template kind: {kind}"
            )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid payload for kind '{kind}': {str(e)}"
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
def list_templates(
    kind: Optional[str] = Query(None, description="按类别过滤: basemap, symbology, layout, thematic"),
    q: Optional[str] = Query(None, description="搜索关键词 (匹配名称、描述或关键字)"),
):
    """
    获取模板列表 (合并数据库内置与用户保存的模板)
    """
    db = SessionLocal()
    try:
        query = db.query(CartographyTemplate)
        if kind:
            query = query.filter(CartographyTemplate.kind == kind)
        
        db_templates = query.all()
        
        if db_templates:
            results = [_template_to_dict(t) for t in db_templates]
        else:
            results = list(SEED_TEMPLATES)
            if kind:
                results = [t for t in results if t.get("kind") == kind]

        if q:
            keyword = q.lower()
            results = [
                t for t in results
                if keyword in t.get("name", "").lower()
                or keyword in (t.get("description") or "").lower()
                or any(keyword in kw.lower() for kw in t.get("keywords", []))
            ]

        return results
    finally:
        db.close()


@router.post("/templates", status_code=status.HTTP_201_CREATED, summary="另存为新模板 (Save as Template)")
def create_template(req: CreateTemplateRequest):
    """
    用户写路径：将当前图层样式或导出配置保存为新模板 (is_builtin=False)
    """
    _validate_payload(req.kind, req.payload)

    template_id = f"tmpl_user_{uuid.uuid4().hex[:8]}"
    tmpl = CartographyTemplate(
        id=template_id,
        kind=req.kind,
        name=req.name,
        category=req.kind,
        keywords=req.keywords,
        description=req.description or f"用户自定义{req.name}",
        payload=req.payload,
        is_builtin=False,
        version=1,
    )

    db = SessionLocal()
    try:
        db.add(tmpl)
        db.commit()
        db.refresh(tmpl)
        return _template_to_dict(tmpl)
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create template: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create template: {str(e)}"
        )
    finally:
        db.close()


@router.delete("/templates/{template_id}", summary="删除用户模板")
def delete_template(template_id: str):
    """
    删除指定的模板。内置模板 (is_builtin=True) 保持只读，禁止删除。
    """
    db = SessionLocal()
    try:
        tmpl = db.query(CartographyTemplate).filter(CartographyTemplate.id == template_id).first()
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

        db.delete(tmpl)
        db.commit()
        return {"status": "deleted", "template_id": template_id}
    finally:
        db.close()
