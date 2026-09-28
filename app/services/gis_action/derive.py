"""Classification 派生重算：参数级语义变化的确定性展开（H10 / ADR-0217）。

修复的缺口：PlanAmendment/Blueprint 携带 ``classification: {k, method,
palette}`` 走编译器路径时只产出 ``legend_spec={"classification":…}`` 裸
token —— 改分级数只改了数字字段，breaks/legend/paint 全部不变。本模块把
"分级参数" 升格为 **derived 重算动作语义**：

- ``derive_classification_spec(values, field, …)``：值注入的纯派生
  （thematic_spec.build_graduated_spec 薄壳 —— single classification
  纪律不变，与工具路径同源同结果，parity 测试锁定）；
- ``materialize_classification(ir, …)``：投影期物化 —— blueprint 带
  classification 参数而 legend_spec 缺 breaks 时，经注入 loader 取数 →
  物化完整 legend_spec。**compiler 保持纯函数**；物化只发生在 services
  投影层（与 project_plan_ir 同层）。

失败语义（绝不静默）：数据缺失/源不可解析 → typed MaterializationRecord，
IR 原样保留（回落既有 token 行为 = 向后兼容），由 receipt/finding 披露。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from app.lib.cartography.plan_ir import MapPlanIR
from app.lib.cartography.plan_ir import digest_of as _ir_digest

logger = logging.getLogger(__name__)

__all__ = [
    "ClassificationParams",
    "MaterializationRecord",
    "derive_classification_spec",
    "materialize_classification",
    "classification_params_of",
]

GeojsonLoader = Callable[[str], Awaitable[Optional[Dict[str, Any]]]]

#: 物化词表（typed 原因；自由文本禁入）。
DERIVE_SOURCE_UNRESOLVED = "DERIVE_SOURCE_UNRESOLVED"
DERIVE_NO_DATA = "DERIVE_NO_DATA"
DERIVE_NO_FIELD = "DERIVE_NO_FIELD"
DERIVE_OK = "DERIVE_OK"
DERIVE_SKIPPED = "DERIVE_SKIPPED"


@dataclass(frozen=True)
class ClassificationParams:
    """classification 调参 token（PlanAmendment/Blueprint 同形态）。"""

    k: Optional[int] = None
    method: str = ""
    palette: str = ""
    clip_policy: str = ""

    @property
    def present(self) -> bool:
        return any((self.k, self.method, self.palette, self.clip_policy))


def classification_params_of(classification: Any) -> ClassificationParams:
    """dict → params（非法形态 = 全空 = 无参数，不抛 —— 投影层容忍）。"""
    if not isinstance(classification, dict):
        return ClassificationParams()
    k = classification.get("k")
    try:
        k = int(k) if k is not None else None
    except (TypeError, ValueError):
        k = None
    return ClassificationParams(
        k=k,
        method=str(classification.get("method") or ""),
        palette=str(classification.get("palette") or ""),
        clip_policy=str(classification.get("clip_policy") or ""),
    )


def derive_classification_spec(
    values: Sequence[float],
    field_name: str,
    *,
    k: Optional[int] = None,
    method: str = "",
    palette: str = "",
    clip_policy: str = "",
    unit: str = "",
    title: str = "",
) -> Optional[Dict[str, Any]]:
    """值注入的确定性分级派生（single classification；无数据 → None）。

    与 ``thematic_spec.build_graduated_spec`` 同一实现路径（对其参数形态
    的薄封装），保证"工具路径重算"与"IR 路径重算"同源同结果。
    """
    if len(values) < 2:
        return None
    features = [
        {"properties": {field_name: float(v)}} for v in values
    ]
    return build_graduated(
        {"type": "FeatureCollection", "features": features},
        field_name,
        k=k, method=method, palette=palette, clip_policy=clip_policy,
        unit=unit or None, title=title or None,
    )


def build_graduated(
    geojson: Dict[str, Any],
    field_name: str,
    *,
    k: Optional[int],
    method: str,
    palette: str,
    clip_policy: str,
    unit: Optional[str],
    title: Optional[str],
) -> Optional[Dict[str, Any]]:
    from app.lib.cartography.thematic_spec import build_graduated_spec

    return build_graduated_spec(
        geojson, field_name,
        method=method or None,
        k=k,
        palette=palette or None,
        clip_policy=clip_policy or None,
        unit=unit,
        title=title,
    )


@dataclass
class MaterializationRecord:
    """一次物化的 typed 披露（进 receipt / reason_codes；不携带数据）。"""

    layer_id: str
    intent_id: str
    code: str
    field: str = ""
    breaks_count: int = 0
    legend_digest: str = ""
    source_ref: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer_id": self.layer_id, "intent_id": self.intent_id,
            "code": self.code, "field": self.field[:64],
            "breaks_count": self.breaks_count,
            "legend_digest": self.legend_digest[:24],
            "source_ref": self.source_ref[:64],
        }


def _iter_classification_intents(ir: MapPlanIR):
    """blueprint 携带 classification 参数且 legend 缺 breaks 的意图。"""
    for li in ir.layer_intents:
        bp = li.blueprint
        if bp is None:
            continue
        params = classification_params_of(bp.classification)
        if not params.present:
            continue
        legend = bp.legend_spec if isinstance(bp.legend_spec, dict) else {}
        if isinstance(legend.get("breaks"), list) and legend.get("breaks"):
            continue  # 已物化（planner 直接给了完整 legend）
        yield li, bp, params, legend


def _current_layer_of(current: Any, layer_id: str) -> Dict[str, Any]:
    from app.lib.cartography.plan_ir import spec_doc_of

    doc = spec_doc_of(current)
    for layer in (doc.get("layers") or []):
        if isinstance(layer, dict) and str(layer.get("id")) == layer_id:
            return layer
    return {}


def _resolve_field(li: Any, bp: Any, legend: Dict[str, Any],
                   cur_layer: Dict[str, Any]) -> str:
    """字段解析序：amendment 显式 > legend 现状（新层无现状）。"""
    classification = bp.classification if isinstance(bp.classification, dict) else {}
    for candidate in (
        classification.get("field"),
        legend.get("field"),
        (cur_layer.get("legend_spec") or {}).get("field")
        if isinstance(cur_layer.get("legend_spec"), dict) else None,
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


def _resolve_source_ref(li: Any, cur_layer: Dict[str, Any]) -> str:
    source = li.source_ref or str(cur_layer.get("source") or "")
    if isinstance(source, str) and source.startswith("ref:"):
        return source
    # source 是 sources 表 id 的形态：由 loader 侧按别名解析（尽力）。
    return source


def _values_of(geojson: Any, field_name: str) -> List[float]:
    if not isinstance(geojson, dict):
        return []
    out: List[float] = []
    for f in (geojson.get("features") or []):
        if not isinstance(f, dict):
            continue
        props = f.get("properties") or {}
        value = props.get(field_name) if isinstance(props, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(float(value))
    return out


async def materialize_classification(
    ir: MapPlanIR,
    *,
    current: Any,
    load_geojson: GeojsonLoader,
) -> tuple[MapPlanIR, List[MaterializationRecord]]:
    """投影期物化：classification 参数 → 完整 legend_spec（确定性）。

    纯语义变换 + 注入 IO（loader 由调用方提供 —— 生产为 session ref 读取，
    测试为 stub）。失败路径全部 typed 记录并保留原 IR 形态（向后兼容）。
    """
    records: List[MaterializationRecord] = []
    updates: Dict[str, Dict[str, Any]] = {}   # intent_id → legend_spec

    for li, bp, params, legend in _iter_classification_intents(ir):
        cur_layer = _current_layer_of(current, li.layer_id)
        field_name = _resolve_field(li, bp, legend, cur_layer)
        if not field_name:
            records.append(MaterializationRecord(
                layer_id=li.layer_id, intent_id=li.intent_id,
                code=DERIVE_NO_FIELD))
            continue
        source_ref = _resolve_source_ref(li, cur_layer)
        try:
            geojson = await load_geojson(source_ref) if source_ref else None
        except Exception as exc:  # noqa: BLE001 — loader 故障 = 取数失败，不阻断
            logger.warning("[action-derive] load failed for %s: %s",
                           source_ref, exc)
            geojson = None
        if geojson is None:
            records.append(MaterializationRecord(
                layer_id=li.layer_id, intent_id=li.intent_id,
                code=DERIVE_SOURCE_UNRESOLVED, field=field_name,
                source_ref=source_ref))
            continue
        spec = derive_classification_spec(
            _values_of(geojson, field_name), field_name,
            k=params.k, method=params.method, palette=params.palette,
            clip_policy=params.clip_policy,
        )
        if spec is None:
            records.append(MaterializationRecord(
                layer_id=li.layer_id, intent_id=li.intent_id,
                code=DERIVE_NO_DATA, field=field_name,
                source_ref=source_ref))
            continue
        updates[li.intent_id] = spec
        records.append(MaterializationRecord(
            layer_id=li.layer_id, intent_id=li.intent_id, code=DERIVE_OK,
            field=field_name, breaks_count=len(spec.get("breaks") or []),
            legend_digest=_ir_digest(spec)[:24], source_ref=source_ref))

    if not updates:
        return ir, records

    new_intents = []
    for li in ir.layer_intents:
        spec = updates.get(li.intent_id)
        if spec is None or li.blueprint is None:
            new_intents.append(li)
            continue
        new_intents.append(li.model_copy(update={
            "blueprint": li.blueprint.model_copy(update={"legend_spec": spec}),
        }))
    return ir.model_copy(update={"layer_intents": new_intents}), records
