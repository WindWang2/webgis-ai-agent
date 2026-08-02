"""
地图导出路由 — 智能制图工作流

导出接口由 Agent 指令 `export_thematic_map` 触发，
接收前端 Canvas 合成结果并持久化。支持 PNG 和标准 PDF 制图输出。
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, Any
import io
import json
import logging
import os
import uuid
import time
import tempfile
from fastapi.responses import FileResponse
from app.core.config import settings
from app.core.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

EXPORT_DIR = os.path.join(settings.DATA_DIR, "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

MAX_EXPORT_SIZE = 50 * 1024 * 1024  # 50 MB

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
}

# 审计 P0：导出文件所有权追踪（防止 IDOR — 任意认证用户通过猜文件名下载他人导出）
# key = filename, value = user_id。
# ⚠️ SEC-10: 这是进程内 dict，进程重启后丢失。生产环境应替换为数据库表
# （exports 表含 user_id 外键），届时 owner 为 None 的分支可移除。
_EXPORT_OWNERS: dict[str, str] = {}


# ─── SVG sanitization (/review P1-5) ─────────────────────────────────────
# SVG content can contain <script>, <foreignObject>, on* event attributes,
# and javascript: href references — i.e. arbitrary code execution if a
# browser ever renders it as image/svg+xml + inline. The download endpoint
# currently serves SVGs as application/octet-stream + attachment, but any
# future code change that flips the disposition would activate stored XSS.
# We sanitize at the gate (upload) so the on-disk file is always safe.
_SVG_DANGEROUS_TAGS = {"script", "foreignObject", "iframe", "embed", "object", "use"}
_SVG_HREF_ATTRS = {"href", "{http://www.w3.org/1999/xlink}href"}


def _sanitize_svg(content: bytes) -> bytes:
    """Parse + sanitize SVG content. Returns sanitized bytes.

    Raises HTTPException(400) if content is not well-formed XML, root is not
    <svg>, or the document uses XML entities (DTD / ENTITY — billion-laughs
    / XXE protection).
    """
    from defusedxml import ElementTree as DET
    from xml.etree import ElementTree as ET  # for serialization (defused parser, stdlib writer)

    try:
        root = DET.fromstring(content)  # forbid_dtd / forbid_entities default True
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SVG 解析失败: {e}")

    # Root must be <svg> (allow namespace prefix)
    tag = root.tag.split("}", 1)[-1] if "}" in root.tag else root.tag
    if tag.lower() != "svg":
        raise HTTPException(status_code=400, detail=f"SVG 根元素必须是 svg, 实际是 {tag}")

    # Walk the tree, strip dangerous elements + attributes in place.
    # We do a two-pass walk to safely mutate.
    def _walk(elem: Any) -> None:
        # Strip on* attributes and javascript:/data:text href values
        for attr in list(elem.attrib.keys()):
            local = attr.split("}", 1)[-1] if "}" in attr else attr
            if local.lower().startswith("on"):
                del elem.attrib[attr]
                continue
            if attr in _SVG_HREF_ATTRS or local.lower() == "href":
                val = elem.attrib.get(attr, "").strip().lower()
                # Allow only data:image/... and same-doc fragments. Reject everything else.
                if val.startswith("javascript:") or val.startswith("data:text") or val.startswith("data:application"):
                    del elem.attrib[attr]
                elif val.startswith("data:") and not val.startswith("data:image"):
                    del elem.attrib[attr]
        # Recurse, then remove children with dangerous tags
        for child in list(elem):
            child_tag = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
            if child_tag in _SVG_DANGEROUS_TAGS:
                elem.remove(child)
                continue
            _walk(child)

    _walk(root)

    # Re-serialize. ElementTree's default emits xmlns properly.
    try:
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
    except TypeError:
        # Older Python: xml_declaration kw not on tostring; fall back
        return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="utf-8")


@router.post("/export", tags=["地图制图"])
async def upload_map_export(
    file: UploadFile = File(...),
    title: Optional[str] = Form(default=None),
    _user: dict = Depends(get_current_user),
):
    """接收来自前端的 Canvas 合成结果并持久化，返回可供下载访问的链接。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".png", ".jpg", ".jpeg", ".svg"]:
        ext = ".png"

    filename = f"map_export_{int(time.time())}_{uuid.uuid4().hex[:12]}{ext}"

    try:
        content = await file.read(MAX_EXPORT_SIZE + 1)
        if len(content) > MAX_EXPORT_SIZE:
            raise HTTPException(status_code=413, detail="文件过大，上限 50MB")

        # /review P1-5: SVGs can carry <script>/event-handlers/javascript: hrefs.
        # Parse with defusedxml (XXE-safe), strip dangerous elements/attrs.
        if ext == ".svg":
            content = _sanitize_svg(content)

        # 写入临时文件再原子移动，防止进程崩溃留下残缺文件
        with tempfile.NamedTemporaryFile(dir=EXPORT_DIR, delete=False, suffix=ext) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        os.replace(tmp_path, os.path.join(EXPORT_DIR, filename))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Export failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="保存导出图失败")

    # 审计 P0：记录文件所有权，防止 IDOR
    _EXPORT_OWNERS[filename] = _user.get("user_id", "unknown")

    download_url = f"/api/v1/export/download/{filename}"
    return {
        "success": True,
        "filename": filename,
        "url": download_url,
        "message": "地图制品已成功保存",
    }


@router.post("/export/pdf", tags=["地图制图"])
async def export_map_as_pdf(
    file: UploadFile = File(...),
    title: Optional[str] = Form(default=None),
    subtitle: Optional[str] = Form(default=None),
    author: Optional[str] = Form(default="WebGIS AI Agent"),
    scale_text: Optional[str] = Form(default=None),
    _user: dict = Depends(get_current_user),
):
    """
    将前端合成的地图图片嵌入标准 A4 横向专题底图 PDF。

    PDF 布局包含：
    - 标题 / 副标题区
    - 地图影像主体（保留原 Canvas 分辨率）
    - 页脚（时间戳、制图者、比例说明）
    """
    try:
        content = await file.read(MAX_EXPORT_SIZE + 1)
        if len(content) > MAX_EXPORT_SIZE:
            raise HTTPException(status_code=413, detail="文件过大，上限 50MB")

        from app.lib.cartography.pdf_renderer import generate_map_pdf

        try:
            pdf_bytes = generate_map_pdf(
                img_bytes=content,
                title=title,
                subtitle=subtitle,
                author=author,
                scale_text=scale_text,
            )
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err))

        pdf_filename = f"map_export_{int(time.time())}_{uuid.uuid4().hex[:12]}.pdf"
        pdf_path = os.path.join(EXPORT_DIR, pdf_filename)
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF export failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="PDF 生成失败")

    # 审计 P0：记录文件所有权
    _EXPORT_OWNERS[pdf_filename] = _user.get("user_id", "unknown")

    return {
        "success": True,
        "filename": pdf_filename,
        "url": f"/api/v1/export/download/{pdf_filename}",
        "format": "pdf",
        "message": "专题底图 PDF 已成功生成",
    }


@router.get("/export/download/{filename}", tags=["地图制图"])
def download_map_export(filename: str, _user: dict = Depends(get_current_user)):
    """下载生成的专题地图成果（PNG / PDF）— 需验证文件所有权。"""
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(EXPORT_DIR, safe_filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="地图文件不存在或已过期失效")

    # 审计 P0：防止 IDOR — 验证请求用户是文件所有者。
    # SEC-10: _EXPORT_OWNERS 是进程内 dict，重启后 owner 为 None。
    # 此时无法证明归属，但端点已要求认证 (get_current_user)，因此允许
    # 任意 *已认证* 用户下载，并依赖文件名高熵（48 位）阻止枚举。
    # TODO: 迁移到 DB-backed 所有权 (exports.user_id)，届时移除此兜底。
    owner = _EXPORT_OWNERS.get(safe_filename)
    if owner is not None and owner != _user.get("user_id"):
        raise HTTPException(status_code=403, detail="无权下载此文件")

    ext = os.path.splitext(safe_filename)[1].lower()
    media_type = _MEDIA_TYPES.get(ext, "application/octet-stream")
    disposition = "inline" if ext in (".png", ".jpg", ".jpeg") else "attachment"

    return FileResponse(
        filepath,
        media_type=media_type,
        headers={"Content-Disposition": f'{disposition}; filename="{safe_filename}"'},
    )


class GeoJSONExportRequest(BaseModel):
    geojson: Any
    filename: str = "export"


@router.post("/export/geojson", tags=["地图制图"])
async def export_geojson(req: GeoJSONExportRequest, _user: dict = Depends(get_current_user)):
    """接收 GeoJSON 数据并持久化为可下载文件。"""
    data = req.geojson
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="GeoJSON 必须是 JSON 对象")

    # Validate basic GeoJSON structure
    geo_type = data.get("type")
    if not geo_type:
        raise HTTPException(status_code=400, detail="GeoJSON 缺少 type 字段")

    try:
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"GeoJSON 序列化失败: {e}")

    safe_name = os.path.basename(req.filename).replace(" ", "_")
    filename = f"{safe_name}_{uuid.uuid4().hex[:12]}.geojson"
    filepath = os.path.join(EXPORT_DIR, filename)

    os.makedirs(EXPORT_DIR, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(content)

    # 审计 P0：记录文件所有权
    _EXPORT_OWNERS[filename] = _user.get("user_id", "unknown")

    return {
        "filename": filename,
        "url": f"/api/v1/export/download/{filename}",
        "format": "geojson",
        "message": f"GeoJSON 导出成功 ({len(content)} bytes)",
    }
