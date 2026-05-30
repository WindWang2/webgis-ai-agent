"""
地图导出路由 — 智能制图工作流

导出接口由 Agent 指令 `export_thematic_map` 触发，
接收前端 Canvas 合成结果并持久化。支持 PNG 和标准 PDF 制图输出。
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
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
    def _walk(elem):
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
):
    """接收来自前端的 Canvas 合成结果并持久化，返回可供下载访问的链接。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".png", ".jpg", ".jpeg", ".svg"]:
        ext = ".png"

    filename = f"map_export_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"

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
        raise HTTPException(status_code=500, detail=f"保存导出图失败: {str(e)}")

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
):
    """
    将前端合成的地图图片嵌入标准 A4 横向专题底图 PDF。

    PDF 布局包含：
    - 标题 / 副标题区
    - 地图影像主体（保留原 Canvas 分辨率）
    - 页脚（时间戳、制图者、比例说明）
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm
        import numpy as np
        from PIL import Image

        content = await file.read(MAX_EXPORT_SIZE + 1)
        if len(content) > MAX_EXPORT_SIZE:
            raise HTTPException(status_code=413, detail="文件过大，上限 50MB")

        img = Image.open(io.BytesIO(content)).convert("RGB")
        img_arr = np.array(img)

        # ── 选择支持 CJK 的字体（如存在） ──
        cjk_keywords = ["cjk", "noto", "source han", "wqy", "simhei",
                        "simsun", "microsoft yahei", "pingfang", "heiti"]
        cjk_font = next(
            (f.name for f in fm.fontManager.ttflist
             if any(kw in f.name.lower() for kw in cjk_keywords)),
            None,
        )
        if not cjk_font:
            logger.warning(
                "[export_map_as_pdf] 未找到 CJK 字体，中文标题/副标题可能会渲染为方块。"
                " 建议安装 Noto CJK、Source Han Sans 或微软雅黑等字体。"
            )
        if cjk_font:
            plt.rcParams["font.family"] = cjk_font

        # ── A4 横向 (297×210 mm ≈ 11.69×8.27 in) ──
        fig = plt.figure(figsize=(11.69, 8.27), facecolor="white")

        map_top = 0.88
        map_bottom = 0.10
        ax_map = fig.add_axes([0.04, map_bottom, 0.92, map_top - map_bottom])
        ax_map.imshow(img_arr, aspect="auto")
        ax_map.axis("off")

        # 边框
        for spine in ax_map.spines.values():
            spine.set_visible(True)
            spine.set_linewidth(0.8)
            spine.set_edgecolor("#cccccc")

        # ── 标题区 ──
        map_title = title or "专题地图"
        fig.text(
            0.5, 0.955, map_title,
            ha="center", va="top",
            fontsize=15, fontweight="bold", color="#1e293b",
        )
        if subtitle:
            fig.text(
                0.5, 0.925, subtitle,
                ha="center", va="top",
                fontsize=10, color="#64748b",
            )

        # ── 页脚 ──
        date_str = time.strftime("%Y-%m-%d")
        footer_parts = [f"制图日期: {date_str}"]
        if author:
            footer_parts.append(f"制图者: {author}")
        if scale_text:
            footer_parts.append(f"比例尺: {scale_text}")
        footer_parts.append("Generated by WebGIS AI Agent")

        fig.text(
            0.5, 0.025, "  |  ".join(footer_parts),
            ha="center", va="bottom",
            fontsize=7, color="#94a3b8", style="italic",
        )

        # ── 保存 PDF ──
        pdf_filename = f"map_export_{int(time.time())}_{uuid.uuid4().hex[:6]}.pdf"
        pdf_path = os.path.join(EXPORT_DIR, pdf_filename)
        fig.savefig(pdf_path, format="pdf", dpi=150, bbox_inches="tight",
                    metadata={
                        "Title": map_title,
                        "Author": author or "WebGIS AI Agent",
                        "Subject": subtitle or "",
                        "Creator": "WebGIS AI Agent",
                    })
        plt.close(fig)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 生成失败: {str(e)}")

    return {
        "success": True,
        "filename": pdf_filename,
        "url": f"/api/v1/export/download/{pdf_filename}",
        "format": "pdf",
        "message": "专题底图 PDF 已成功生成",
    }


@router.get("/export/download/{filename}", tags=["地图制图"])
def download_map_export(filename: str):
    """下载生成的专题地图成果（PNG / PDF）"""
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(EXPORT_DIR, safe_filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="地图文件不存在或已过期失效")

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
async def export_geojson(req: GeoJSONExportRequest):
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
    filename = f"{safe_name}_{uuid.uuid4().hex[:6]}.geojson"
    filepath = os.path.join(EXPORT_DIR, filename)

    os.makedirs(EXPORT_DIR, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(content)

    return {
        "filename": filename,
        "url": f"/api/v1/export/download/{filename}",
        "format": "geojson",
        "message": f"GeoJSON 导出成功 ({len(content)} bytes)",
    }
