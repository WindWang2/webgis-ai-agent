"""ModelOps agent tools（ADR-0119 §2；Epic §N）。

工具面契约：Harness 只见 typed capabilities 与结构化结果，**不触
provider internals**。全部入口经 :class:`ModelOpsService`（owner scope
恰好一维；推理可取消；异常 typed 带 correction_hint）。

capability id 复用既有词表（R1-m1：``image_segmentation`` 已存在于
``app/lib/gis/capabilities/raster.py:216``；不新增悬空 id）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

MAX_SOURCE_URI_LEN = 2048


def register_modelops_tools(registry: ToolRegistry) -> None:
    @tool(
        registry,
        name="modelops_list_models",
        description="列出当前 owner scope 可见的 GeoAI 推理模型（内置种子 + 自注册）",
        param_descriptions={
            "task_type": "按任务类型过滤（如 semantic_segmentation/object_detection）",
            "project_id": "项目 scope（与 session_id 二选一）",
            "session_id": "会话 scope（与 project_id 二选一）",
        },
        tier=2,
        domains=["raster"],
        cost="light",
        side_effect="pure",
        tags=("modelops", "geoai"),
        latency_class="fast",
        memory_class="light",
        capabilities=["image_segmentation"],
    )
    async def modelops_list_models(
        task_type: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.modelops.service import get_modelops_service, normalize_scope

        scope = normalize_scope(session_id=session_id, project_id=project_id)
        service = get_modelops_service()
        models = service.list_models(
            session_id=scope.get("session_id"),
            project_id=scope.get("project_id"),
            task_type=task_type,
        )
        return {"models": models, "count": len(models)}

    @tool(
        registry,
        name="modelops_inspect_model",
        description="检视一个 GeoAI 模型的完整描述符（能力/空间要求/资源/包校验报告）",
        param_descriptions={
            "model_id": "模型 id",
            "model_version": "可选；缺省取最新注册版本",
            "project_id": "项目 scope",
            "session_id": "会话 scope",
        },
        tier=2,
        domains=["raster"],
        cost="light",
        side_effect="pure",
        tags=("modelops", "geoai"),
        latency_class="fast",
        memory_class="light",
        capabilities=["image_segmentation"],
    )
    async def modelops_inspect_model(
        model_id: str,
        model_version: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.modelops.service import get_modelops_service, normalize_scope

        scope = normalize_scope(session_id=session_id, project_id=project_id)
        return get_modelops_service().inspect_model(
            model_id,
            model_version=model_version,
            session_id=scope.get("session_id"),
            project_id=scope.get("project_id"),
        )

    @tool(
        registry,
        name="modelops_check_compatibility",
        description="检查模型与输入栅格的语义兼容性（波段/分辨率/CRS/时序），失败 typed",
        param_descriptions={
            "model_id": "模型 id",
            "source_uri": "输入栅格路径（COG/GeoTIFF）",
            "project_id": "项目 scope",
            "session_id": "会话 scope",
        },
        tier=2,
        domains=["raster"],
        cost="light",
        side_effect="pure",
        tags=("modelops", "geoai"),
        latency_class="fast",
        memory_class="light",
        capabilities=["image_segmentation"],
    )
    async def modelops_check_compatibility(
        model_id: str,
        source_uri: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.modelops.service import get_modelops_service, normalize_scope

        scope = normalize_scope(session_id=session_id, project_id=project_id)
        return await get_modelops_service().check_compatibility_async(
            model_id, source_uri[:MAX_SOURCE_URI_LEN],
            session_id=scope.get("session_id"), project_id=scope.get("project_id"),
        )

    @tool(
        registry,
        name="modelops_estimate_resources",
        description="估算一次推理的资源需求（设备/批尺寸/tile 数/VRAM/host 内存）",
        param_descriptions={
            "model_id": "模型 id",
            "source_uri": "输入栅格路径",
            "project_id": "项目 scope",
            "session_id": "会话 scope",
        },
        tier=2,
        domains=["raster"],
        cost="light",
        side_effect="pure",
        tags=("modelops", "geoai"),
        latency_class="fast",
        memory_class="light",
        capabilities=["image_segmentation"],
    )
    async def modelops_estimate_resources(
        model_id: str,
        source_uri: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.modelops.service import get_modelops_service, normalize_scope

        scope = normalize_scope(session_id=session_id, project_id=project_id)
        return await get_modelops_service().estimate_resources_async(
            model_id, source_uri[:MAX_SOURCE_URI_LEN],
            session_id=scope.get("session_id"), project_id=scope.get("project_id"),
        )

    @tool(
        registry,
        name="modelops_run_inference",
        description="运行 GeoAI 推理（分割/检测/实例/嵌入/分类/时序/变化检测/融合），产物可渲染并带完整 provenance",
        param_descriptions={
            "model_id": "模型 id",
            "source_uri": "输入栅格路径（COG/GeoTIFF；懒窗口读取，绝不整幅加载）",
            "task_type": "多任务模型时必填（如 semantic_segmentation）",
            "source_uri_b": "双时相变化检测的后时相栅格（change_detection 必填）",
            "project_id": "项目 scope（与 session_id 二选一）",
            "session_id": "会话 scope",
            "score_threshold": "检测分数阈值（0-1）",
        },
        tier=2,
        domains=["raster"],
        cost="heavy",
        timeout=900.0,
        side_effect="artifact_creation",
        tags=("modelops", "geoai"),
        latency_class="slow",
        memory_class="heavy",
        capabilities=["image_segmentation"],
    )
    async def modelops_run_inference(
        model_id: str,
        source_uri: str,
        task_type: Optional[str] = None,
        source_uri_b: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        score_threshold: float = 0.5,
    ) -> dict:
        # 引擎契约在 services 平面（app/lib/modelops 是纯契约层，无 engine）。
        from app.services.modelops.engine import InferenceRequest
        from app.services.modelops.service import get_modelops_service, normalize_scope

        scope = normalize_scope(session_id=session_id, project_id=project_id)
        service = get_modelops_service()
        request = InferenceRequest(
            model_id=model_id,
            source_uri=source_uri[:MAX_SOURCE_URI_LEN],
            owner_scope=scope,
            task_type=task_type,
            source_uri_b=source_uri_b[:MAX_SOURCE_URI_LEN] if source_uri_b else None,
            score_threshold=float(score_threshold),
        )
        result = await service.run_inference_async(request)
        return _result_payload(result)

    @tool(
        registry,
        name="modelops_run_promptable",
        description="运行 promptable 分割（point/box prompt，像素坐标），输出目标掩膜",
        param_descriptions={
            "model_id": "promptable 模型 id",
            "source_uri": "输入栅格路径",
            "points": "点 prompt 列表 [[x,y],…]（像素坐标）",
            "boxes": "框 prompt 列表 [[x,y,w,h],…]（像素坐标）",
            "project_id": "项目 scope",
            "session_id": "会话 scope",
        },
        tier=2,
        domains=["raster"],
        cost="heavy",
        timeout=600.0,
        side_effect="artifact_creation",
        tags=("modelops", "geoai"),
        latency_class="slow",
        memory_class="heavy",
        capabilities=["image_segmentation"],
    )
    async def modelops_run_promptable(
        model_id: str,
        source_uri: str,
        points: Optional[List[List[float]]] = None,
        boxes: Optional[List[List[float]]] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.lib.modelops.errors import ModelOpsError
        from app.lib.modelops.promptable import PromptSpec
        from app.services.modelops.engine import InferenceRequest
        from app.services.modelops.service import get_modelops_service, normalize_scope

        if not points and not boxes:
            raise ModelOpsError(
                "promptable inference requires points or boxes",
                correction_hint="pass points=[[x,y],…] and/or boxes=[[x,y,w,h],…]",
            )
        scope = normalize_scope(session_id=session_id, project_id=project_id)
        prompt = PromptSpec(
            points=tuple(tuple(map(float, p)) for p in (points or [])),
            boxes=tuple(tuple(map(float, b)) for b in (boxes or [])),
        )
        request = InferenceRequest(
            model_id=model_id,
            source_uri=source_uri[:MAX_SOURCE_URI_LEN],
            owner_scope=scope,
            prompt=prompt,
        )
        result = await get_modelops_service().run_inference_async(request)
        return _result_payload(result)

    @tool(
        registry,
        name="modelops_evaluate_model",
        description="评估模型输出（IoU/F1/混淆矩阵 或 检测 P/R/AP），含空间泄漏审计",
        param_descriptions={
            "task_type": "segmentation | object_detection | classification",
            "predictions_path": "预测栅格路径（分割/分类）",
            "references_path": "参考标签栅格路径（分割/分类）",
            "project_id": "项目 scope",
            "session_id": "会话 scope",
        },
        tier=2,
        domains=["raster"],
        cost="heavy",
        timeout=600.0,
        side_effect="pure",
        tags=("modelops", "geoai"),
        latency_class="medium",
        memory_class="medium",
        capabilities=["image_segmentation"],
    )
    async def modelops_evaluate_model(
        task_type: str,
        predictions_path: str,
        references_path: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        from app.lib.modelops.errors import ModelOpsError
        from app.services.modelops.evaluation_service import EvaluationRequest
        from app.services.modelops.service import get_modelops_service, normalize_scope

        if task_type in ("segmentation", "classification") and not references_path:
            raise ModelOpsError(
                f"{task_type} evaluation requires references_path",
                correction_hint="pass the label raster path",
            )
        scope = normalize_scope(session_id=session_id, project_id=project_id)
        request = EvaluationRequest(
            owner_scope=scope,
            task_type=task_type,
            predictions_path=Path(predictions_path),
            references_path=Path(references_path) if references_path else None,
        )
        return await get_modelops_service().evaluate_async(request)

    @tool(
        registry,
        name="modelops_compare_results",
        description="对比两次推理的 manifest（模型/参数/性能/复用身份差异）",
        param_descriptions={
            "manifest_a": "第一个推理 manifest（JSON dict 或文件路径）",
            "manifest_b": "第二个推理 manifest",
        },
        tier=2,
        domains=["raster"],
        cost="light",
        side_effect="pure",
        tags=("modelops", "geoai"),
        latency_class="fast",
        memory_class="light",
        capabilities=["image_segmentation"],
    )
    async def modelops_compare_results(manifest_a: dict, manifest_b: dict) -> dict:
        from app.services.modelops.service import get_modelops_service

        return get_modelops_service().compare_results(manifest_a, manifest_b)

    @tool(
        registry,
        name="modelops_inspect_provenance",
        description="检视推理 manifest 的出处字段（模型/预处理/tile/性能/产物，已脱敏）",
        param_descriptions={
            "manifest": "推理 manifest（modelops_run_inference 返回的 manifest 字段）",
            "section": "可选：只看某一段（model/provider/preprocess/tile_plan/performance…）",
        },
        tier=2,
        domains=["raster"],
        cost="light",
        side_effect="pure",
        tags=("modelops", "geoai"),
        latency_class="fast",
        memory_class="light",
        capabilities=["image_segmentation"],
    )
    async def modelops_inspect_provenance(manifest: dict, section: Optional[str] = None) -> dict:
        from app.services.modelops.service import get_modelops_service

        return get_modelops_service().inspect_provenance(manifest, section=section)

    @tool(
        registry,
        name="modelops_cancel_inference",
        description="取消一次进行中的推理（取消键 = 提交返回的 run_id）",
        param_descriptions={"cancel_key": "取消键（modelops_run_inference 返回的 run_id）"},
        tier=2,
        domains=["raster"],
        cost="light",
        side_effect="state_mutation",
        tags=("modelops", "geoai"),
        latency_class="fast",
        memory_class="light",
        capabilities=["image_segmentation"],
    )
    async def modelops_cancel_inference(cancel_key: str) -> dict:
        from app.services.modelops.service import get_modelops_service

        cancelled = get_modelops_service().cancel(cancel_key)
        return {"cancelled": bool(cancelled), "cancel_key": cancel_key}


def _result_payload(result: Any) -> dict:
    """InferenceResult → 工具结果（产物路径/DataObject id/manifest/性能）。"""
    payload: Dict[str, Any] = {
        "run_id": result.run_id,
        "status": result.status,
        "reused": result.reused,
        "task_type": result.task_type,
        "outputs": {
            role: {k: v for k, v in out.items() if k != "path"} | {"path": out.get("path")}
            for role, out in (result.outputs or {}).items()
        },
        "performance": result.perf,
        "manifest": result.manifest,
    }
    if result.reuse_key:
        payload["reuse_key"] = result.reuse_key
    return payload
