"""Layer delivery —— ModelOps 输出 → 地图图层（V3 §I）。

推理产物（DataObject/文件）到 Workbench 图层的最后一公里：

- :func:`build_layer_packages`：InferenceResult 的 outputs + manifest →
  逐 role 的图层包（数据载荷 / 图层类型 / 名称建议 / **style
  recommendation** / provenance 链）；
- :func:`style_recommendation`：确定性样式建议（类别 → 封闭调色板；
  置信度 → 顺序渐变；变化图 → 专用双色）；**建议**而非强制——前端/
  Cartography 保留最终裁量；
- :func:`publish_layers`：渲染型 role 经 ``session_data_manager.store``
  注册为会话数据引用（raster = ``{"path": …}``；vector = GeoJSON
  FeatureCollection），返回 ref_id + alias——agent 随即可用
  ``display_layer``/``finalize_display`` 收口，产物即图层。

provenance 链（不可裁剪）：model_id/version/checksum/provider/run_id/
reuse_key 随每个图层包下发并写进 ref payload 的 ``_modelops`` 字段。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 渲染型 role → 图层类型（封闭映射；其余 role 不进图层）。
RASTER_ROLES = frozenset({"classes", "confidence", "superres", "temporal_forecast", "prompt_mask"})
VECTOR_ROLES = frozenset(
    {"detections", "instance_polygons", "class_polygons", "prompt_mask_geojson"}
)

#: 类别调色板（封闭、确定、色觉安全的定性色；按类索引循环）。
CATEGORICAL_PALETTE = (
    "#4C78A8", "#F58518", "#54A24B", "#B279A2", "#EECA3B",
    "#72B7B2", "#FF9DA6", "#9D755D", "#BAB0AC", "#D67195",
    "#8CD17D", "#6B4E9E", "#E7BA52", "#FABFD2", "#B6992D",
    "#2B3A67", "#C44E52", "#3C8E7E", "#7F6BAE", "#A1A1A1",
)

#: 变化图专用（no-change/change）。
CHANGE_PALETTE = ("#D9D9D9", "#C44E52")


def _class_palette(class_names: Optional[List[str]]) -> Dict[str, Any]:
    if class_names and len(class_names) == 2 and class_names[0] in ("no_change", "background"):
        return {"type": "categorical", "palette": list(CHANGE_PALETTE[: len(class_names)])}
    count = max(1, len(class_names or []))
    return {
        "type": "categorical",
        "palette": list(CATEGORICAL_PALETTE[: min(20, max(2, count))]),
        "class_names": list(class_names) if class_names else None,
    }


def style_recommendation(
    role: str,
    *,
    class_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """确定性样式建议（role 驱动；不依赖运行时状态）。"""
    if role == "classes":
        return {
            "kind": "raster_categorical",
            **_class_palette(class_names),
            "opacity": 0.75,
            "nodata_value": 255,
        }
    if role == "prompt_mask":
        return {
            "kind": "raster_categorical",
            "type": "categorical",
            "palette": ["#D9D9D9", "#4C78A8"],
            "opacity": 0.7,
            "nodata_value": 255,
        }
    if role in ("confidence",):
        return {
            "kind": "raster_continuous",
            "type": "sequential",
            "ramp": ["#F7F7F7", "#9ECAE1", "#31A354"],
            "opacity": 0.6,
            "nodata_value": 0,
        }
    if role in ("superres", "temporal_forecast"):
        return {
            "kind": "raster_continuous",
            "type": "sequential",
            "ramp": ["#FFF7BC", "#FEC44F", "#D95F0E"],
            "opacity": 1.0,
        }
    if role in ("detections", "instance_polygons"):
        return {
            "kind": "vector",
            "stroke_color": CATEGORICAL_PALETTE[1],
            "fill_opacity": 0.15,
            "stroke_width": 2,
            "label_property": "label_name" if role == "detections" else "instance_id",
        }
    # class_polygons / prompt_mask_geojson
    return {
        "kind": "vector",
        "style_by_property": "class_name",
        "fill_opacity": 0.45,
        "stroke_width": 1,
    }


def _provenance(manifest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    model = (manifest or {}).get("model") or {}
    provider = (manifest or {}).get("provider") or {}
    return {
        "capability": "modelops.inference",
        "model_id": model.get("model_id"),
        "model_version": model.get("model_version"),
        "checksum": model.get("checksum"),
        "provider_ref": provider.get("provider_ref"),
        "run_id": (manifest or {}).get("run_id"),
        "reuse_key": (manifest or {}).get("reuse_key"),
    }


def build_layer_packages(
    outputs: Dict[str, Dict[str, Any]],
    manifest: Optional[Dict[str, Any]] = None,
    *,
    class_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """InferenceResult.outputs → 图层包清单（渲染型 role 全集）。"""
    provenance = _provenance(manifest)
    task = (manifest or {}).get("task_type")
    class_names = class_names or _class_names_from_manifest(manifest)
    packages: List[Dict[str, Any]] = []
    for role, payload in (outputs or {}).items():
        if role in RASTER_ROLES:
            layer_kind = "raster"
        elif role in VECTOR_ROLES:
            layer_kind = "vector"
        else:
            continue
        path = payload.get("path")
        if layer_kind == "raster":
            data: Any = {"path": str(path)} if path else None
        else:
            data = payload.get("feature_collection")
            if data is None and path:
                # GeoJSON 文件产物由调用方读出（tool 层注入 feature_collection）。
                data = None
        packages.append(
            {
                "role": role,
                "layer_kind": layer_kind,
                "data_object_id": payload.get("data_object_id"),
                "path": str(path) if path else None,
                "data": data,
                "name": _layer_name(role, task),
                "style": style_recommendation(role, class_names=class_names),
                "provenance": provenance,
            }
        )
    return packages


def _class_names_from_manifest(manifest: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    model = (manifest or {}).get("model") or {}
    schema = model.get("class_schema") or {}
    classes = schema.get("classes")
    return list(classes) if classes else None


def _layer_name(role: str, task: Optional[str]) -> str:
    role_names = {
        "classes": "GeoAI 类别栅格",
        "confidence": "GeoAI 置信度",
        "superres": "GeoAI 超分辨率",
        "temporal_forecast": "GeoAI 时序预测",
        "prompt_mask": "GeoAI 目标掩膜",
        "detections": "GeoAI 检测",
        "instance_polygons": "GeoAI 实例",
        "class_polygons": "GeoAI 类别多边形",
        "prompt_mask_geojson": "GeoAI 目标轮廓",
    }
    base = role_names.get(role, f"GeoAI {role}")
    return f"{base}（{task}）" if task and role in ("classes", "class_polygons") else base


async def publish_layers(
    outputs: Dict[str, Dict[str, Any]],
    manifest: Optional[Dict[str, Any]],
    *,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    feature_collections: Optional[Dict[str, Dict[str, Any]]] = None,
    session_data_manager: Any = None,
    class_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """图层包 → 会话数据引用（agent 可直接 display_layer / finalize）。

    owner scope 恰好一维；store 失败 honest 记录（产物文件仍在）。
    """
    if bool(session_id) == bool(project_id):
        from app.lib.modelops.errors import ModelOpsError

        raise ModelOpsError("owner scope requires exactly one of session_id / project_id")
    if session_data_manager is None:
        from app.services.session_data import session_data_manager
    packages = build_layer_packages(outputs, manifest, class_names=class_names)
    refs: List[Dict[str, Any]] = []
    for package in packages:
        data = package.pop("data", None)
        if data is None:
            fc = (feature_collections or {}).get(package["role"])
            if fc is not None and package["layer_kind"] == "vector":
                data = fc
        if data is None:
            package["registered"] = False
            package["reason"] = "no renderable payload (vector needs feature_collection)"
            refs.append(package)
            continue
        payload = dict(data) if isinstance(data, dict) else data
        if isinstance(payload, dict):
            payload["_modelops"] = package["provenance"]
        try:
            ref_id = await session_data_manager.store(
                session_id or project_id, payload, prefix="modelops"
            )
            package["ref_id"] = ref_id
            package["alias"] = f"modelops_{package['role']}"
            await session_data_manager.set_alias(
                session_id or project_id, ref_id, package["alias"]
            )
            package["registered"] = True
        except Exception as exc:  # noqa: BLE001 — 图层注册失败不毁产物
            logger.warning("layer registration for role %s failed: %s", package["role"], exc)
            package["registered"] = False
            package["reason"] = str(exc)[:200]
        refs.append(package)
    return {
        "layers": refs,
        "layer_count": len(refs),
        "registered_count": sum(1 for r in refs if r.get("registered")),
        "provenance": _provenance(manifest),
    }


__all__ = [
    "RASTER_ROLES",
    "VECTOR_ROLES",
    "build_layer_packages",
    "publish_layers",
    "style_recommendation",
]
