"""GeoAI agent tools（Platform 11 / ADR-0198；WP-F）。

工具面契约：GeoPrompt artifact 检视/编译、可提示分割（含多候选）、
候选精化（refine = 以选定候选为先验的重跑）、embedding（带 cache 观测）、
语义类 zero-shot 映射。全部经 :class:`ModelOpsService`；typed 失败带
correction_hint；先验数组不经 JSON（服务进程内直传）。

refine 语义：读取先前 run 的 ``prompt_candidates`` GeoJSON，把选定候选
栅格化为 mask sidecar（内容寻址），作为先验 artifact 重新推理——精化是
显式的二次提交，不隐式篡改原 run。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

MAX_SOURCE_URI_LEN = 2048
#: semantic zero-shot 的逐 chip 明细上限（摘要 + 计数不受限）。
MAX_SEMANTIC_CHIP_DETAILS = 32


def register_geoai_tools(registry: ToolRegistry) -> None:
    @tool(
        registry,
        name="geoai_prompt_artifact_inspect",
        description=(
            "检视 GeoPrompt artifact：校验 schema/几何/CRS 声明、计算内容寻址 "
            "artifact_id、几何统计；提供 source_uri 时做编译 dry-run（含往返"
            "容差审计与派生先验来源），不执行推理"
        ),
        param_descriptions={
            "artifact": "GeoPrompt artifact JSON（SCHEMA 见 docs/geoai 或 ADR-0198）",
            "source_uri": "可选；目标栅格路径（提供时执行编译 dry-run 审计）",
            "session_id": "会话 scope（与 project_id 二选一）",
            "project_id": "项目 scope",
        },
        tier=2,
        domains=["raster"],
        cost="light",
        side_effect="pure",
        tags=("geoai", "modelops"),
        latency_class="fast",
        memory_class="light",
        capabilities=["image_segmentation"],
    )
    async def geoai_prompt_artifact_inspect(
        artifact: dict,
        source_uri: Optional[str] = None,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> dict:
        from app.lib.modelops.errors import ModelOpsError
        from app.lib.modelops.geo_prompt import GeoPromptArtifact

        try:
            parsed = GeoPromptArtifact.from_payload(artifact)
        except ModelOpsError as exc:
            return {"valid": False, "error": str(exc)}
        payload = parsed.to_payload()
        result: Dict[str, Any] = {
            "valid": True,
            "artifact_id": payload["artifact_id"],
            "crs": parsed.crs,
            "geometry": {
                "points": len(parsed.points),
                "boxes": len(parsed.boxes),
                "polylines": len(parsed.polylines),
                "polygons": len(parsed.polygons),
            },
            "has_text": bool(parsed.text),
            "has_mask_ref": parsed.mask_ref is not None,
            "has_reference_layer": parsed.reference_layer is not None,
            "time": payload.get("time"),
            "target": payload.get("target"),
        }
        if source_uri:
            from app.services.modelops.service import get_modelops_service

            compiled = get_modelops_service().compile_geo_prompt(
                artifact, source_uri[:MAX_SOURCE_URI_LEN]
            )
            result["compile_audit"] = compiled["audit"]
            result["prompt_geometry"] = compiled["prompt"].geometry_payload()
        return result

    @tool(
        registry,
        name="geoai_run_promptable",
        description=(
            "GeoAI 可提示分割：接受 GeoPrompt artifact（多边形/折线/参考层/mask "
            "sidecar/文本）或直接 points/boxes；可请求多 mask 候选（含质量分）"
            "与 best|index 裁决；产物为掩膜 GeoJSON/栅格 + 完整 provenance"
        ),
        param_descriptions={
            "model_id": "promptable 模型 id",
            "source_uri": "输入栅格路径（COG/GeoTIFF）",
            "artifact": "GeoPrompt artifact JSON（与 points/boxes 二选一优先）",
            "points": "点 prompt [[x,y],…]（像素坐标，除非 geographic_coords）",
            "boxes": "框 prompt [[x,y,w,h],…]",
            "geographic_coords": "points/boxes 为地图坐标（artifact 自带 CRS 声明时无需）",
            "return_candidates": "返回全部 mask 候选（GeoJSON + 分数 + 来源）",
            "candidate_selection": "best（默认）| index（配合 selected_candidate）",
            "selected_candidate": "selection=index 时候选下标",
            "session_id": "会话 scope（与 project_id 二选一）",
            "project_id": "项目 scope",
        },
        tier=2,
        domains=["raster"],
        cost="heavy",
        timeout=600.0,
        side_effect="artifact_creation",
        tags=("geoai", "modelops"),
        latency_class="slow",
        memory_class="heavy",
        capabilities=["image_segmentation"],
    )
    async def geoai_run_promptable(
        model_id: str,
        source_uri: str,
        artifact: Optional[dict] = None,
        points: Optional[List[List[float]]] = None,
        boxes: Optional[List[List[float]]] = None,
        geographic_coords: bool = False,
        return_candidates: bool = False,
        candidate_selection: str = "best",
        selected_candidate: Optional[int] = None,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> dict:
        from app.lib.modelops.errors import ModelOpsError
        from app.lib.modelops.promptable import PromptSpec
        from app.services.modelops.engine import InferenceRequest
        from app.services.modelops.service import get_modelops_service, normalize_scope

        service = get_modelops_service()
        scope = normalize_scope(session_id=session_id, project_id=project_id)
        uri = source_uri[:MAX_SOURCE_URI_LEN]
        if artifact:
            compiled = service.compile_geo_prompt(artifact, uri)
            prompt: PromptSpec = compiled["prompt"]
            artifact_id: Optional[str] = compiled["artifact_id"]
            audit: Optional[dict] = compiled["audit"]
        elif points or boxes:
            prompt = PromptSpec(
                points=tuple(tuple(map(float, p)) for p in (points or [])),
                boxes=tuple(tuple(map(float, b)) for b in (boxes or [])),
            )
            artifact_id = None
            audit = None
        else:
            raise ModelOpsError(
                "geoai_run_promptable requires artifact or points/boxes",
                correction_hint="pass a GeoPrompt artifact or points=[[x,y],…]",
            )
        request = InferenceRequest(
            model_id=model_id,
            source_uri=uri,
            owner_scope=scope,
            prompt=prompt,
            prompt_artifact_id=artifact_id,
            prompt_audit=audit,
            return_candidates=bool(return_candidates),
            candidate_selection=candidate_selection,
            selected_candidate=(
                int(selected_candidate) if selected_candidate is not None else None
            ),
        )
        if artifact is None and (points or boxes):
            request = _replace_prompt_crs(request, geographic=geographic_coords)
        result = await service.run_inference_async(request)
        payload = _result_payload(result)
        if artifact:
            payload["prompt_artifact_id"] = artifact_id
        return payload

    @tool(
        registry,
        name="geoai_prompt_refine",
        description=(
            "候选精化：以先前 run 的选定 mask 候选为先验重新推理（候选几何栅格化"
            "为内容寻址 mask sidecar → GeoPrompt artifact → 重跑）。refine 是显式"
            "二次提交，不修改原 run"
        ),
        param_descriptions={
            "model_id": "promptable 模型 id",
            "source_uri": "输入栅格路径（须与原 run 相同）",
            "run_outputs": "原 run 的 outputs 字典（含 prompt_candidates 角色）",
            "candidate": "选定候选下标（来自原 run 的候选列表）",
            "session_id": "会话 scope",
            "project_id": "项目 scope",
        },
        tier=2,
        domains=["raster"],
        cost="heavy",
        timeout=600.0,
        side_effect="artifact_creation",
        tags=("geoai", "modelops"),
        latency_class="slow",
        memory_class="heavy",
        capabilities=["image_segmentation"],
    )
    async def geoai_prompt_refine(
        model_id: str,
        source_uri: str,
        run_outputs: dict,
        candidate: int,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> dict:
        import rasterio
        from rasterio import features as _features
        from shapely.geometry import shape as _shape

        from app.lib.data.fingerprints import sha256_of_file
        from app.lib.geo_raster.reader import RasterReader
        from app.lib.modelops.errors import ModelOpsError
        from app.services.modelops.engine import InferenceRequest
        from app.services.modelops.service import get_modelops_service, normalize_scope

        cand_role = (run_outputs or {}).get("prompt_candidates") or {}
        cand_path = cand_role.get("path") if isinstance(cand_role, dict) else None
        if not cand_path or not Path(cand_path).exists():
            raise ModelOpsError(
                "run_outputs has no readable prompt_candidates artifact",
                correction_hint="re-run with return_candidates=True first",
            )
        features = json.loads(
            Path(cand_path).read_text(encoding="utf-8")
        ).get("features", [])
        chosen = [
            f for f in features
            if (f.get("properties") or {}).get("candidate") == int(candidate)
        ]
        if not chosen:
            available = sorted(
                {(f.get("properties") or {}).get("candidate") for f in features}
            )
            raise ModelOpsError(
                f"candidate {candidate} not present (available: {available})",
                correction_hint="pick one of the available candidate indexes",
            )
        service = get_modelops_service()
        scope = normalize_scope(session_id=session_id, project_id=project_id)
        uri = source_uri[:MAX_SOURCE_URI_LEN]
        with RasterReader.open(uri) as reader:
            meta = reader.metadata()
            transform = reader.dataset.transform
        mask = _features.rasterize(
            (( _shape(f["geometry"]), 1) for f in chosen),
            out_shape=(meta.height, meta.width),
            transform=transform,
            fill=0,
            dtype="uint8",
        ).astype(bool)
        if not mask.any():
            raise ModelOpsError(
                "chosen candidate rasterizes to an empty mask on this grid",
                correction_hint="candidate/source grid mismatch — rerun the "
                "original inference on this source",
            )
        ref_dir = Path(service._settings.registry_dir) / "prompt_refs"
        ref_dir.mkdir(parents=True, exist_ok=True)
        sidecar = ref_dir / f"refine-{sha256_of_file(str(Path(cand_path)))[:12]}-c{int(candidate)}.tif"
        with rasterio.open(
            sidecar, "w", driver="GTiff", width=meta.width, height=meta.height,
            count=1, dtype="uint8", crs=meta.crs, transform=transform,
        ) as dst:
            dst.write(mask.astype("uint8"), 1)
        payload = {
            "mask_ref": {
                "path": str(sidecar),
                "sha256": sha256_of_file(str(sidecar)),
                "band": 1,
            }
        }
        compiled = service.compile_geo_prompt(payload, uri)
        result = await service.run_inference_async(InferenceRequest(
            model_id=model_id,
            source_uri=uri,
            owner_scope=scope,
            prompt=compiled["prompt"],
            prompt_artifact_id=compiled["artifact_id"],
            prompt_audit=compiled["audit"],
        ))
        out = _result_payload(result)
        out["refined_from"] = {
            "candidate": int(candidate),
            "source_run_candidates": str(cand_path),
            "prior_pixels": int(mask.sum()),
        }
        return out

    @tool(
        registry,
        name="geoai_embed",
        description=(
            "GeoAI embedding 推理（逐窗特征向量），附带 embedding cache 观测"
            "（命中/未命中/条目/字节）与失效入口提示"
        ),
        param_descriptions={
            "model_id": "embedding 模型 id",
            "source_uri": "输入栅格路径",
            "session_id": "会话 scope",
            "project_id": "项目 scope",
        },
        tier=2,
        domains=["raster"],
        cost="heavy",
        timeout=600.0,
        side_effect="artifact_creation",
        tags=("geoai", "modelops"),
        latency_class="slow",
        memory_class="medium",
        capabilities=["image_segmentation"],
    )
    async def geoai_embed(
        model_id: str,
        source_uri: str,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> dict:
        from app.services.modelops.engine import InferenceRequest
        from app.services.modelops.service import get_modelops_service, normalize_scope

        service = get_modelops_service()
        scope = normalize_scope(session_id=session_id, project_id=project_id)
        result = await service.run_inference_async(InferenceRequest(
            model_id=model_id,
            source_uri=source_uri[:MAX_SOURCE_URI_LEN],
            owner_scope=scope,
            task_type="embedding",
        ))
        payload = _result_payload(result)
        payload["embedding_cache"] = service.embedding_cache_stats()
        return payload

    @tool(
        registry,
        name="geoai_semantic_zero_shot",
        description=(
            "语义类 zero-shot：注册类别原型并把 embedding run 的逐窗向量映射到"
            "类别（余弦 top-k）。无文本 encoder 接线时 typed 拒绝（平台不伪装"
            "文本理解）；stub encoder 的结果显式标注 stub=true"
        ),
        param_descriptions={
            "classes": "类别名列表（如 ['water','forest','urban']）",
            "model_id": "embedding 模型 id（须与 encoder 维度一致）",
            "source_uri": "输入栅格路径",
            "top_k": "每窗返回的 top-k（默认 1）",
            "session_id": "会话 scope",
            "project_id": "项目 scope",
        },
        tier=2,
        domains=["raster"],
        cost="heavy",
        timeout=600.0,
        side_effect="state_mutation",
        tags=("geoai", "modelops"),
        latency_class="slow",
        memory_class="medium",
        capabilities=["image_segmentation"],
    )
    async def geoai_semantic_zero_shot(
        classes: List[str],
        model_id: str,
        source_uri: str,
        top_k: int = 1,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> dict:
        import numpy as np

        from app.services.modelops.engine import InferenceRequest
        from app.services.modelops.service import get_modelops_service, normalize_scope

        service = get_modelops_service()
        scope = normalize_scope(session_id=session_id, project_id=project_id)
        registered = service.register_semantic_classes(classes, replace=True)
        result = await service.run_inference_async(InferenceRequest(
            model_id=model_id,
            source_uri=source_uri[:MAX_SOURCE_URI_LEN],
            owner_scope=scope,
            task_type="embedding",
        ))
        items = json.loads(
            Path(result.outputs["embeddings"]["path"]).read_text(encoding="utf-8")
        )
        per_chip = []
        counter: Dict[str, int] = {}
        for item in items:
            vec = np.asarray(item["vector"], dtype=np.float32).tolist()
            mapped = service.semantic_zero_shot(vec, top_k=top_k)
            label = mapped["top"][0]["class"]
            counter[label] = counter.get(label, 0) + 1
            if len(per_chip) < MAX_SEMANTIC_CHIP_DETAILS:
                per_chip.append({
                    "chip_index": item.get("chip_index"),
                    "window": item.get("core_window"),
                    "top": mapped["top"],
                })
        return {
            "registered": registered,
            "chips_total": len(items),
            "label_distribution": dict(sorted(counter.items(), key=lambda kv: -kv[1])),
            "per_chip": per_chip,
            "details_truncated": len(items) > len(per_chip),
            "run_id": result.run_id,
        }


def _replace_prompt_crs(request, *, geographic: bool):
    from dataclasses import replace as _dc_replace

    return _dc_replace(request, prompt_crs=bool(geographic))


def _result_payload(result: Any) -> dict:
    """InferenceResult → 工具结果（与 modelops 工具面同口径）。"""
    payload: Dict[str, Any] = {
        "run_id": result.run_id,
        "status": result.status,
        "reused": result.reused,
        "task_type": result.task_type,
        "outputs": dict(result.outputs or {}),
        "performance": result.perf,
        "manifest": result.manifest,
    }
    if result.reuse_key:
        payload["reuse_key"] = result.reuse_key
    return payload
