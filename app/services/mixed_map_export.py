"""G26 统一混合地图导出（矢量 + 数值栅格 + 标注 + 图例/整饰 → PNG/SVG/PDF）。

一条编译路径（``compile_mapspec_to_svg_detailed`` + 冻结栅格窗口嵌入），
两个交付面（PDF 经 publication 链 / PNG 经 rsvg 能力探测 / SVG 自包含）。
不建平行 export service：冻结复用 G20 纪律，渲染复用瓦片语义，PDF 复用
publication_export，落盘复用既有 export 目录/所有权/签名下载。

诚实纪律：
- 冻结一次 MapSpec 修订；绑定解析后复检，移动即 CAS_SUPERSEDED；
- 默认 ``blocking``：栅格层无法解析 / 存在不可嵌入图层 → typed 拒绝
  （请求「完整地图」不能带遗漏还报 complete）；``allow_partial`` 显式
  选择时携带完整 omitted 清单；
- 产物 manifest（P6）：版本/包含与省略/像素网格/近似性/checksum。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
from typing import Any, Dict, List, Optional

from app.schemas.raster_spec import RasterExpressionError

logger = logging.getLogger(__name__)

#: rsvg-convert 能力探测（进程级缓存；Docker slim 缺席 → PNG typed 拒绝）。
_RSVG_PATH: Optional[str] = None
_RSVG_PROBED = False

MIXED_EXPORT_SCHEMA_VERSION = "mixed_map_export.v1"
SUPPORTED_FORMATS = ("png", "svg", "pdf")


class MixedMapExportError(Exception):
    """typed 混合导出错误（code / message / status_code；HTTP 与 Pi 共用）。"""

    def __init__(self, code: str, message: str, *, status_code: int = 422,
                 details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def _probe_rsvg() -> Optional[str]:
    global _RSVG_PATH, _RSVG_PROBED
    if not _RSVG_PROBED:
        _RSVG_PROBED = True
        _RSVG_PATH = shutil.which("rsvg-convert")
    return _RSVG_PATH


def _checksum(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


async def _frozen_document(
    session_id: str, expected_revision: Optional[int],
) -> Dict[str, Any]:
    """一次读冻结（frozen_layers 同模式：CAS → 单读 → 供绑定后复检）。"""
    from app.services.mapspec_store import mapspec_store
    from app.services.raster_authoring.service import current_revision

    revision = await current_revision(session_id)
    if expected_revision is not None and int(expected_revision) != revision:
        raise MixedMapExportError(
            "CAS_SUPERSEDED",
            f"export revision {expected_revision} != current MapSpec revision {revision}",
            status_code=409,
            details={"expected": expected_revision, "current": revision},
        )
    doc = await mapspec_store.get_mapspec(session_id)
    if not isinstance(doc, dict) or not doc:
        raise MixedMapExportError(
            "SOURCE_UNAVAILABLE", "session has no authoritative MapSpec",
            status_code=404,
        )
    return {"revision": revision, "document": doc}


def _raster_extent_union(bindings: Dict[str, Dict[str, Any]]) -> Optional[List[float]]:
    """绑定声明范围的并集（无显式 bounds 的层不参与 —— 由渲染窗口自理）。"""
    boxes = []
    for b in bindings.values():
        bounds = b.get("source_bounds")
        if isinstance(bounds, list) and len(bounds) == 4:
            try:
                w, s, e, n = (float(v) for v in bounds)
                if w < e and s < n:
                    boxes.append((w, s, e, n))
            except (TypeError, ValueError):
                continue
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _has_geojson_extent(doc: Dict[str, Any]) -> bool:
    sources = doc.get("sources")
    if not isinstance(sources, dict):
        return False
    for src in sources.values():
        if isinstance(src, dict) and src.get("type") in ("geojson", "vector") \
                and isinstance(src.get("inlineData"), dict):
            return True
    return False


def _frame_with_extent(doc: Dict[str, Any],
                       raster_union: Optional[List[float]]) -> Optional[List[float]]:
    """既有帧/视图 extent 优先；无 extent 且只有栅格承载 extent → 用栅格
    并集显式建帧（视图决策在导出服务内，披露进 manifest；不改 MapSpec）。"""
    layout = doc.get("layout") if isinstance(doc.get("layout"), dict) else {}
    frames = layout.get("frames")
    if isinstance(frames, list) and frames:
        for f in frames:
            if isinstance(f, dict) and isinstance(f.get("extent"), list) \
                    and len(f["extent"]) == 4:
                return None  # 已有显式 extent —— 不干预
    if raster_union is None or _has_geojson_extent(doc):
        return None
    return raster_union


def _unsupported_layer_report(
    doc: Dict[str, Any],
    bindings: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """按图层枚举不可嵌入的层（烘焙 raster/外部瓦片/hillshade/悬空源引用/
    未知源类型/无表达 numeric_raster），供 blocking 判定与 omitted 清单 ——
    A19/A20 的完整遗漏事实源（review P2-1：悬空/未知也不得凭空消失）。"""
    out: List[Dict[str, str]] = []
    bound_layers = set(bindings or {})
    sources = doc.get("sources") if isinstance(doc.get("sources"), dict) else {}
    for layer in doc.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        lid = str(layer.get("id") or "")
        if lid in bound_layers:
            continue  # 冻结绑定承载（嵌入成败由编译后复检裁决）
        ltype = str(layer.get("type") or "")
        if ltype == "hillshade":
            out.append({"layer_id": lid, "reason": "hillshade_not_vectorizable"})
            continue
        sid = layer.get("source")
        src = sources.get(sid) if isinstance(sid, str) else None
        if not isinstance(src, dict):
            out.append({"layer_id": lid, "reason": "source_ref_dangling"})
            continue
        stype = str(src.get("type") or "")
        if stype == "numeric_raster":
            # 有源无表达（未提交 raster_style）→ 无法按冻结表达嵌入
            if not isinstance(layer.get("raster_style"), dict):
                out.append({"layer_id": lid,
                            "reason": "numeric_raster_without_expression"})
            continue
        if stype in ("geojson", "vector"):
            continue  # 矢量面 —— 编译链承载
        if stype == "model3d":
            # review Round-2 nit-4：编译链不渲染 3D 资产源 —— 枚举为遗漏
            # （不静默、不冒充已承载）。
            out.append({"layer_id": lid, "reason": "model3d_not_in_publication_chain"})
            continue
        if stype in ("raster", "wms", "wmts", "pmtiles", "data_fabric"):
            tiles = src.get("tiles") or ([src.get("url")] if src.get("url") else [])
            template = tiles[0] if isinstance(tiles, list) and tiles \
                and isinstance(tiles[0], str) else ""
            if template and any(k in template for k in ("{x}", "{y}", "{z}", "http")):
                out.append({"layer_id": lid,
                            "reason": "external_tiles_licence_not_embedded"})
            else:
                out.append({"layer_id": lid,
                            "reason": "raster_source_not_embedded_offline"})
        else:
            out.append({"layer_id": lid, "reason": f"source_type_unsupported:{stype[:32]}"})
    return out


def _post_compile_omissions(
    bindings: Dict[str, Dict[str, Any]],
    raster_layers: List[Dict[str, Any]],
    diagnostics: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """编译后复检（review P1-2）：冻结绑定的层必须真正进产物（embedded）
    或诚实披露无交集（overlap=none）；否则 = 遗漏（窗口渲染失败被编译面
    降级为诊断 —— blocking 不得放行为 complete）。"""
    seen: Dict[str, str] = {}
    window_failed: Dict[str, str] = {}
    for p in raster_layers:
        if not isinstance(p, dict):
            continue
        lid = str(p.get("layer_id") or "")
        if p.get("embedded"):
            seen[lid] = "embedded"
        elif str(p.get("overlap") or "") == "none":
            seen[lid] = "no_overlap"
    for d in diagnostics:
        if not isinstance(d, dict):
            continue
        detail = str(d.get("detail") or "")
        if d.get("code") == "layer_ref_unavailable" \
                and "numeric_raster_window_failed" in detail:
            code = detail.partition("numeric_raster_window_failed:")[2][:40] \
                or "RENDER_FAILED"
            window_failed[str(d.get("layer_id") or "")] = code
    omissions: List[Dict[str, str]] = []
    for lid in bindings:
        if seen.get(lid) in ("embedded", "no_overlap"):
            continue
        omissions.append({
            "layer_id": lid,
            "reason": f"raster_window_failed:{window_failed.get(lid, 'unknown')}"
            if lid in window_failed else "raster_layer_missing_in_output",
        })
    return omissions


async def export_mixed_map(
    session_id: str,
    *,
    fmt: str,
    expected_revision: Optional[int] = None,
    owner_token: Optional[str] = None,
    trusted_dispatch: bool = False,
    target_dpi: int = 300,
    partial_policy: str = "blocking",
    title: str = "",
) -> Dict[str, Any]:
    """冻结 → 绑定 → 统一编译 → {bytes, media_type, extension, manifest}。

    调用方负责受权落盘（HTTP 路由 / Pi 工具经 ``persist_mixed_export``）。
    """
    from app.services.raster_authoring.publication_bindings import (
        freeze_raster_bindings,
    )

    if fmt not in SUPPORTED_FORMATS:
        raise MixedMapExportError(
            "EXPORT_UNSUPPORTED", f"unsupported mixed export format {fmt!r}",
            details={"supported": list(SUPPORTED_FORMATS)},
        )
    if partial_policy not in ("blocking", "allow_partial"):
        raise MixedMapExportError(
            "EXPORT_UNSUPPORTED",
            "partial_policy must be 'blocking' or 'allow_partial'",
        )

    frozen = await _frozen_document(session_id, expected_revision)
    revision = frozen["revision"]
    doc = frozen["document"]

    # on_unresolvable 与 partial_policy 同语义：默认 blocking。
    bound = await freeze_raster_bindings(
        session_id, doc, revision=revision, owner_token=owner_token,
        trusted_dispatch=trusted_dispatch,
        on_unresolvable="disclose" if partial_policy == "allow_partial" else "raise",
    )
    bindings: Dict[str, Dict[str, Any]] = bound["bindings"]
    unbound: List[Dict[str, Any]] = bound["unbound"]
    unsupported = _unsupported_layer_report(doc, bindings)
    has_raster_layers = any(
        isinstance(layer, dict) and isinstance(layer.get("raster_style"), dict)
        for layer in (doc.get("layers") or []))

    omitted = (
        [{"layer_id": u.get("layer_id"), "reason": u.get("code"),
          "message": u.get("message")} for u in unbound]
        + unsupported
    )
    if partial_policy == "blocking" and omitted:
        raise MixedMapExportError(
            "EXPORT_BLOCKED_INCOMPLETE",
            "the export would omit layers; pass partial_policy='allow_partial' "
            "to export with an explicit omission manifest",
            status_code=409,
            details={"omitted": omitted[:12]},
        )

    # 视图：既有帧 extent 优先；纯栅格无 extent → 并集显式建帧（披露）。
    raster_union = _raster_extent_union(bindings)
    synthetic_extent = _frame_with_extent(doc, raster_union)
    compile_doc = doc
    if synthetic_extent is not None:
        compile_doc = dict(doc)
        layout = dict(compile_doc.get("layout") or {})
        frames = list(layout.get("frames") or [])
        frames.insert(0, {"id": "g26-extent", "enabled": True,
                          "extent": synthetic_extent})
        layout["frames"] = frames
        compile_doc["layout"] = layout

    def _recheck_omissions(raster_layers: List[Dict[str, Any]],
                           diagnostics: List[Dict[str, Any]]) -> None:
        """编译后遗漏复检（P1-2）：blocking 放行条件 = 冻结绑定层全部真实
        embedded / no-overlap 披露；allow_partial 则并入清单。"""
        post = _post_compile_omissions(bindings, raster_layers, diagnostics)
        if not post:
            return
        if partial_policy == "blocking":
            raise MixedMapExportError(
                "EXPORT_BLOCKED_INCOMPLETE",
                "raster layers failed to embed at compile time; pass "
                "partial_policy='allow_partial' for an explicit omission manifest",
                status_code=409,
                details={"omitted": post[:12]},
            )
        omitted.extend(post)
        manifest["partial"] = True
        manifest["omitted_layers"] = omitted

    manifest: Dict[str, Any] = {
        "schema_version": MIXED_EXPORT_SCHEMA_VERSION,
        "format": fmt,
        "session_id": session_id,
        "mapspec_revision": revision,
        "partial": bool(omitted),
        "omitted_layers": omitted,
        "raster_layers": [],
        "view": {
            "synthetic_extent": synthetic_extent,
            "source": "frames_or_raster_union" if synthetic_extent else "spec",
        },
        "target_dpi": int(target_dpi) if fmt == "pdf" else None,
        "renderer": {},
    }

    if fmt == "pdf":
        artifact = await asyncio.to_thread(
            _export_pdf, compile_doc, bindings, target_dpi, title, manifest,
            has_raster_layers, _recheck_omissions)
    elif fmt == "svg":
        artifact = await asyncio.to_thread(
            _export_svg, compile_doc, bindings, manifest, has_raster_layers,
            synthetic_extent, _recheck_omissions)
    else:
        artifact = await asyncio.to_thread(
            _export_png, compile_doc, bindings, manifest, has_raster_layers,
            synthetic_extent, _recheck_omissions)
    return artifact


def _pdf_options_payload(bindings: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """绑定 → worker 子进程 options（JSON 可序列化）。"""
    return {"raster_bindings": bindings}


def _export_pdf(doc: Dict[str, Any], bindings: Dict[str, Any],
                target_dpi: int, title: str, manifest: Dict[str, Any],
                has_raster_layers: bool,
                recheck: Any = None) -> Dict[str, Any]:
    from app.services.publication_render_worker import render_publication_bounded

    try:
        result = render_publication_bounded(
            doc, title=title or "WebGIS AI Agent 混合专题地图",
            target_dpi=target_dpi,
            **_pdf_options_payload(bindings) if bindings else {},
        )
    except Exception as exc:
        raise _map_pdf_error(exc) from exc
    manifest["raster_layers"] = [
        {k: v for k, v in entry.items()} for entry in (result.raster_layers or [])
    ]
    manifest["renderer"] = {
        "engine": "weasyprint",
        "pages": result.page_count,
        "frames_rendered": result.frames_rendered,
        "frames_skipped": result.frames_skipped,
        "layout_version": result.layout_version,
        "spec_fingerprint": result.spec_fingerprint,
        "font_cjk": result.font_cjk,
        "effective_dpi": result.target_dpi,
    }
    manifest["diagnostics"] = [d for d in (result.diagnostics or [])][:40]
    manifest["checksum"] = _checksum(result.pdf)
    if recheck is not None and bindings:
        # 复检消费**未截断**的回执（manifest 展示面才截 24 条 —— review
        # Round-2 nit-1：>24 放置时截断表会把在场层误判为缺席 → 假 blocking）。
        recheck(list(result.raster_layers or []), manifest["diagnostics"])
    return {
        "bytes": result.pdf, "media_type": "application/pdf",
        "extension": ".pdf", "manifest": manifest,
    }


def _map_pdf_error(exc: Exception) -> Exception:
    from app.services.publication_export import (
        PublicationBusyError,
        PublicationUnavailableError,
    )
    from app.lib.cartography.mapspec_schema import MapSpecSchemaError

    if isinstance(exc, (PublicationBusyError, PublicationUnavailableError)):
        return MixedMapExportError(
            "VECTOR_PDF_UNAVAILABLE" if isinstance(exc, PublicationUnavailableError)
            else "VECTOR_PDF_BUSY",
            str(exc), status_code=503 if isinstance(exc, PublicationUnavailableError) else 429,
        )
    if isinstance(exc, MapSpecSchemaError):
        return MixedMapExportError(
            str(getattr(exc, "code", "mapspec_invalid")), str(exc), status_code=400)
    if isinstance(exc, RasterExpressionError):
        return MixedMapExportError(exc.code, exc.message,
                                   status_code=exc.status_code,
                                   details=exc.to_dict().get("details"))
    return MixedMapExportError("RENDER_FAILED", f"publication render failed: {exc}",
                               status_code=500)


def _compile_single_svg(doc: Dict[str, Any], bindings: Dict[str, Any],
                        width: int = 1200, height: int = 800,
                        bounds: Any = None) -> Any:
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    return compile_mapspec_to_svg_detailed(
        doc, target_dpi=150, width=width, height=height, padding=24,
        include_chrome=True, bounds=bounds, raster_bindings=bindings or None,
    )


def _export_svg(doc: Dict[str, Any], bindings: Dict[str, Any],
                manifest: Dict[str, Any], has_raster_layers: bool,
                synthetic_extent: Any = None,
                recheck: Any = None) -> Dict[str, Any]:
    if not has_raster_layers:
        raise MixedMapExportError(
            "EXPORT_UNSUPPORTED",
            "no numeric raster layer in the frozen MapSpec; "
            "use the vector publication export instead",
        )
    # review P1-4：编译器不读 layout.frames extent —— 合成范围必须显式
    # 作为 bounds 传入（否则纯栅格导出退化为 -180..180 世界视图）。
    comp = _compile_single_svg(doc, bindings, bounds=synthetic_extent)
    svg = comp.svg
    manifest["raster_layers"] = [dict(p) for p in (comp.raster_placements or [])]
    manifest["renderer"] = {
        "engine": "mapspec-to-svg",
        "feature_count": comp.feature_count,
        "embedded": "base64 PNG data URI (offline, no session URL)",
    }
    manifest["diagnostics"] = [d for d in (comp.diagnostics or [])][:40]
    manifest["checksum"] = _checksum(svg.encode("utf-8"))
    if recheck is not None and bindings:
        recheck(manifest["raster_layers"], manifest["diagnostics"])
    return {
        "bytes": svg.encode("utf-8"), "media_type": "image/svg+xml",
        "extension": ".svg", "manifest": manifest,
    }


def _export_png(doc: Dict[str, Any], bindings: Dict[str, Any],
                manifest: Dict[str, Any], has_raster_layers: bool,
                synthetic_extent: Any = None,
                recheck: Any = None) -> Dict[str, Any]:
    import subprocess
    import tempfile
    from pathlib import Path

    if not has_raster_layers:
        raise MixedMapExportError(
            "EXPORT_UNSUPPORTED",
            "no numeric raster layer in the frozen MapSpec; "
            "use the vector publication export instead",
        )
    rsvg = _probe_rsvg()
    if rsvg is None:
        raise MixedMapExportError(
            "PNG_RASTERIZER_UNAVAILABLE",
            "server-side SVG rasterizer (rsvg-convert) is not installed; "
            "export svg or pdf, or use the browser canvas export",
            status_code=503,
        )
    comp = _compile_single_svg(doc, bindings, width=1600, height=1100,
                               bounds=synthetic_extent)
    with tempfile.TemporaryDirectory(prefix="g26-png-") as tmp:
        out = Path(tmp) / "out.png"
        proc = subprocess.run(
            [rsvg, "-w", "1600", "-h", "1100", "-o", str(out), "--keep-aspect-ratio", "-"],
            input=comp.svg.encode("utf-8"), capture_output=True, timeout=120)
        if proc.returncode != 0 or not out.is_file():
            raise MixedMapExportError(
                "RENDER_FAILED",
                f"svg rasterization failed: {proc.stderr[:200]!r}",
                status_code=502,
            )
        png = out.read_bytes()
    manifest["raster_layers"] = [dict(p) for p in (comp.raster_placements or [])]
    manifest["renderer"] = {
        "engine": "mapspec-to-svg+rsvg-convert",
        "pixels": "1600x1100",
        "feature_count": comp.feature_count,
    }
    manifest["diagnostics"] = [d for d in (comp.diagnostics or [])][:40]
    manifest["checksum"] = _checksum(png)
    if recheck is not None and bindings:
        recheck(manifest["raster_layers"], manifest["diagnostics"])
    return {
        "bytes": png, "media_type": "image/png",
        "extension": ".png", "manifest": manifest,
    }


async def persist_mixed_export(
    session_id: str, artifact: Dict[str, Any], *, principal: str,
    user_id: Optional[str] = None, owner_token: Optional[str] = None,
    db: Any = None,
) -> Dict[str, Any]:
    """受权落盘（复用既有 export 目录/所有权/原子写/签名下载 + lineage）。"""
    import time
    import uuid

    from app.api.routes.map import _persist_export_file, _set_export_owner
    from app.core.signing import sign_path

    filename = (
        f"mixed_map_{int(time.time())}_{uuid.uuid4().hex[:10]}"
        f"{artifact['extension']}"
    )
    await asyncio.to_thread(
        _persist_export_file, filename, artifact["bytes"], artifact["extension"])
    manifest = artifact["manifest"]
    manifest["filename"] = filename
    sidecar = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    manifest_name = f"{filename}.manifest.json"
    await asyncio.to_thread(
        _persist_export_file, manifest_name, sidecar, ".json")
    # 下载路由对未知 owner fail-closed —— manifest sidecar 同名注册所有权
    # （与成品同 principal；签名 URL 另行签发）。
    await asyncio.to_thread(_set_export_owner, filename, principal)
    await asyncio.to_thread(_set_export_owner, manifest_name, principal)

    # lineage：best-effort 增值记录（db 在场才执行 —— 无 DB 上下文时
    # record_export_lineage 自身 fail-closed 跳过）。
    if db is not None:
        try:
            from app.services.export_lineage import record_export_lineage

            await record_export_lineage(
                session_id,
                filename=filename, ext=artifact["extension"],
                user_id=user_id or "unknown", db=db, owner_token=owner_token,
                title=f"mixed map export ({manifest.get('format')})",
                vector=manifest.get("format") == "pdf",
                pages=int((manifest.get("renderer") or {}).get("pages") or 0),
                target_dpi=int(manifest.get("target_dpi") or 0) or 0,
                degradation_codes=[
                    str(d.get("code")) for d in (manifest.get("diagnostics") or [])
                    if isinstance(d, dict) and d.get("code")
                ][:12],
                layout_version=str(
                    (manifest.get("renderer") or {}).get("layout_version") or ""),
                spec_fingerprint=str(
                    (manifest.get("renderer") or {}).get("spec_fingerprint") or ""),
            )
        except Exception:  # noqa: BLE001 — 增值披露绝不阻断交付
            logger.warning("[g26] lineage recording failed", exc_info=True)

    download_path = f"/api/v1/export/download/{filename}"
    exp, sig = sign_path(download_path, ttl_seconds=3600)
    manifest_path = f"/api/v1/export/download/{filename}.manifest.json"
    m_exp, m_sig = sign_path(manifest_path, ttl_seconds=3600)
    return {
        "success": True,
        "filename": filename,
        "download_url": f"{download_path}?exp={exp}&sig={sig}",
        "manifest_url": f"{manifest_path}?exp={m_exp}&sig={m_sig}",
        "media_type": artifact["media_type"],
        "bytes": len(artifact["bytes"]),
        "manifest": {k: v for k, v in manifest.items() if k != "filename"},
    }


__all__ = [
    "MIXED_EXPORT_SCHEMA_VERSION",
    "SUPPORTED_FORMATS",
    "MixedMapExportError",
    "export_mixed_map",
    "persist_mixed_export",
]
