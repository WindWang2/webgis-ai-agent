"""G34 纸面作者服务 —— UI（REST）与 Pi（ToolRegistry）**唯一共用入口**。

所有写都经 ``apply_gis_mutation(SetLayoutIntent(print_layout=...))``，
复用既有 CAS / 用户锁 / checkpoint / provenance / 撤销栈；不建第二套
MapSpec store。回执 refs-only：不返回整页像素、字体 bytes 或栅格数组。

终态词表显式保留 ``unknown / not_evaluated / partial / blocked / cancelled``；
``queued / pending / tool-returned / agent_settled`` 绝不当作完成。
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.lib.cartography.print_layout_schema import (
    MAX_ATLAS_PAGES, PRINT_LAYOUT_SCHEMA_VERSION, PRINT_RENDER_PATH,
    empty_print_layout, validate_print_layout,
)
from app.services.print_layout_authoring import (
    operations, template as template_mod,
)
from app.services.print_layout_authoring.compile import (
    build_draw_plan, serializable_receipt,
)
from app.services.print_layout_authoring.fonts import (
    FONT_IDS, font_asset_version, font_resource_status,
)
from app.services.print_layout_authoring.pages import (
    page_plan, sync_pages, planner_pages,
)
from app.services.print_layout_authoring.units import (
    clamp_dpi, mm_to_canvas_units, mm_to_sample_px, units_corpus,
)
from app.services.print_layout_authoring.validate import (
    check_source_revision, locks_of, validate_authoring,
)

MAX_PREVIEW_BYTES = 8 * 1024 * 1024
MAX_EXPORT_BYTES = 50 * 1024 * 1024


class PrintLayoutServiceError(Exception):
    def __init__(self, code: str, message: str = "", status_code: int = 400):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status_code = status_code

    def to_dict(self) -> Dict[str, Any]:
        return {"success": False, "code": self.code, "message": self.message}


async def load_mapspec(session_id: str) -> Dict[str, Any]:
    if not session_id:
        raise PrintLayoutServiceError("SOURCE_UNAVAILABLE", "Missing session_id", 503)
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(session_id)
    if not isinstance(spec, dict) or not spec:
        raise PrintLayoutServiceError("SOURCE_UNAVAILABLE",
                                      "No committed MapSpec in this session", 503)
    return spec


def plan_of(mapspec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    layout = mapspec.get("layout") if isinstance(mapspec, dict) else None
    raw = layout.get("print_layout") if isinstance(layout, dict) else None
    return copy.deepcopy(raw) if isinstance(raw, dict) and raw else None


async def _spec_revision(session_id: str, mapspec: Dict[str, Any]) -> int:
    """真实 CAS 基线：session_data 的 ``_cartographic_mutation_revision``。

    MapSpec 文档本身**不携带** revision 字段（旧实现读 ``mapspec["revision"]``
    恒得 None → 恒 0，任何已发生过突变的会话都会被误判 ``SUPERSEDED``）。
    与 ``raster_authoring.service.current_revision`` 取同一事实源。
    """
    from app.services.session_data import session_data_manager

    getter = getattr(session_data_manager, "get_state_field", None)
    raw = await getter(session_id, "_cartographic_mutation_revision") if callable(getter) else None
    if raw is None:
        raw = (mapspec.get("revision")
               or mapspec.get("_cartographic_mutation_revision") or 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def capabilities() -> Dict[str, Any]:
    """能力面：字体 / 纸张 / 词表 / 预算 / 单位 corpus（有界，可缓存）。"""
    from app.lib.cartography.publication_ir import PAGE_PROFILES

    return {
        "success": True,
        "schema_version": PRINT_LAYOUT_SCHEMA_VERSION,
        "render_path": PRINT_RENDER_PATH,
        "paper_profiles": {k: {"width_mm": v[0], "height_mm": v[1]}
                           for k, v in PAGE_PROFILES.items()},
        "fonts": font_resource_status(),
        "font_asset_version": font_asset_version(),
        "font_ids": list(FONT_IDS),
        "component_kinds": [
            "title", "subtitle", "legend", "scale_bar", "north_arrow",
            "attribution", "disclosure", "map_slot", "text_block",
            "page_number", "toc", "group",
        ],
        "text_bindings": list(template_mod.ALLOWED_PLACEHOLDERS),
        "operations": sorted(operations.OPS),
        "budgets": {
            "max_components": 32, "max_atlas_pages": MAX_ATLAS_PAGES,
            "max_text_bytes": 2048, "max_template_bytes": 16 * 1024,
            "max_templates": 8, "max_legend_columns": 4, "max_dpi": 600,
        },
        "units": units_corpus(),
        "default_plan": empty_print_layout(),
    }


async def inspect(session_id: str, *, sync_planner: bool = False,
                  atlas: Any = None) -> Dict[str, Any]:
    """检查当前版式 / 页清单 / 锁 / validation / 字体状态。"""
    mapspec = await load_mapspec(session_id)
    plan = plan_of(mapspec)
    locks = locks_of(mapspec)
    if plan is None:
        return {
            "success": True, "status": "not_configured",
            "schema_version": PRINT_LAYOUT_SCHEMA_VERSION,
            "note": "no layout.print_layout; legacy publication path unchanged",
            "locks": locks, "font_asset_version": font_asset_version(),
            "mapspec_revision": await _spec_revision(session_id, mapspec),
        }
    verdict = validate_print_layout(plan)
    if not verdict.ok:
        return {
            "success": False, "status": "blocked",
            "code": "PRINT_LAYOUT_INVALID",
            "issues": [{"code": i.code, "message": i.message, "severity": i.severity,
                        "component_id": i.component_id}
                       for i in verdict.issues],
        }
    sync_info: Dict[str, Any] = {}
    if sync_planner:
        try:
            sync_info = sync_pages(plan, mapspec, atlas)
            plan = sync_info["plan"]
        except Exception as ex:  # noqa: BLE001
            sync_info = {"error": f"{type(ex).__name__}: {str(ex)[:120]}"}
    from app.services.print_layout_authoring.pages import page_numbering

    numbering = page_numbering(plan)
    return {
        "success": True, "status": "configured",
        "schema_version": plan.get("version"),
        "plan": plan,
        "locks": locks,
        "mapspec_revision": await _spec_revision(session_id, mapspec),
        "font_asset_version": font_asset_version(),
        "validation": {
            "ok": verdict.ok,
            "issues": [{"code": i.code, "message": i.message, "severity": i.severity,
                        "component_id": i.component_id}
                       for i in verdict.issues],
        },
        "numbering": {k: numbering[k] for k in ("page_count", "toc", "toc_enabled")},
        "planner_sync": {k: sync_info.get(k) for k in
                         ("planner_page_count", "binding_changed") if k in sync_info},
    }


async def prepare_pages(session_id: str, atlas: Any = None) -> Dict[str, Any]:
    """真实待导出页清单（消费既有 planner，不新增分幅算法）。"""
    mapspec = await load_mapspec(session_id)
    plan = plan_of(mapspec) or empty_print_layout()
    try:
        synced = sync_pages(plan, mapspec, atlas)
    except Exception as ex:  # noqa: BLE001
        raise PrintLayoutServiceError(
            "ATLAS_PLAN_FAILED", f"{type(ex).__name__}: {str(ex)[:200]}", 422) from ex
    from app.services.print_layout_authoring.pages import page_numbering

    numbering = page_numbering(synced["plan"])
    real_by_id = {p["page_id"]: p for p in synced["planner_pages"]}
    rows: List[Dict[str, Any]] = []
    for r in numbering["toc"]:
        real = real_by_id.get(r["page_id"], {})
        authored = next((p for p in synced["plan"]["atlas"]["pages"]
                         if p["page_id"] == r["page_id"]), {})
        rows.append({
            "page_id": r["page_id"], "title": r["title"],
            "page_number": r["page_number"],
            "binding_digest": r["binding_digest"],
            "paper_mm": (real.get("paper") or {}).get("width_mm"),
            "paper_height_mm": (real.get("paper") or {}).get("height_mm"),
            "enabled": True, "order": int(authored.get("order") or 0),
            "template_id": authored.get("template_id"),
            "overrides": len(authored.get("overrides") or []),
            "cover": bool(real.get("cover")),
            "thumb_status": "not_evaluated",  # 缩略图由 UI 按需生成
        })
    disabled = [{
        "page_id": p["page_id"], "title": p.get("title") or "",
        "enabled": False, "binding_digest": p.get("binding_digest"),
    } for p in synced["plan"]["atlas"]["pages"]
        if p.get("enabled") is False]
    return {
        "success": True, "status": "ok",
        "planner_page_count": synced["planner_page_count"],
        "page_budget": MAX_ATLAS_PAGES,
        "page_count": numbering["page_count"],
        "toc": numbering["toc"] if numbering["toc_enabled"] else [],
        "pages": rows, "disabled_pages": disabled,
        "binding_changed": synced["binding_changed"],
        "exceeds_budget": synced["planner_page_count"] > MAX_ATLAS_PAGES,
    }


def _evaluate(mapspec: Dict[str, Any], plan: Dict[str, Any],
              ops: Any, *, dpi: int = 300,
              allow_empty: bool = False) -> Dict[str, Any]:
    """纯 dry-run 内核：不改 store、不改入参（``apply_operations`` 自持副本）。

    ``propose`` 与 ``apply`` 共用本函数——避免 apply 重读 store 把刚刚由
    ``initialize=True`` 造出来的空计划丢掉（首建路径曾因此必然失败）。
    成功时额外返回 ``candidate`` 供调用方直接提交，不二次执行 operation。
    """
    if not isinstance(ops, list):
        return {"success": False, "code": "INVALID_PROPOSAL",
                "message": "operations must be a list"}
    locks = locks_of(mapspec)
    if allow_empty and not ops:
        # ``initialize=True`` 且不带操作 = 「用默认 A4 计划建版」；不是空提交。
        result: Dict[str, Any] = {"ok": True, "plan": copy.deepcopy(plan),
                                  "changes": [], "skipped_locked": [], "issues": []}
    else:
        result = operations.apply_operations(plan, ops, locks)
    if not result["ok"]:
        return {"success": False, "status": "blocked",
                "code": result["issues"][0]["code"] if result["issues"] else "INVALID_PROPOSAL",
                "issues": result["issues"], "changes": [], "skipped_locked": []}
    candidate = result["plan"]
    report = validate_authoring(mapspec, candidate, dpi=dpi)
    first_error = next((e for e in report["errors"] if e), {})
    return {
        "success": True, "status": "validated" if report["ok"] else "blocked",
        # 校验失败要带上**具体**错误码；否则 UI/Pi 只能看到笼统的 BLOCKED。
        "code": "" if report["ok"] else str(first_error.get("code") or "PRINT_LAYOUT_BLOCKED"),
        "issues": report["errors"] if not report["ok"] else [],
        "dry_run": True, "committed": False,
        "changes": result["changes"],
        "skipped_locked": result["skipped_locked"],
        "locks": locks,
        "adopted_values": {
            "version": candidate.get("version"),
            "paper": candidate.get("paper"),
            "components": [
                {"id": c.get("id"), "frame": c.get("frame"),
                 "typography": c.get("typography"), "legend": c.get("legend")}
                for c in candidate.get("components") or []
            ],
            "atlas_pages": len((candidate.get("atlas") or {}).get("pages") or []),
        },
        "validation": {"ok": report["ok"], "errors": report["errors"],
                       "warnings": report["warnings"]},
        "candidate": candidate,
        "note": "dry-run only; call apply() to commit one CAS mutation",
    }


async def propose(session_id: str, proposal: Dict[str, Any]) -> Dict[str, Any]:
    """有界变更 dry-run：采用值 / 覆盖范围 / 锁跳过 / validation，零写入。"""
    mapspec = await load_mapspec(session_id)
    plan = plan_of(mapspec)
    if plan is None:
        if not bool(proposal.get("initialize")):
            return {"success": False, "code": "PRINT_LAYOUT_NOT_CONFIGURED",
                    "message": "initialize=true creates the default print_layout.v1 plan"}
        plan = empty_print_layout()
    out = _evaluate(mapspec, plan, proposal.get("operations"),
                    dpi=int(proposal.get("dpi") or 300),
                    allow_empty=bool(proposal.get("initialize")))
    out.pop("candidate", None)  # dry-run 回执不夹带可提交状态
    return out


async def apply(session_id: str, proposal: Dict[str, Any], *,
                expected_revision: Optional[int] = None,
                origin: str = "user",
                actor: str = "print-layout-authoring") -> Dict[str, Any]:
    """原子应用：一次 dry-run → 一次 ``set_layout`` intent / 一次 CAS。

    Pi 与 UI 走同一函数、同一服务、同一 CAS、同一锁规则。失败零写入。
    """
    mapspec = await load_mapspec(session_id)
    plan = plan_of(mapspec)
    if plan is None:
        if not bool(proposal.get("initialize")):
            return {"success": False, "code": "PRINT_LAYOUT_NOT_CONFIGURED",
                    "message": "initialize=true creates the default plan"}
        plan = empty_print_layout()
    spec_revision = await _spec_revision(session_id, mapspec)
    stale = check_source_revision(plan, expected_revision,
                                  mapspec_revision=spec_revision)
    if not stale["ok"]:
        return {"success": False, "code": stale["code"],
                "message": stale["message"], "committed": False}
    dry = _evaluate(mapspec, plan, proposal.get("operations") or [],
                    dpi=int(proposal.get("dpi") or 300),
                    allow_empty=bool(proposal.get("initialize")))
    if not dry.get("success") or dry.get("status") != "validated":
        return {"success": False, "code": dry.get("code") or "PRINT_LAYOUT_BLOCKED",
                "issues": dry.get("issues", []), "committed": False}
    candidate = dry
    if not candidate["success"]:
        return {"success": False, "code": "PRINT_LAYOUT_BLOCKED",
                "issues": candidate.get("issues", []), "committed": False}
    new_plan = candidate["candidate"]
    new_plan["source"] = {
        "mapspec_revision": int(expected_revision or spec_revision),
        "style_revision": str(mapspec.get("version") or ""),
        "spec_fingerprint": str(mapspec.get("fingerprint") or "")[:256],
    }
    new_plan["font_asset_version"] = font_asset_version()
    new_plan["updated_at"] = str(int(time.time()))
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import SetLayoutIntent

    result = await apply_gis_mutation(
        session_id, SetLayoutIntent(print_layout=new_plan),
        origin=origin if origin in ("user", "agent", "system") else "user",
        actor=actor, expected_revision=expected_revision,
        client_optimistic_id=str(proposal.get("client_mutation_id") or "") or None,
        reason="g34 print layout authoring",
    )
    if result.is_error:
        return {"success": False, "code": "MUTATION_REJECTED",
                "message": result.error_msg or "mutation rejected",
                "correction_hint": result.correction_hint, "committed": False}
    if result.superseded:
        return {"success": False, "code": "SUPERSEDED",
                "message": "MapSpec revision changed; re-read and retry",
                "committed": False}
    return {
        "success": True, "status": "committed", "committed": True,
        "mutation_revision": result.mutation_revision,
        "schema_version": new_plan.get("version"),
        "changes": candidate["changes"],
        "skipped_locked": candidate["skipped_locked"],
        "adopted_values": dry["adopted_values"],
        "validation": dry["validation"],
        "plan_fingerprint": template_mod.template_fingerprint(new_plan),
        # 计划绑定 revision 落后于当前 spec：已按 CAS 放行并重绑，如实披露
        "binding_drift": bool(stale.get("binding_drift")),
        "rebased_from": stale.get("rebased_from"),
    }


async def preview(session_id: str, *, page_id: str = "", dpi: int = 300,
                  include_svg: bool = False,
                  cancel_event: Any = None) -> Dict[str, Any]:
    """纸面预览：真实编译一页，返回有界回执（+ 可选 SVG 供 UI 使用）。

    取消 / 字体资源不可用 → 明确终态，绝不返回虚假 completed。
    """
    def _cancelled() -> bool:
        return bool(cancel_event is not None and getattr(cancel_event, "is_set",
                                                         lambda: False)())

    if _cancelled():
        return {"success": False, "status": "cancelled", "code": "CANCELLED"}
    mapspec = await load_mapspec(session_id)
    plan = plan_of(mapspec)
    if plan is None:
        return {"success": False, "status": "not_configured",
                "code": "PRINT_LAYOUT_NOT_CONFIGURED"}
    dpi_i = int(clamp_dpi(dpi))
    report = validate_authoring(mapspec, plan, dpi=dpi_i, page_id=page_id)
    if not report["ok"]:
        unavailable = [e for e in report["errors"]
                       if e["code"] == "FONT_RESOURCE_UNAVAILABLE"]
        return {
            "success": False,
            "status": "blocked" if not unavailable else "font_unavailable",
            "code": unavailable[0]["code"] if unavailable else "PRINT_LAYOUT_BLOCKED",
            "errors": report["errors"], "warnings": report["warnings"],
        }
    if _cancelled():
        return {"success": False, "status": "cancelled", "code": "CANCELLED"}
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    pw, ph = _paper_for(plan, page_id)
    geo_bounds = list((mapspec.get("view") or {}).get("bounds") or
                      plan.get("source", {}).get("bounds") or [100.0, 20.0, 110.0, 30.0])
    if len(geo_bounds) != 4:
        geo_bounds = [100.0, 20.0, 110.0, 30.0]
    comp = await asyncio.to_thread(
        compile_mapspec_to_svg_detailed,
        mapspec, target_dpi=dpi_i, width=int(pw * 4), height=int(ph * 4),
        padding=24, include_chrome=True, bounds=geo_bounds,
        print_layout=plan, paper_mm=(pw, ph), page_id=page_id,
    )
    if _cancelled():
        return {"success": False, "status": "cancelled", "code": "CANCELLED"}
    receipt = dict(getattr(comp, "print_layout_receipt", None) or {})
    if receipt.get("status") == "compile_failed":
        return {"success": False, "status": "blocked",
                "code": "PRINT_LAYOUT_COMPILE_FAILED",
                "diagnostics": comp.diagnostics[:8]}
    svg_bytes = len(comp.svg.encode("utf-8"))
    out: Dict[str, Any] = {
        "success": True, "status": "preview_ready", "committed": False,
        "page_id": page_id, "dpi": dpi_i,
        "paper_mm": {"width": pw, "height": ph},
        "sample_px": {"width": round(mm_to_sample_px(pw, dpi_i)),
                      "height": round(mm_to_sample_px(ph, dpi_i))},
        "receipt": receipt,
        "svg_sha256": hashlib.sha256(comp.svg.encode("utf-8")).hexdigest(),
        "svg_bytes": min(svg_bytes, MAX_PREVIEW_BYTES),
        "warnings": report["warnings"],
        "font_status": [{"requested_font_id": c["font"]["requested_font_id"],
                         "applied_font_id": c["font"]["applied_font_id"],
                         "issues": c["font"]["issues"]}
                        for c in report["components"]],
    }
    if include_svg and svg_bytes <= MAX_PREVIEW_BYTES:
        out["svg"] = comp.svg
    return out


def _paper_for(plan: Dict[str, Any], page_id: str) -> tuple:
    """单页真实纸面（mm）：页级 ``paper_profile`` > 计划纸张 > A4 横向。

    预览与导出都走这里，保证「同一作者决定 → 同一纸面尺寸」，不出现
    预览是 A4、导出是 A3 的分裂。
    """
    paper = plan.get("paper") or {}
    if page_id:
        try:
            page = page_plan(plan, page_id)
        except Exception:  # noqa: BLE001 — 纸张解析失败不得阻断预览
            page = {}
        if page.get("found"):
            paper = page.get("paper") or paper
    return (float(paper.get("width_mm") or 297.0),
            float(paper.get("height_mm") or 210.0))


async def submit_export(session_id: str, *, fmt: str = "pdf", dpi: int = 300,
                        page_id: str = "", cancel_event: Any = None) -> Dict[str, Any]:
    """提交**既有**导出链（PDF/SVG/PNG），作者计划从同一 MapSpec 读取。

    返回 refs-only 回执；不返回整页像素或字体 bytes。取消 / 依赖缺失 →
    明确终态，不影响其它用户页面与任务。
    """
    fmt = str(fmt or "pdf").lower()
    if fmt not in ("pdf", "svg", "png"):
        return {"success": False, "code": "UNSUPPORTED_FORMAT", "format": fmt}
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        return {"success": False, "status": "cancelled", "code": "CANCELLED"}
    mapspec = await load_mapspec(session_id)
    plan = plan_of(mapspec)
    if plan is None:
        return {"success": False, "status": "blocked",
                "code": "PRINT_LAYOUT_NOT_CONFIGURED",
                "message": "authoring plan required before paper export"}
    dpi_i = int(clamp_dpi(dpi))
    pw, ph = _paper_for(plan, page_id)
    geo_bounds = list((mapspec.get("view") or {}).get("bounds") or [100.0, 20.0, 110.0, 30.0])
    if len(geo_bounds) != 4:
        geo_bounds = [100.0, 20.0, 110.0, 30.0]
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    comp = await asyncio.to_thread(
        compile_mapspec_to_svg_detailed,
        mapspec, target_dpi=dpi_i, width=int(pw * 4), height=int(ph * 4),
        padding=24, include_chrome=True, bounds=geo_bounds,
        print_layout=plan, paper_mm=(pw, ph), page_id=page_id,
    )
    receipt = dict(getattr(comp, "print_layout_receipt", None) or {})
    if receipt.get("status") == "compile_failed":
        return {"success": False, "status": "blocked",
                "code": "PRINT_LAYOUT_COMPILE_FAILED"}
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        return {"success": False, "status": "cancelled", "code": "CANCELLED"}

    manifest: Dict[str, Any] = {
        "schema_version": PRINT_LAYOUT_SCHEMA_VERSION,
        "render_path": PRINT_RENDER_PATH,
        "format": fmt, "target_dpi": dpi_i,
        "paper_mm": {"width": pw, "height": ph},
        "print_layout": receipt,
        "mapspec_revision": await _spec_revision(session_id, mapspec),
    }
    if fmt == "svg":
        payload = _with_physical_size(comp.svg, pw, ph).encode("utf-8")
        media, ext = "image/svg+xml", ".svg"
    elif fmt == "png":
        try:
            payload = await asyncio.to_thread(
                _svg_to_png, comp.svg, pw, ph, dpi_i)
        except PrintLayoutServiceError as ex:
            return {"success": False, "status": "blocked", "code": ex.code,
                    "message": ex.message}
        media, ext = "image/png", ".png"
    else:
        try:
            payload = await asyncio.to_thread(
                _svg_to_pdf, comp.svg, pw, ph, "G34 纸面版式成果")
        except PrintLayoutServiceError as ex:
            return {"success": False, "status": "blocked", "code": ex.code,
                    "message": ex.message}
        media, ext = "application/pdf", ".pdf"
    if len(payload) > MAX_EXPORT_BYTES:
        return {"success": False, "status": "blocked", "code": "EXPORT_BYTE_BUDGET"}
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        return {"success": False, "status": "cancelled", "code": "CANCELLED"}
    manifest["checksum"] = hashlib.sha256(payload).hexdigest()
    artifact = {"bytes": payload, "media_type": media, "extension": ext,
                "manifest": manifest}
    from app.services.mixed_map_export import persist_mixed_export

    delivered = await persist_mixed_export(
        session_id, artifact, principal=f"session:{session_id}")
    if not delivered.get("success"):
        return {"success": False, "status": "blocked",
                "code": "EXPORT_PERSIST_FAILED", "details": delivered}
    return {
        "success": True, "status": "exported", "format": fmt, "dpi": dpi_i,
        "bytes": delivered.get("bytes"),
        "download_url": delivered.get("download_url"),
        "manifest_url": delivered.get("manifest_url"),
        "filename": delivered.get("filename"),
        "checksum": manifest["checksum"],
        "print_layout": receipt,
    }


_SVG_TAG_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)


def _with_physical_size(svg: str, width_mm: float, height_mm: float) -> str:
    """给独立交付的 SVG 根节点加**物理毫米尺寸**。

    ``compile_mapspec_to_svg_detailed`` 产出的是 viewBox 像素轴（4 px/mm），
    ``width=1188`` 在 96dpi 下会被读成 ≈314mm —— 交付物因此不是物理 A4。
    这里只改根节点 width/height 为 mm（viewBox 保持不变，几何零位移），
    使 96/150/300 DPI 下打印尺寸一致，并留 ``data-g34-paper-mm`` 供回读核验。
    """
    m = _SVG_TAG_RE.search(svg)
    if not m:
        return svg
    tag = m.group(0)
    new_tag = re.sub(r'\swidth="[^"]*"', "", tag, count=1)
    new_tag = re.sub(r'\sheight="[^"]*"', "", new_tag, count=1)
    attrs = (f' width="{width_mm:.4f}mm" height="{height_mm:.4f}mm"'
             f' data-g34-paper-mm="{width_mm:.4f}x{height_mm:.4f}"')
    new_tag = new_tag[:-1].rstrip() + attrs + ">"
    return svg[:m.start()] + new_tag + svg[m.end():]


def _svg_to_png(svg: str, width_mm: float, height_mm: float, dpi: int) -> bytes:
    """真实 rsvg-convert 栅格化（不 mock 渲染主链）。"""
    exe = "rsvg-convert"
    import shutil

    if shutil.which(exe) is None:
        raise PrintLayoutServiceError("PNG_RENDERER_UNAVAILABLE",
                                      "rsvg-convert not installed", 503)
    w_px = max(1, int(round(mm_to_sample_px(width_mm, dpi))))
    h_px = max(1, int(round(mm_to_sample_px(height_mm, dpi))))
    with tempfile.TemporaryDirectory(prefix="g34-png-") as td:
        src = Path(td) / "page.svg"
        dst = Path(td) / "page.png"
        src.write_bytes(svg.encode("utf-8"))
        proc = subprocess.run(
            [exe, "-w", str(w_px), "-h", str(h_px), "-o", str(dst), str(src)],
            capture_output=True, timeout=60,
        )
        if proc.returncode != 0 or not dst.is_file():
            raise PrintLayoutServiceError(
                "PNG_RENDER_FAILED", proc.stderr.decode("utf-8", "replace")[:200], 500)
        return dst.read_bytes()


def _svg_to_pdf(svg: str, width_mm: float, height_mm: float, title: str) -> bytes:
    """复用 G26/G34 同一 WeasyPrint 装配面（真实 PDF 文本层，非整页截图）。"""
    from app.services.publication_export import (
        CSS_FONT_STACK, _cjk_font_face_css, _deny_url_fetcher,
        _g34_font_face_css, render_pdf_exclusive,
    )

    # G34 导出始终内嵌作者所选字体（G26 只在无系统 CJK 字体时嵌入）。
    font_face = _cjk_font_face_css() or _g34_font_face_css()
    stack = f"'Noto Sans SC Embedded', {CSS_FONT_STACK}" if font_face else CSS_FONT_STACK
    # 只给未声明 font-family 的文本兜底，绝不静默覆盖作者选择（场景 6）。
    fallback = (
        f"svg:not([font-family]), svg text:not([font-family]) "
        f"{{ font-family: {stack}; }}"
    )
    html_doc = (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
        f'{font_face}'
        f'@page {{ size: {width_mm}mm {height_mm}mm; margin: 0; }}'
        f'html, body {{ margin: 0; padding: 0; }}'
        f'svg {{ width: 100%; height: 100%; }}'
        f'{fallback}'
        f'</style></head><body>{svg}</body></html>'
    )
    from weasyprint import HTML

    try:
        return render_pdf_exclusive(lambda: HTML(
            string=html_doc, url_fetcher=_deny_url_fetcher,
            base_url=None).write_pdf())
    except Exception as ex:  # noqa: BLE001
        raise PrintLayoutServiceError("PDF_RENDER_FAILED", str(ex)[:200], 500) from ex


async def templates(session_id: str, action: str,
                    proposal: Dict[str, Any]) -> Dict[str, Any]:
    """模板保存 / 应用范围 / 复位（走同一 propose→apply 原子链）。"""
    spec = await load_mapspec(session_id)  # noqa: F841 — 触发有界检查
    if action == "scope":
        plan = plan_of(spec)
        if plan is None:
            return {"success": False, "code": "PRINT_LAYOUT_NOT_CONFIGURED"}
        try:
            return {"success": True, "scope": template_mod.template_scope(
                plan, str(proposal.get("template_id") or ""))}
        except template_mod.PrintTemplateError as ex:
            return {"success": False, "code": ex.code, "message": ex.message}
    ops = []
    if action == "save":
        ops.append({"op": "save_template", **proposal})
    elif action == "apply":
        ops.append({"op": "apply_template", **proposal})
    elif action == "reset":
        ops.append({"op": "reset_overrides", **proposal})
    else:
        return {"success": False, "code": "UNKNOWN_TEMPLATE_ACTION", "action": action}
    return await apply(session_id, {"operations": ops,
                                    "initialize": bool(proposal.get("initialize"))},
                       expected_revision=proposal.get("expected_revision"),
                       origin=str(proposal.get("origin") or "user"))
