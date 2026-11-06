"""Label Field Guard —— 显式标注字段缺失守卫（W2.2，ADR-0283 D3）。

**调研结论（2026-11，只读核实）**：``semantic_checks._check_label_collision``
在标注字段不在 profile fields 时**静默跳过**（``_estimate_label_chars`` 返回
``None`` → ``return``，semantic_checks.py:1366-1368 —— 估计检查不可评而非
缺陷披露）；``_check_layer_paint_fields`` 只覆盖 paint 字段（PAINT_FIELD_
EXISTS）；``label_plan.choose_label_field`` 的 ``no_label_field`` 是**规划期
选字段**的理由而非对已声明字段的守卫。即：显式标注字段不在源字段集的
确定性检查此前不存在 —— 本模块补位（渲染侧守卫）。

语义：仅对**显式声明**的字段生效 ——
- symbol/text 层 ``layout["text-field"]`` / ``paint["text-field"]`` 的
  ``"{field}"`` 花括号 pattern（缺省 ``{name}`` 非显式，不触发；非花括号
  pattern 按编译器口径为字面文本，不是字段引用）；
- 任意层 ``label.field`` 策略声明（label_plan / label_layer 组件产物，
  编译为 symbol 子层 —— 与 ``_check_label_collision`` 的 ac-05 域一致）。

字段集由调用方提供（``source_fields_provider: source_id -> 字段集或
None``）；``None`` = 不可评估（瓦片/ref 源）→ 诚实跳过，不误报。

纯函数、确定性；守卫披露缺失，不替代编译器的文本求值。显式字段缺失时
编译器不以另一个字段伪造标签。
"""
from __future__ import annotations

from typing import Any, Callable, List, Mapping, Optional, Set, TypedDict

#: closed vocab 诊断码（render_diagnostics 权威词表注册，warning）。
LABEL_FIELD_MISSING = "label_field_missing"

#: 源字段集证据摘要上限（evidence 有界）。
_MAX_FIELDS_IN_EVIDENCE = 12

SourceFieldsProvider = Callable[[str], Optional[Set[str]]]


class LabelFieldEvidence(TypedDict):
    field: str
    source_field_count: int
    source_fields_sample: List[str]


class LabelFieldFinding(TypedDict):
    code: str
    severity: str
    layer_id: object
    field: str
    source_id: str
    evidence: LabelFieldEvidence


def _explicit_label_fields(layer: Mapping[str, object]) -> List[str]:
    """层的显式标注字段引用（text-field 花括号 pattern + label.field）。"""
    candidates: List[str] = []
    if layer.get("type") in ("symbol", "text"):
        for cfg_key in ("layout", "paint"):
            cfg = layer.get(cfg_key)
            if not isinstance(cfg, dict):
                continue
            tf = cfg.get("text-field")
            if isinstance(tf, str):
                pattern = tf.strip()
                if pattern.startswith("{") and pattern.endswith("}") and len(pattern) >= 2:
                    fname = pattern[1:-1].strip()
                    if fname:
                        candidates.append(fname)
    label_cfg = layer.get("label")
    if isinstance(label_cfg, dict):
        lf = label_cfg.get("field")
        if isinstance(lf, str) and lf.strip():
            candidates.append(lf.strip())
    # 保序去重（text-field 与 label.field 同名只报一次）
    seen: Set[str] = set()
    result: List[str] = []
    for name in candidates:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def check_label_fields(
    spec_like: Any,
    source_fields_provider: SourceFieldsProvider,
) -> List[LabelFieldFinding]:
    """检查显式标注字段是否在源字段集内，返回 finding dict 列表。

    每条 finding：``{code, severity, layer_id, field, source_id, evidence}``，
    ``evidence`` 携带源字段数与有界样本（sorted 前
    ``_MAX_FIELDS_IN_EVIDENCE`` 个）。非 dict spec / 无 layers / 无 source
    引用 / provider 不可评估（None）→ 不产出（诚实跳过）。
    """
    findings: List[LabelFieldFinding] = []
    if not isinstance(spec_like, dict):
        return findings
    layers = spec_like.get("layers")
    if not isinstance(layers, list):
        return findings
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        explicit = _explicit_label_fields(layer)
        if not explicit:
            continue
        source_id = layer.get("source")
        if not isinstance(source_id, str) or not source_id:
            continue
        fields = source_fields_provider(source_id)
        if fields is None:
            continue
        field_set = {str(f) for f in fields}
        for fname in explicit:
            if fname not in field_set:
                findings.append({
                    "code": LABEL_FIELD_MISSING,
                    "severity": "warning",
                    "layer_id": layer.get("id"),
                    "field": fname,
                    "source_id": source_id,
                    "evidence": {
                        "field": fname,
                        "source_field_count": len(field_set),
                        "source_fields_sample": sorted(field_set)[:_MAX_FIELDS_IN_EVIDENCE],
                    },
                })
    return findings


def inline_source_field_set(
    geojson_data: Any, *, max_features: int = 1000
) -> Optional[Set[str]]:
    """内联 GeoJSON 源的字段集（要素属性键并集；有界确定性采样）。

    非 dict 数据（ref/瓦片源）→ ``None``（不可评估）。仅扫前
    ``max_features`` 个要素（资源包络；截断取前 N 与编译器 features 截断
    同向，确定性）。
    """
    if not isinstance(geojson_data, dict):
        return None
    features = (
        geojson_data.get("features", [geojson_data])
        if geojson_data.get("type") == "FeatureCollection"
        else [geojson_data]
    )
    if not isinstance(features, list):
        features = [geojson_data]
    # A sampled union cannot prove absence in the rest of the source. The
    # negative-only guard must be unevaluated when its scan is incomplete.
    if len(features) > max_features:
        return None
    fields: Set[str] = set()
    for feat in features[:max_features]:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties")
        if isinstance(props, dict):
            fields.update(str(k) for k in props.keys())
    return fields
