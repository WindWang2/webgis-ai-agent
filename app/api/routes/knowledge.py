"""
知识库管理 API - RAG 向量检索增强
支持文档上传、分块、向量化、语义搜索
"""
import os
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.api_response import ApiResponse, ErrCode
from app.services import rag_service

router = APIRouter(prefix="/knowledge", tags=["知识库管理"])

# ── Schemas ──────────────────────────────────────────────────────────


class AddDocumentRequest(BaseModel):
    title: str
    content: str
    file_type: str = "text"  # text/markdown/json


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    document_id: Optional[str] = None


class DeleteRequest(BaseModel):
    document_id: str


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/documents", response_model=ApiResponse)
async def add_document(request: AddDocumentRequest, db: Session = Depends(get_db)):
    """上传文档到知识库，执行向量嵌入"""
    if not request.content.strip():
        return ApiResponse.fail(
            code=ErrCode.VALIDATE_ERROR,
            message="文档内容不能为空"
        )

    result = await rag_service.add_document(
        title=request.title,
        content=request.content,
        file_type=request.file_type,
    )

    if "error" in result:
        return ApiResponse.fail(
            code=ErrCode.SERVER_ERROR,
            message=f"文档上传失败: {result['error']}"
        )

    return ApiResponse.ok(
        data={
            "document_id": result["document_id"],
            "chunk_count": result["chunk_count"],
            "status": result["status"],
        },
        message="文档已添加到知识库"
    )


@router.get("/documents", response_model=ApiResponse)
async def list_documents(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """列出知识库中的文档"""
    result = await rag_service.list_documents(limit=limit, offset=offset)
    return ApiResponse.ok(data=result)


@router.get("/search", response_model=ApiResponse)
async def semantic_search(
    q: str = Query(..., description="查询文本"),
    top_k: int = Query(5, ge=1, le=20),
    document_id: Optional[str] = Query(None, description="限定文档ID"),
):
    """向量语义搜索"""
    if not q.strip():
        return ApiResponse.fail(
            code=ErrCode.VALIDATE_ERROR,
            message="查询文本不能为空"
        )

    results = await rag_service.semantic_search(
        query=q,
        top_k=top_k,
        document_id=document_id,
    )

    return ApiResponse.ok(data={"results": results})


@router.delete("/document/{document_id}", response_model=ApiResponse)
async def delete_document(document_id: str, db: Session = Depends(get_db)):
    """删除指定文档"""
    success = await rag_service.delete_document(document_id)

    if not success:
        return ApiResponse.fail(
            code=ErrCode.SERVER_ERROR,
            message="删除失败"
        )

    return ApiResponse.ok(message="文档已删除")


@router.post("/retrieve-context", response_model=ApiResponse)
async def retrieve_context(request: SearchRequest):
    """为对话引擎提供的上下文检索接口"""
    context = await rag_service.retrieve_context(
        query=request.query,
        top_k=request.top_k or 3,
    )
    return ApiResponse.ok(data={"context": context})


__all__ = ["router"]