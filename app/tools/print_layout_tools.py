"""G34 纸面版式作者工作台 —— Pi 标准工具面。

注册到既有 ``ToolRegistry``（``app/tools/__init__.py::_TOOL_MODULES`` 追加
一行），经 Pi 扩展的 dynamic registered surface 或 ``webgis_execute``
长尾代理到**同一个** ``ToolRegistry.dispatch`` → 本域 ``service.py``。
不改 RPC 内核、不加 ``NATIVE_TOOL_NAMES``、不注册 ``webgis_execute``、
不造旁路。UI 的 REST 路由与本工具共用同一 service / CAS / 锁规则。

回执 refs-only：不把整页 SVG、字体 bytes 或栅格数组塞进 LLM 上下文。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class _SessionArgs(BaseModel):
    session_id: str = Field(default="", description="由 dispatcher 注入的会话 id")


class CapabilitiesArgs(_SessionArgs):
    pass


class InspectArgs(_SessionArgs):
    sync_planner: bool = Field(
        default=False, description="是否把既有 atlas planner 的真实页清单同步进作者计划")


class PagesArgs(_SessionArgs):
    driver: str = Field(default="frames", pattern="^(frames|category|feature|time|product)$",
                        description="复用既有 atlas planner 驱动；不新增分幅算法")
    page_budget: int = Field(default=20, ge=1, le=20,
                             description="沿用既有 MAX_ATLAS_PAGES=20，不抬高上限")


class ProposeArgs(_SessionArgs):
    operations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="有界作者操作列表（≤32）；op 词表见 capabilities。"
                    "例：set_frame / align / distribute / set_legend / "
                    "apply_template / reset_overrides / set_pages",
    )
    initialize: bool = Field(default=False, description="无计划时初始化默认 A4 计划")
    dpi: int = Field(default=300, ge=72, le=600)


class ApplyArgs(ProposeArgs):
    expected_revision: Optional[int] = Field(
        default=None, ge=0,
        description="CAS 基线；与当前 MapSpec 修订不符 → SUPERSEDED 拒绝（必填）")
    client_mutation_id: str = Field(default="", max_length=128)


class PreviewArgs(_SessionArgs):
    page_id: str = Field(default="", max_length=128)
    dpi: int = Field(default=300, ge=72, le=600)


class ExportArgs(_SessionArgs):
    format: str = Field(default="pdf", pattern="^(pdf|svg|png)$")
    dpi: int = Field(default=300, ge=72, le=600)
    page_id: str = Field(default="", max_length=128)
    expected_revision: Optional[int] = Field(default=None, ge=0)


def _bounded(payload: Dict[str, Any], limit: int = 6000) -> Dict[str, Any]:
    """有界回执：绝不把整页像素/字体 bytes/栅格数组送进模型上下文。"""
    import json

    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) <= limit:
        return payload
    trimmed = {
        "success": payload.get("success"),
        "status": payload.get("status"),
        "code": payload.get("code"),
        "message": payload.get("message"),
        "truncated": True,
        "hint": "full receipt available via webgis_print_layout_inspect / REST",
    }
    for key in ("mutation_revision", "changes", "skipped_locked", "receipt",
                "download_url", "manifest_url", "checksum", "issues", "errors"):
        if key in payload:
            part = json.dumps(payload[key], ensure_ascii=False, default=str)
            if len(part.encode("utf-8")) < 2000:
                trimmed[key] = payload[key]
    return trimmed


def _err(exc: Exception) -> Dict[str, Any]:
    code = getattr(exc, "code", type(exc).__name__)
    return {
        "success": False, "code": str(code),
        "message": str(getattr(exc, "message", str(exc)))[:400],
        "correction_hint": (
            "Call webgis_print_layout_capabilities, then "
            "webgis_print_layout_propose (dry-run), then apply with the "
            "current expected_revision from webgis_print_layout_inspect."
        ),
    }


def register_print_layout_tools(registry: ToolRegistry) -> None:
    """注册 G34 标准工具（tier 2；长尾经 webgis_execute 或 registered surface）。"""
    from app.services.print_layout_authoring import service

    @tool(
        registry,
        name="webgis_print_layout_capabilities",
        domains=["cartography", "publication"],
        tier=2,
        args_model=CapabilitiesArgs,
        description=(
            "读取纸面版式工作台能力：受控字体资源（真实可用/weight/缺字）、"
            "纸张毫米 profiles、组件 kind 与操作词表、预算上限、单位换算 corpus。"
            "在提出任何版式变更前先调用本工具确认受支持值，不要猜字体或纸张。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("纸面版式", "能力", "字体"),
        output_semantic_type="capability_matrix",
        result_size_policy="bounded",
        required_context=("map_state",),
        failure_modes=("source_unavailable",),
    )
    async def webgis_print_layout_capabilities(session_id: str = "") -> dict:
        try:
            caps = await service.capabilities()
            # 工具面保持有界：完整单位 corpus / 默认计划由 REST 与
            # golden parity 消费，模型只取常量与一个采样行。
            compact = dict(caps)
            units = dict(caps.get("units") or {})
            units["rows"] = list(units.get("rows") or [])[:2]
            compact["units"] = units
            compact.pop("default_plan", None)
            return _bounded(compact)
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @tool(
        registry,
        name="webgis_print_layout_inspect",
        domains=["cartography", "publication"],
        tier=2,
        args_model=InspectArgs,
        description=(
            "检查当前会话的纸面版式：print_layout.v1 计划、用户锁、"
            "validation、字体资源版本、图册页序/页码/总数。"
            "返回 not_configured 时旧出版路径未变，需 initialize 才启用。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("纸面版式", "检查"),
        output_semantic_type="authoring_state",
        result_size_policy="bounded",
        required_context=("map_state",),
        failure_modes=("source_unavailable", "invalid_plan"),
    )
    async def webgis_print_layout_inspect(
        sync_planner: bool = False, session_id: str = "",
    ) -> dict:
        try:
            return _bounded(await service.inspect(session_id, sync_planner=sync_planner))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @tool(
        registry,
        name="webgis_print_layout_pages",
        domains=["cartography", "publication"],
        tier=2,
        args_model=PagesArgs,
        description=(
            "生成图册真实待导出页清单（消费既有 atlas planner 的 "
            "frames/category/feature/time/product 驱动），含每页绑定指纹、"
            "最终启用页序的页码/总数/目录。超出既有 page_budget 明确拒绝，"
            "不把前 N 页冒称全册；不新增分幅算法。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=("图册", "页清单"),
        output_semantic_type="page_manifest",
        result_size_policy="bounded",
        required_context=("map_state",),
        failure_modes=("source_unavailable", "atlas_plan_failed", "page_budget_exceeded"),
    )
    async def webgis_print_layout_pages(
        driver: str = "frames", page_budget: int = 20, session_id: str = "",
    ) -> dict:
        try:
            from app.lib.cartography.publication_ir import AtlasPolicy

            atlas = AtlasPolicy(driver=driver, page_budget=page_budget)
            return _bounded(await service.prepare_pages(session_id, atlas))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @tool(
        registry,
        name="webgis_print_layout_propose",
        domains=["cartography", "publication"],
        tier=2,
        args_model=ProposeArgs,
        description=(
            "有界作者变更的 dry-run（零写入）：把标题改成两行、图例改两列、"
            "多选组件对齐/等距、应用模板、页级覆写复位等。"
            "返回实际将采用的值、覆盖范围、被锁组件跳过项和 validation。"
            "修改多个组件是一个原子动作；失败不留半应用模板。"
            "本工具绝不直接写全文 MapSpec。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=("纸面版式", "预览变更"),
        output_semantic_type="dry_run_receipt",
        result_size_policy="bounded",
        required_context=("map_state",),
        failure_modes=("invalid_proposal", "locked_component", "not_configured",
                       "text_overflow_blocked", "legend_incomplete"),
    )
    async def webgis_print_layout_propose(
        operations: List[Dict[str, Any]] = None, initialize: bool = False,
        dpi: int = 300, session_id: str = "",
    ) -> dict:
        try:
            return _bounded(await service.propose(session_id, {
                "operations": operations or [], "initialize": initialize, "dpi": dpi,
            }))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @tool(
        registry,
        name="webgis_print_layout_apply",
        domains=["cartography", "publication"],
        tier=2,
        args_model=ApplyArgs,
        description=(
            "原子提交纸面作者变更：一次 dry-run 验证 → 一次 set_layout intent "
            "→ 一次 CAS。与 UI 共用同一服务/锁/撤销栈。必须带 "
            "expected_revision（从 inspect 取得）；revision 变化 → SUPERSEDED "
            "拒绝，不混合新旧页面内容。用户锁定的组件默认整笔拒绝。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        map_mutations=["component"],
        tags=("纸面版式", "应用", "CAS"),
        output_semantic_type="mutation_receipt",
        result_size_policy="bounded",
        required_context=("map_state",),
        failure_modes=("superseded", "missing_expected_revision",
                       "locked_component", "invalid_proposal", "mutation_rejected"),
    )
    async def webgis_print_layout_apply(
        operations: List[Dict[str, Any]] = None, initialize: bool = False,
        dpi: int = 300, expected_revision: Optional[int] = None,
        client_mutation_id: str = "", session_id: str = "",
    ) -> dict:
        try:
            return _bounded(await service.apply(
                session_id,
                {"operations": operations or [], "initialize": initialize,
                 "dpi": dpi, "client_mutation_id": client_mutation_id},
                expected_revision=expected_revision, origin="agent",
                actor="pi:webgis_print_layout_apply",
            ))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @tool(
        registry,
        name="webgis_print_layout_preview",
        domains=["cartography", "publication"],
        tier=2,
        args_model=PreviewArgs,
        description=(
            "纸面预览：真实编译当前页，返回组件毫米框、文本 lines/overflow、"
            "图例条目完整性、比例尺与地图槽有效 extent、字体状态的有界回执；"
            "不返回整页像素。字体不可用或取消 → 明确终态，绝不假称完成。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="small",
        tags=("纸面版式", "预览"),
        output_semantic_type="preview_receipt",
        result_size_policy="bounded",
        required_context=("map_state",),
        failure_modes=("font_unavailable", "cancelled", "compile_failed",
                       "not_configured"),
    )
    async def webgis_print_layout_preview(
        page_id: str = "", dpi: int = 300, session_id: str = "",
    ) -> dict:
        try:
            return _bounded(await service.preview(
                session_id, page_id=page_id, dpi=dpi, include_svg=False))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @tool(
        registry,
        name="webgis_print_layout_export",
        domains=["cartography", "publication"],
        tier=2,
        args_model=ExportArgs,
        description=(
            "提交既有导出链产出真实 PDF/SVG/PNG 纸面成品（同一作者决定），"
            "返回受权 download_url / manifest_url / checksum refs。"
            "PNG 依赖宿主 rsvg-convert，PDF 依赖 WeasyPrint；依赖缺失明确 "
            "blocked，不伪造 completed。不等线上 CI。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="medium",
        tags=("纸面版式", "导出", "PDF/SVG/PNG"),
        output_semantic_type="reference",
        result_size_policy="ref_offload",
        produced_refs=("artifact", "manifest"),
        required_context=("map_state",),
        failure_modes=("not_configured", "unsupported_format", "pdf_render_failed",
                       "png_renderer_unavailable", "cancelled", "export_persist_failed"),
    )
    async def webgis_print_layout_export(
        format: str = "pdf", dpi: int = 300, page_id: str = "",
        expected_revision: Optional[int] = None, session_id: str = "",
    ) -> dict:
        try:
            return _bounded(await service.submit_export(
                session_id, fmt=format, dpi=dpi, page_id=page_id))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)
