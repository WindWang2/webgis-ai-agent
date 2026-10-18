"""RendererCapabilityV1 —— 渲染器能力的 versioned 机器真值投影
（Goal 03 RenderWork Protocol V1，ADR-0225）。

与既有声明面的分工（**不造第二矩阵**）：

- 图层类型支持：唯一权威 = :mod:`app.lib.cartography.layer_capability`
  （``LAYER_TYPE_SUPPORT``，只描述已实现）—— 本模块**引用**其输出并
  versioned/fingerprint 化，不复制判定；
- 组件支持：唯一权威 = :mod:`app.lib.cartography.component_renderers`
  （``_SUPPORT_MATRIX``）—— 本模块只投影组件类型清单供对账；
- 源类型 / feature 面（clustering/heatmap/terrain/3D/representation/
  export）：本模块是**首个声明面**，truth = 前端
  ``frontend/lib/mapspec-compiler/compiler.ts`` 与 runtime 的实际消费
  实况（只描述已实现，不声明未来功能为 supported —— v1.6 渲染端可执行
  表达已端到端落地故 ``semantic_zoom_representation = true``；artifact
  模式（``generalization``）无预计算工件供给链故仍 = false）。

消费方向：

- 后端组合/规划面执行前检查 ``required_capabilities``（信封）；
- 前端/契约对账经 ``capability_fingerprint``（canonical 投影的 sha256）
  —— Python/TS 漂移由 parity gate 爆（contract manifest 三重锁）；
- ``design_system`` 以 ``renderCapabilityManifest`` 键导出（additive）。

本模块纯声明 + 纯函数，零 I/O、零状态。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable

from app.lib.cartography.layer_capability import LAYER_TYPE_SUPPORT

#: capability 声明契约版本。
CAPABILITY_SCHEMA_VERSION = "renderer_capability.v1"

#: 支持级别（与 layer_capability 同词表）。
LEVEL_FULL = "full"
LEVEL_PARTIAL = "partial"
LEVEL_NONE = "none"

# ── 源类型支持（truth = compiler.ts KNOWN_SOURCE_TYPES + ref 解析实况）──
# data_fabric：headless 编译器不直接消费 —— live 路径先经 ref 水合解析为
# geojson 才进编译器（ref-source-resolver）；声明 partial 并注明通道。
# wms/wmts/pmtiles：schema Literal 存在但无机器消费方 → none（保留词表位）。
SOURCE_TYPE_SUPPORT: Dict[str, Dict[str, Any]] = {
    "geojson": {
        "level": LEVEL_FULL,
        "channels": ["inline", "ref"],
        "note": "compiler.ts KNOWN_SOURCE_TYPES；inline/inlineData + ref 拉取两通道",
    },
    "vector": {
        "level": LEVEL_FULL,
        "channels": ["mvt-tiles"],
        "note": "tiles[] → MapLibre vector source（MVT 通道，不重写）",
    },
    "raster": {
        "level": LEVEL_FULL,
        "channels": ["imageRef"],
        "note": "RasterMapSpecSource imageRef/bounds 消费",
    },
    "raster-dem": {
        "level": LEVEL_FULL,
        "channels": ["url"],
        "note": "hillshade 数据面 + scene.terrain（ADR-0155/0199）",
    },
    "data_fabric": {
        "level": LEVEL_PARTIAL,
        "channels": ["ref-hydration"],
        "note": "先经 ref 水合解析为 geojson 再编译（headless 编译器不直接消费）",
    },
    "wms": {"level": LEVEL_NONE, "channels": [], "note": "schema 词表位，无机器消费方"},
    "wmts": {"level": LEVEL_NONE, "channels": [], "note": "schema 词表位，无机器消费方"},
    "pmtiles": {"level": LEVEL_NONE, "channels": [], "note": "schema 词表位，无机器消费方"},
}

# ── feature 面（truth = compiler.ts/runtime.ts 实况；只描述已实现）─────
FEATURE_SUPPORT: Dict[str, Dict[str, Any]] = {
    "point_clustering": {
        "supported": True,
        "note": "circle 层三态裁决（native/cluster/heatmap，compiler.ts 点密度裁决）",
    },
    "heatmap_density": {
        "supported": True,
        "note": "heatmap 层 + circle→heatmap 密度改写",
    },
    "label_placement": {
        "supported": True,
        "note": "symbol 标注通道 + zoomBands 分级抽稀（ac-05 运行时）",
    },
    "terrain": {
        "supported": True,
        "note": "scene.terrain → raster-dem（ADR-0199；瓦片失败有降级披露）",
    },
    "fill_extrusion_3d": {
        "supported": True,
        "note": "fill-extrusion 编译；视觉依赖地图 pitch",
    },
    "semantic_zoom_representation": {
        # #1617（MapSpec v1.6 RepresentationAtScale）已合并（2026-10-18）：
        # 渲染端可执行模式（raw/point_cluster/visual_heatmap）端到端落地 ——
        # 后端生产方 analysis_cartography_converter 落笔
        # layer.representation，前端 compiler（resolveRepresentationPlan）
        # 与 runtime（表达窗子层 / 源级 cluster）实际消费。artifact 模式
        # （aggregate_grid/generalized）需预计算工件，不可用 —— 见
        # generalization 键（红线：只声明已实现）。
        "supported": True,
        "note": "渲染端可执行模式（raw/point_cluster/visual_heatmap）已实现；artifact 模式无工件（见 generalization）",
    },
    "generalization": {
        # artifact-backed 表达模式（aggregate_grid/generalized）的 capability
        # seam：须调用方声明已实际可用的预计算工件 —— master 无 artifact
        # 供给链，故不声明 supported（红线：不把未来功能声明为已实现）。
        "supported": False,
        "note": "artifact 模式（aggregate_grid/generalized）需预计算工件；master 无供给链",
    },
    "export_svg": {
        "supported": True,
        "note": "mapspec_to_svg.py（Python/TS 双投影 parity）",
    },
    "export_pdf": {
        "supported": True,
        "note": "pdf_renderer.py",
    },
}


def layer_type_levels() -> Dict[str, str]:
    """图层类型 → 支持级别（单源引用 layer_capability，不复制判定）。"""
    return {t: s.level for t, s in sorted(LAYER_TYPE_SUPPORT.items())}


def component_type_keys() -> list:
    """组件类型清单（对账用；支持判定权威在 component_renderers）。"""
    try:
        from app.lib.cartography.component_renderers import (
            get_component_renderer_registry,
        )
        return sorted(get_component_renderer_registry().all_types)
    except Exception:  # pragma: no cover - 防御：注册表初始化失败不炸声明面
        return []


def capability_manifest() -> Dict[str, Any]:
    """canonical capability 投影（deterministic；fingerprint 的输入）。"""
    return {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "layer_types": layer_type_levels(),
        "source_types": {
            t: v["level"] for t, v in sorted(SOURCE_TYPE_SUPPORT.items())
        },
        "features": {
            k: bool(v["supported"]) for k, v in sorted(FEATURE_SUPPORT.items())
        },
        "component_types": component_type_keys(),
    }


def capability_fingerprint(manifest: Dict[str, Any] | None = None) -> str:
    """canonical manifest → sha256（Python/TS parity 锁的锚点）。"""
    canonical = json.dumps(
        manifest if manifest is not None else capability_manifest(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_required_capabilities(required: Iterable[Any]) -> Dict[str, Any]:
    """信封 ``required_capabilities`` → 支持裁决（封闭、有界、诚实）。

    返回 ``{supported: bool, unsupported: [keys], degraded: [keys]}``；
    ``component:<type>`` 键对账组件清单，其余对账 layer/source/feature
    词表。词表外键 → unsupported（不静默放行 —— 与 layer_capability
    对账测试同纪律）。
    """
    unsupported = []
    degraded = []
    layer_levels = layer_type_levels()
    for key in sorted(set(required or [])):
        key = str(key)[:64]
        if key.startswith("component:"):
            ctype = key[len("component:"):]
            if ctype not in component_type_keys():
                unsupported.append(key)
            continue
        if key in layer_levels:
            if layer_levels[key] == "none":
                unsupported.append(key)
            elif layer_levels[key] == "partial":
                degraded.append(key)
            continue
        if key in SOURCE_TYPE_SUPPORT:
            level = SOURCE_TYPE_SUPPORT[key]["level"]
            if level == LEVEL_NONE:
                unsupported.append(key)
            elif level == LEVEL_PARTIAL:
                degraded.append(key)
            continue
        if key in FEATURE_SUPPORT:
            if not FEATURE_SUPPORT[key]["supported"]:
                unsupported.append(key)
            continue
        unsupported.append(key)
    return {
        "supported": not unsupported,
        "unsupported": unsupported,
        "degraded": degraded,
    }


__all__ = [
    "CAPABILITY_SCHEMA_VERSION",
    "LEVEL_FULL",
    "LEVEL_PARTIAL",
    "LEVEL_NONE",
    "SOURCE_TYPE_SUPPORT",
    "FEATURE_SUPPORT",
    "layer_type_levels",
    "component_type_keys",
    "capability_manifest",
    "capability_fingerprint",
    "check_required_capabilities",
]
