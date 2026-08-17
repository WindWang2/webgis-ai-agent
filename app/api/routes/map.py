"""
地图导出路由 — 智能制图工作流

导出接口由 Agent 指令 `export_thematic_map` 触发，
接收前端 Canvas 合成结果并持久化。支持 PNG 和标准 PDF 制图输出。
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, Any
import asyncio
import json  # noqa: F401  (kept: tests/test_event_loop_offload_427 monkeypatches map_mod.json.dumps)
import logging
import os
import uuid
import time
import tempfile
from fastapi.responses import FileResponse
from app.core.config import settings
from app.core.auth import get_current_user
from app.lib.geojson_serializer import serialize_geojson as _serialize_geojson

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
# （exports 表含 user_id 外键）。#616：owner 缺失（LRU 重启清空 + 侧车丢失）
# 时下载路由 fail-closed（404），不再对全体认证用户放行。
# PERF-F6: bounded LRU (was an unbounded process-lifetime dict — one entry
# per export forever). Rereads fall back to the .owner sidecar file.
from collections import OrderedDict as _OD

_EXPORT_OWNERS: "dict[str, str]" = _OD()
_EXPORT_OWNERS_MAX = 2048


def _export_owners_remember(filename: str, owner: str) -> None:
    _EXPORT_OWNERS[filename] = owner
    _EXPORT_OWNERS.move_to_end(filename)
    while len(_EXPORT_OWNERS) > _EXPORT_OWNERS_MAX:
        _EXPORT_OWNERS.popitem(last=False)


def _set_export_owner(filename: str, user_id: str) -> None:
    """记录文件所有权，并在 EXPORT_DIR 下持久化 .owner 侧车文件以支持多 worker 进程环境。"""
    _export_owners_remember(filename, user_id)
    meta_path = os.path.join(EXPORT_DIR, f"{filename}.owner")
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(user_id)
    except Exception as e:
        logger.warning(f"Failed to write export owner sidecar: {e}")


def _get_export_owner(filename: str) -> Optional[str]:
    """读取文件所有者，优先从内存 _EXPORT_OWNERS 获取，没有则读取 .owner 侧车文件。"""
    if filename in _EXPORT_OWNERS:
        return _EXPORT_OWNERS[filename]
    meta_path = os.path.join(EXPORT_DIR, f"{filename}.owner")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                owner = f.read().strip()
                _export_owners_remember(filename, owner)
                return owner
        except Exception:
            pass
    return None


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
        # PERF #427: defusedxml DOM 解析最多 50MB —— 在 worker 线程执行，
        # 避免阻塞事件循环上所有并发 SSE 流（#386 同类遗留）。
        if ext == ".svg":
            content = await asyncio.to_thread(_sanitize_svg, content)

        # 写入临时文件再原子移动，防止进程崩溃留下残缺文件 —— 同步写盘移出
        # 事件循环（#592：≤50MB 写入内联在 async def 会冻结全部并发流）。
        await asyncio.to_thread(_persist_export_file, filename, content, ext)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Export failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="保存导出图失败")

    # 审计 P0：记录文件所有权，防止 IDOR
    _set_export_owner(filename, _user.get("user_id", "unknown"))

    download_url = f"/api/v1/export/download/{filename}"
    return {
        "success": True,
        "filename": filename,
        "url": download_url,
        "message": "地图制品已成功保存",
    }


def _render_pdf_to_file(
    filename: str, content: bytes,
    title: Optional[str], subtitle: Optional[str],
    author: Optional[str], scale_text: Optional[str],
) -> None:
    """reportlab 渲染 + 同步文件写 —— 纯同步 CPU/socket 无关 IO，必须在
    worker 线程执行（计算隔离不变式 1）。ValueError 原样上抛给路由做 400。"""
    from app.lib.cartography.pdf_renderer import generate_map_pdf

    pdf_bytes = generate_map_pdf(
        img_bytes=content,
        title=title,
        subtitle=subtitle,
        author=author,
        scale_text=scale_text,
    )
    pdf_path = os.path.join(EXPORT_DIR, filename)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)


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

        pdf_filename = f"map_export_{int(time.time())}_{uuid.uuid4().hex[:12]}.pdf"
        # 计算隔离不变式 1：reportlab 渲染（含 ≤50MB 图嵌入）+ 同步文件写在
        # worker 线程执行，避免阻塞事件循环上所有并发 SSE 流（#386）。
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, _render_pdf_to_file,
                pdf_filename, content, title, subtitle, author, scale_text,
            )
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF export failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="PDF 生成失败")

    # 审计 P0：记录文件所有权
    _set_export_owner(pdf_filename, _user.get("user_id", "unknown"))

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
    # SEC-10 / #616: _EXPORT_OWNERS 是进程内 dict（重启即空），.owner 侧车
    # 是持久化兜底。两者都拿不到 owner（进程重启 + 侧车写失败/丢失/多 worker
    # NFS 未同步）时 **fail-closed**：拒绝而非对全体认证用户放行 —— 与
    # layer/raster 路由的 fail-closed 语义一致；返回 404（与文件不存在同响应）
    # 不泄漏文件存在性。DB-backed 所有权（exports.user_id）落地后可移除兜底。
    owner = _get_export_owner(safe_filename)
    if owner is None:
        raise HTTPException(status_code=404, detail="地图文件存在性无法验证，拒绝下载")
    if owner != _user.get("user_id"):
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


def _persist_export_file(filename: str, content: bytes, ext: str) -> None:
    """同步 IO：写临时文件 + 原子 replace —— 移出事件循环（#592 与 #427 的
    _write_export_file 同款纪律：上传分支此前把 ≤50MB 的写入内联在 async def，
    慢盘/NFS 上会冻结全部并发 SSE 流）。"""
    with tempfile.NamedTemporaryFile(dir=EXPORT_DIR, delete=False, suffix=ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    os.replace(tmp_path, os.path.join(EXPORT_DIR, filename))


def _write_export_file(filepath: str, content: bytes) -> None:
    """同步文件写 —— 与序列化一样移出事件循环（#427）。"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(content)


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

    # PERF #427: GeoJSON 体无界且可达数十 MB —— 分块序列化在 worker 线程执行
    #（C encoder 整体持有 GIL，to_thread 单次调用仍会阻塞事件循环 ~秒级），
    # 每块仅持有 GIL 数毫秒，事件循环间隙 <100ms（#386 同类遗留）。
    try:
        content = await _serialize_geojson(data)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"GeoJSON 序列化失败: {e}")

    # PERF #427: GeoJSON 导出体此前完全无界（文件上传分支有 50MB 上限）。
    # 对序列化结果施加同一 MAX_EXPORT_SIZE 预算，超限返回 413。
    if len(content) > MAX_EXPORT_SIZE:
        raise HTTPException(status_code=413, detail="GeoJSON 过大，上限 50MB")

    safe_name = os.path.basename(req.filename).replace(" ", "_")
    filename = f"{safe_name}_{uuid.uuid4().hex[:12]}.geojson"
    filepath = os.path.join(EXPORT_DIR, filename)

    await asyncio.to_thread(_write_export_file, filepath, content)

    # 审计 P0：记录文件所有权
    _set_export_owner(filename, _user.get("user_id", "unknown"))

    return {
        "filename": filename,
        "url": f"/api/v1/export/download/{filename}",
        "format": "geojson",
        "message": f"GeoJSON 导出成功 ({len(content)} bytes)",
    }
