"""Atlas export agent tools —— 批量图册发布的 Agent 原生入口。

单一工具 ``export_atlas``：提交 durable job（与 REST/前端同一任务 API），
返回 job 句柄供轮询。重计算（逐页渲染 + PDF 组装）全部在 worker ——
工具面只做参数校验与提交（idempotent 幂等键防双击/重连重复提交）。
"""
import logging
from typing import Optional

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def register_atlas_export_tools(registry: ToolRegistry):
    """注册批量图册导出工具。"""

    @tool(
        registry, name="export_atlas",
        capabilities=["map_export_publishing"],  # 平台能力包既有词汇（platform.py）
        tier=2, domains=["cartography"],
        description=(
            "批量专题图册导出（durable job，异步）：以会话已提交的权威 MapSpec "
            "为母版，按行政区属性值（category）逐区生成一页真实数据绑定的地图，"
            "含封面/目录/页码，组装单一矢量 PDF 并产出可复现 provenance manifest。"
            "\n何时用：用户说『为各区做统一风格的专题地图集/图册/系列地图，每区一页，"
            "导出一个 PDF』；或需要逐页可验证、失败页可定向重试的批量制图。"
            "\n何时不用：(1) 单张地图导出 — 走既有导出；(2) 只要前端逐帧画布拼图 — "
            "export_batch_maps；(3) 逐页自定义分级 — 需显式 frames 版式（local scope）。"
            "\n关键约束：session 必须已提交 MapSpec 且分页属性在内联/会话数据图层上；"
            "页预算 ≤20；shared 分类 = 全册同一图例阈值（跨页可比），local = 逐页"
            "显式 frames 分级（跨页不可比，manifest 如实披露）。"
            "\n任务异步：返回 job_id 后在任务中心轮询（/tasks/jobs）；失败页重试"
            "不重渲染已通过页（页指纹缓存）。"
        ),
        param_descriptions={
            "session_id": "会话 ID（必须已提交权威 MapSpec）",
            "driver": "分页驱动：category=按属性去重值逐区一页（推荐）；feature=按要素分块；frames=显式多帧",
            "layer_id": "目标图层 id（空=自动定位首个内联 geojson 图层）",
            "category_property": "category 驱动的分页属性名（如行政区名字段），必填",
            "features_per_page": "feature 驱动每页要素数（1-500）",
            "page_budget": "页数预算（1-20，超出截断并披露）",
            "include_cover": "是否生成封面/目录页",
            "atlas_title": "图册标题（封面/页头）",
            "classification_scope": "分级语义：shared=全册同一阈值图例（跨页可比）；local=逐页分级（仅 frames 驱动，跨页不可比）",
            "target_dpi": "目标 DPI（72-600，缺省 300）",
            "title": "PDF 元数据标题",
        },
        side_effect="artifact_creation",  # PDF + manifest 产物入 exports+lineage
        deterministic=True,
        network=False,
        idempotent=True,  # submit_durable_job 幂等键（idempotent_reuse）
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        output_semantic_type="text",
        result_size_policy="inline_small",
        data_mutations=("artifact_write",),  # PDF + manifest 入 exports + lineage
        tags=("图册", "atlas", "批量导出", "系列制图", "专题地图集", "PDF", "行政区"),
        failure_modes=("invalid_args", "missing_data", "busy", "page_failed"),
    )
    def export_atlas(
        session_id: Optional[str] = None,
        driver: str = "category",
        layer_id: str = "",
        category_property: str = "",
        features_per_page: int = 50,
        page_budget: int = 20,
        include_cover: bool = True,
        atlas_title: str = "",
        classification_scope: str = "shared",
        target_dpi: int = 300,
        title: str = "",
    ) -> dict:
        from app.tools.upload_tools import _resolve_session_id

        from app.services.atlas_batch.submit import (
            build_atlas_job_spec,
            submit_atlas_job,
        )

        # R1-P1：session_id 是调用方声称值（prompt-injection 可伪造）—— 一律经
        # 运行时上下文归属校验（与 upload_tools 同纪律），不因显式提供而直通。
        session = _resolve_session_id(session_id)
        if not session:
            return {"error": "session_id 未通过归属校验（与当前会话不符或无会话上下文）",
                    "code": "session_forbidden"}

        try:
            spec = build_atlas_job_spec(
                session_id=str(session),
                atlas_policy={
                    "driver": driver,
                    "layer_id": layer_id,
                    "category_property": category_property,
                    "features_per_page": features_per_page,
                    "page_budget": page_budget,
                    "include_cover": include_cover,
                    "atlas_title": atlas_title,
                },
                classification_scope=classification_scope,
                target_dpi=target_dpi,
                title=title,
            )
        except Exception as ex:  # noqa: BLE001 — typed 校验面 → 结构化错误返回
            return {"error": str(ex), "code": getattr(ex, "code", "invalid_args")}

        # R1-P2：图册产物下载走属主（user_id）通道 —— 匿名（无 owner）提交会
        # 产出无人可下载的 PDF，诚实拒绝而不是静默半成品。
        from app.services.jobs.context import current_origin

        origin = current_origin()
        if origin is None or not origin.owner_id:
            return {"error": "批量图册导出需要已认证用户（产物下载走属主通道）",
                    "code": "atlas_owner_required"}

        result = submit_atlas_job(spec)
        return {
            "status": result.get("status", "submitted"),
            "job_id": result.get("job_id"),
            "task_id": result.get("task_id"),
            "idempotent_reuse": result.get("idempotent_reuse", False),
            "message": result.get("message", ""),
            "poll_hint": f"GET /api/v1/tasks/jobs/{result.get('job_id')} 或 GET /api/v1/atlas/jobs/{result.get('job_id')}",
        }
