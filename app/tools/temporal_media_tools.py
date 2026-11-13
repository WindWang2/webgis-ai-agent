"""WP10 — Pi 标准通道（能力 / 预览 / 提交 / 查询 / 取消 / 成果）。

与 UI 共用 ``app.services.temporal_media.service``，不绕过源权限、缺期披露、
资源预检或 partial 选择。参数由关闭词表的 Pydantic 模型校验（不接受路径、
URL、shell 参数）；工具只返回有界摘要和授权 ref，不携带媒体字节。

``side_effect`` / ``produced_refs`` / ``memory`` / ``latency`` / ``scale`` 如实登记：
提交并不产出成果，只产出 pending 状态；成果只在 completed 后由 result 返回。
"""
from __future__ import annotations

from typing import Any, Dict

from app.tools.registry import ToolRegistry, tool

from app.services.temporal_media import contracts as C
from app.services.temporal_media import service as media_service


def _error(exc: C.TemporalMediaError) -> Dict[str, Any]:
    payload = exc.to_payload()
    hints = {
        "NO_TEMPORAL_BINDING": "先调用 temporal_view_bind 绑定真实观测帧",
        "PLAN_VERSION_MISMATCH": "重新调用 webgis_temporal_media_plan 取得当前 plan_hash",
        "RESULT_NOT_READY": "继续用 webgis_temporal_media_status 查询，pending 不是完成",
        "BLOCKED_INCOMPLETE": "显式选择 partial_policy='allow_partial' 并接受 omitted_layers 披露",
        "RESOURCE_BUDGET_EXCEEDED": "缩小 frame_ids / 尺寸 / fps / dwell_ms",
        "CAS_SUPERSEDED": "重新预览计划以取得新 MapSpec revision",
    }
    payload["correction_hint"] = hints.get(exc.code, "按 code 修正输入后重试")
    return payload


async def _invoke(fn: Any, model: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    try:
        args = model(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "code": "INVALID_ARGS", "message": str(exc)[:300]}
    if not args.session_id:
        return {"success": False, "code": "SOURCE_UNAVAILABLE",
                "message": "Missing session_id context"}
    principal = media_service._principal(None, args.session_id)
    try:
        return await fn(args, principal)
    except C.TemporalMediaError as exc:
        return _error(exc)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "code": "MEDIA_TOOL_ERROR",
                "message": type(exc).__name__}


def register_temporal_media_tools(registry: ToolRegistry) -> None:
    """注册 G35 六个标准工具（Pi 与 UI 同一服务链）。"""

    @tool(
        registry,
        name="webgis_temporal_media_capabilities",
        domains=["cartography", "temporal", "delivery"],
        tier=2,
        args_model=C.CapabilitiesArgs,
        description=(
            "查询时序地图动画媒体的真实能力：编码器/版本、可用格式（APNG 无损核心、"
            "GIF 有损兼容）、尺寸/帧数/时长硬帽、rsvg 栅格器是否可用、视频 codec "
            "probe 与部署缺失、以及当前会话 temporal 绑定与不可离屏重现图层清单。"
            "media plan 前先调用它；依赖缺失会 typed 拒绝而非猜编码成功。"
        ),
        side_effect="cacheable_read", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="json", produced_refs=(),
        tags=("时序", "动画", "媒体", "能力"),
    )
    async def webgis_temporal_media_capabilities(**kwargs: Any) -> Dict[str, Any]:
        return await _invoke(media_service.capabilities, C.CapabilitiesArgs, kwargs)

    @tool(
        registry,
        name="webgis_temporal_media_plan",
        domains=["cartography", "temporal", "delivery"],
        tier=2,
        args_model=C.MediaPlanArgs,
        description=(
            "预览冻结的时序动画媒体计划：真实观测桶、缺期与实际时间跨度、"
            "等时长或按真实间隔缩放的逐段时长、时间标注、固定窗口/分类、"
            "画面尺寸/格式/循环、资源预估与完整披露。返回 plan_hash；"
            "提交必须使用同一 hash（未知/漂移版本拒绝）。预览不是渲染结果。"
        ),
        # preview 会持久化 owner 作用域的预览计划（产出 plan_ref），不是纯读。
        side_effect="state_mutation", deterministic=False, network=False,
        latency_class="medium", memory_class="light", scale_class="medium",
        output_semantic_type="json", produced_refs=("plan_ref",),
        tags=("时序", "动画", "计划", "缺期"),
    )
    async def webgis_temporal_media_plan(**kwargs: Any) -> Dict[str, Any]:
        return await _invoke(media_service.preview, C.MediaPlanArgs, kwargs)

    @tool(
        registry,
        name="webgis_temporal_media_submit",
        domains=["cartography", "temporal", "delivery"],
        tier=2,
        args_model=C.SubmitArgs,
        description=(
            "按已预览的计划版本提交时序动画媒体任务（冻结计划→逐帧真实渲染→"
            "有界编码→独立解码核验→原子发布）。优先传 webgis_temporal_media_plan "
            "返回的 16 位 plan_ref（短句柄不会被上下文预算截断）；也可传完整 "
            "plan_hash。返回 pending + job_id；**pending 不是完成**，必须用 status "
            "查询到 completed 且 verification.passed 后才能用 result 取授权下载。"
            "未知/漂移计划版本拒绝。"
        ),
        # 工具本身只写两个 JSON + 入队即返回 pending；逐帧渲染/编码的耗时
        # 属于后台 job 阶段，不记到派发工具的资源分级上。
        side_effect="state_mutation", deterministic=False, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="reference", result_size_policy="ref_offload",
        produced_refs=("job",), idempotent=True,
        tags=("时序", "动画", "提交", "后台任务"),
        failure_modes=("plan_version_mismatch", "blocked_incomplete",
                       "resource_budget_exceeded", "renderer_unavailable"),
    )
    async def webgis_temporal_media_submit(**kwargs: Any) -> Dict[str, Any]:
        return await _invoke(media_service.submit, C.SubmitArgs, kwargs)

    @tool(
        registry,
        name="webgis_temporal_media_status",
        domains=["cartography", "temporal", "delivery"],
        tier=2,
        args_model=C.JobRefArgs,
        description=(
            "查询媒体任务真实阶段（freezing/rendering/encoding/verifying/publishing）、"
            "有界进度、唯一渲染帧数、缓存命中、编码帧数、失败原因，"
            "以及 completed 后的授权成果引用。总帧数已知才显示百分比。"
            "pending/queued/tool-returned 一律不升级为完成。"
        ),
        side_effect="cacheable_read", deterministic=False, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="json", produced_refs=(),
        tags=("时序", "动画", "状态", "恢复"),
    )
    async def webgis_temporal_media_status(**kwargs: Any) -> Dict[str, Any]:
        return await _invoke(media_service.status, C.JobRefArgs, kwargs)

    @tool(
        registry,
        name="webgis_temporal_media_cancel",
        domains=["cartography", "temporal", "delivery"],
        tier=2,
        args_model=C.JobRefArgs,
        description=(
            "取消尚未完成的媒体任务：停止后续帧渲染与编码、清理本任务临时文件；"
            "cancelled 是终态，worker 迟到成功不得覆盖。已完成并核验发布的成果不可取消。"
        ),
        side_effect="state_mutation", deterministic=False, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="json", produced_refs=(),
        tags=("时序", "动画", "取消"),
    )
    async def webgis_temporal_media_cancel(**kwargs: Any) -> Dict[str, Any]:
        return await _invoke(media_service.cancel, C.JobRefArgs, kwargs)

    @tool(
        registry,
        name="webgis_temporal_media_result",
        domains=["cartography", "temporal", "delivery"],
        tier=2,
        args_model=C.ResultArgs,
        description=(
            "读取已完成且独立核验通过的成果：可播放动画文件、PNG 数字帧/manifest "
            "证据包、manifest 的授权下载 ref 与有界摘要。未完成时 typed 拒绝，"
            "不把 pending 冒充 finished；签名 URL 过期可重新鉴权签发。"
        ),
        side_effect="cacheable_read", deterministic=False, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        output_semantic_type="reference", result_size_policy="ref_offload",
        produced_refs=("artifact", "manifest"),
        tags=("时序", "动画", "下载", "证据"),
    )
    async def webgis_temporal_media_result(**kwargs: Any) -> Dict[str, Any]:
        return await _invoke(media_service.result, C.ResultArgs, kwargs)


__all__ = ["register_temporal_media_tools"]
