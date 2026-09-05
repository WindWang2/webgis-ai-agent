"""工具结果契约视图（ADR-0101 Wave 2, §11）。

现状：200+ 工具返回**约定形状的 dict**（std_error_response 错误形状、
llm_payload、ref: 游标、GeoJSON FC……），这是既成事实契约，重写所有工具
既不必要也高风险。本模块不改变任何工具的返回，而是提供**只读适配视图**
``inspect_tool_result(result) -> ToolResultView``，把既有约定收敛为一个
稳定的结构化词汇，供：

- no-progress / 重复调用检测 V2（Wave 6）判定「结果是否真的有进展」；
- replay harness（Wave 8）对结果契约做等价比较（而不是 bytes 级 diff）；
- debug bundle 与 trace 摘要；
- 评测 corpus 断言。

大结果永远以 ref + 有界摘要暴露给模型 —— 摘要边界由 ``bounded_summary``
统一执行（服务层 slim_tool_result 继续负责 LLM payload 的最终裁剪）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 复用全库既有的错误形状判定（#529/#589 家族）与预算化字节估计
from app.services.llm_result_formatter import is_error_like_result  # noqa: F401
from app.lib.json_size import estimate_json_bytes


class OutputSemanticType(str, Enum):
    """工具输出的语义类型（结果契约的一部分，重放比较时按类型分派）。"""

    UNKNOWN = "unknown"
    TEXT = "text"                       # str 或 {text: str} 形态
    GEOJSON_FC = "geojson_fc"           # FeatureCollection（含 data 包裹）
    GEOMETRY = "geometry"               # 单几何 / Feature
    HEATMAP = "heatmap"                 # 热力/表面数据（grid/points + 渲染参数）
    CHART = "chart"                     # 图表规格
    TABLE = "table"                     # 记录数组
    MAP_PRODUCT = "map_product"         # 地图产品装配结果
    MAPSPEC = "mapspec"                 # MapSpec 显示状态
    ERROR = "error"                     # 错误形状 dict


_FC_KEYS = ("geojson", "feature_collection", "features")
_DATA_WRAPPED_KEYS = ("data",)
_REF_KEYS = ("ref", "ref_id", "geojson_ref", "data_ref", "layer_ref", "primary_ref",
             "overlay_refs", "ref_cursor", "chartRef", "tableRef", "imageRef")
_ARTIFACT_HINT_KEYS = ("artifact_id", "artifact_ref", "artifacts")

_REF_PREFIX = "ref:"


@dataclass(frozen=True)
class ToolResultView:
    """工具结果的规范化只读视图。"""

    ok: bool
    semantic_type: OutputSemanticType = OutputSemanticType.UNKNOWN
    error_code: Optional[str] = None
    error_type: Optional[str] = None
    message: str = ""                     # 有界错误/首行消息
    correction_hint: str = ""
    ref_ids: Tuple[str, ...] = ()         # 结果中暴露的 ref 游标
    artifact_ids: Tuple[str, ...] = ()
    approx_bytes: int = 0
    approx_bytes_truncated: bool = False  # 预算耗尽 → 近似值
    warnings: Tuple[str, ...] = ()

    def contract_key(self) -> Dict[str, Any]:
        """重放比较键：形状级特征（不含大数据本体）。"""
        return {
            "ok": self.ok,
            "semantic_type": self.semantic_type.value,
            "error_code": self.error_code,
            "ref_count": len(self.ref_ids),
            "artifact_count": len(self.artifact_ids),
        }


def _classify_shape(result: Any) -> OutputSemanticType:
    if isinstance(result, str):
        return OutputSemanticType.TEXT
    if not isinstance(result, dict):
        return OutputSemanticType.UNKNOWN
    # data 包裹（dispatch service 的 ref 卸载通道之前的工具原生形态）
    inner = result.get("data") if isinstance(result.get("data"), dict) else None
    probe = inner if inner is not None else result
    probe_type = probe.get("type")
    if probe_type is None and isinstance(result.get("geojson"), dict):
        probe = result["geojson"]
        probe_type = probe.get("type")
    if probe_type == "FeatureCollection":
        return OutputSemanticType.GEOJSON_FC
    if probe_type == "Feature" or (
        isinstance(probe_type, str) and probe_type in {
            "Point", "MultiPoint", "LineString", "MultiLineString",
            "Polygon", "MultiPolygon", "GeometryCollection",
        }
    ):
        return OutputSemanticType.GEOMETRY
    if "heatmap" in probe or probe.get("render_type"):
        return OutputSemanticType.HEATMAP
    if "chart_type" in probe or "chart_spec" in probe:
        return OutputSemanticType.CHART
    if "mapspec" in result or "mutation_revision" in result:
        return OutputSemanticType.MAPSPEC
    if "product" in result or "map_product" in result:
        return OutputSemanticType.MAP_PRODUCT
    if "text" in result and isinstance(result.get("text"), str):
        return OutputSemanticType.TEXT
    if isinstance(result.get("items"), list) or isinstance(result.get("records"), list):
        return OutputSemanticType.TABLE
    return OutputSemanticType.UNKNOWN


def _collect_refs(result: Any, budget: int = 64) -> Tuple[str, ...]:
    """收集结果中的 ref 游标字符串（键名引导 + ref: 前缀兜底，有界）。"""
    refs: list[str] = []

    def _walk(node: Any, depth: int) -> None:
        if len(refs) >= budget or depth > 4:
            return
        if isinstance(node, str):
            if node.startswith(_REF_PREFIX) and node not in refs:
                refs.append(node)
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and (
                    v.startswith(_REF_PREFIX) or (k in _REF_KEYS and v.startswith("ref:"))
                ):
                    if v not in refs:
                        refs.append(v)
                else:
                    _walk(v, depth + 1)
        elif isinstance(node, list):
            for item in node[:64]:
                _walk(item, depth + 1)

    _walk(result, 0)
    return tuple(refs[:budget])


def inspect_tool_result(result: Any) -> ToolResultView:
    """把任意工具返回值适配为 ``ToolResultView``（永不抛异常）。"""
    try:
        approx_budget = [4096]
        approx_bytes = estimate_json_bytes(result, _budget=approx_budget) if result is not None else 0
        truncated = approx_budget[0] <= 0

        if isinstance(result, dict) and (
            result.get("success") is False or is_error_like_result(result)
        ):
            message = str(
                result.get("message") or result.get("error") or ""
            )[:600]
            return ToolResultView(
                ok=False,
                semantic_type=OutputSemanticType.ERROR,
                error_code=str(result.get("code") or "TOOL_ERROR"),
                error_type=str(result.get("error_type") or "") or None,
                message=message,
                correction_hint=str(result.get("correction_hint") or "")[:400],
                approx_bytes=approx_bytes,
                approx_bytes_truncated=truncated,
            )

        semantic = _classify_shape(result)
        refs = _collect_refs(result)
        artifacts = tuple(
            str(result[k])[:120] for k in _ARTIFACT_HINT_KEYS
            if isinstance(result, dict) and result.get(k)
        )
        warnings = tuple(
            str(w)[:200] for w in (result.get("warnings") or [])[:8]
        ) if isinstance(result, dict) else ()
        return ToolResultView(
            ok=True,
            semantic_type=semantic,
            ref_ids=refs,
            artifact_ids=artifacts,
            approx_bytes=approx_bytes,
            approx_bytes_truncated=truncated,
            warnings=warnings,
        )
    except Exception as exc:  # noqa: BLE001 — 视图永不影响执行
        # review R1 minor：fail-CLOSED —— 视图构建失败不能伪装成「成功未知
        # 类型」（no-progress / replay 会把失败当进展）。诚实返回失败视图。
        logger.debug("inspect_tool_result failed: %s", exc)
        return ToolResultView(
            ok=False,
            semantic_type=OutputSemanticType.UNKNOWN,
            error_code="INSPECTION_FAILED",
            message=str(exc)[:200],
        )


def bounded_summary(result: Any, max_chars: int = 400) -> str:
    """结果的有界文本摘要（trace / debug bundle 用，不做语义解析）。"""
    view = inspect_tool_result(result)
    if not view.ok:
        head = f"[{view.error_code}] {view.message}"
        return head[:max_chars]
    base = f"ok type={view.semantic_type.value}"
    if view.ref_ids:
        base += f" refs={list(view.ref_ids[:4])}"
    if view.approx_bytes_truncated:
        base += " bytes≈(huge,approx)"
    else:
        base += f" bytes≈{view.approx_bytes}"
    return base[:max_chars]
