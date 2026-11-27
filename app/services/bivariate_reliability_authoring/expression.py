"""G41 候选表达：冻结展示副本 + 单一 paint 投影 + canonical 图例 + 证据块。

* 派生类/桶只写进**冻结展示副本**（新会话 ref），绝不原地覆盖源数据；
  源已有 ``__biv_class`` 只记录血缘、绝不覆盖（B14/B17）。
* ``paint.color`` 一律经 ``thematic_spec.spec_to_paint``（唯一投影构造点）；
  第二通道用既有支持的 ``match`` StyleMethod（live compiler 与服务端 SVG
  ``_resolve_paint_value`` 都已支持），不新增第二渲染器。
* 新 source + layer 走**同一笔** ``UpsertLayerIntent``，失败无半引用。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.lib.cartography.thematic_spec import spec_to_paint
from app.services.bivariate_reliability_authoring.classify import (
    PAIR_CLASS_FIELD,
    BivariateResult,
)
from app.services.bivariate_reliability_authoring.contract import (
    MAX_DERIVED_BYTES,
)
from app.services.bivariate_reliability_authoring.errors import AuthoringError
from app.services.bivariate_reliability_authoring.legend import build_bivariate_legend
from app.services.bivariate_reliability_authoring.reliability import (
    RELIABILITY_BUCKET_FIELD,
    ReliabilityResult,
)
from app.services.bivariate_reliability_authoring.snapshot import FrozenSource
from app.services.bivariate_reliability_authoring.stats import PairedScan

#: 层上的本域证据块键（兄弟键；upsert 整层替换时由本服务维护）。
AUTHORING_KEY = "bivariate_reliability_authoring"


def expression_fingerprint(legend_spec: Any) -> Optional[str]:
    if not isinstance(legend_spec, dict):
        return None
    raw = json.dumps(legend_spec, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False, default=str)
    return "g41e-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class CandidateExpression:
    """preview / commit 共用的确定性候选（零 mutation 即可完整构建）。"""

    legend_spec: Dict[str, Any]
    paint: Dict[str, Any]                 # 本域写入的规范 paint 键
    paint_keys: List[str]
    derived_features: List[Dict[str, Any]]
    derived_fields: List[str]
    stats_basis: Dict[str, Any]
    disclosures: List[str]
    source_has_biv_class: bool
    class_field: Optional[str] = None
    bucket_field: Optional[str] = None


def _check_derived_collision(features: List[Dict[str, Any]], names: List[str]) -> bool:
    """用户已有同名派生字段 → 拒绝；源已有 __biv_class → 只记录、不覆盖。"""
    for feat in features:
        props = feat.get("properties")
        if not isinstance(props, dict):
            continue
        for name in names:
            if name in props:
                raise AuthoringError(
                    "DERIVED_FIELD_COLLISION",
                    f"源已存在字段 {name!r} —— 拒绝覆盖用户字段；"
                    "派生表达必须有独立身份",
                    field=name,
                    correction_hint="换用其它派生字段名或先清理该源字段。")
        _ = props.get("__biv_class")
    return any(
        isinstance(f.get("properties"), dict)
        and "__biv_class" in f["properties"]
        for f in features
    )


def _derive_features(
    frozen: FrozenSource,
    values_by_feature: List[Any],
    field_name: str,
    names: List[str],
) -> List[Dict[str, Any]]:
    _check_derived_collision(frozen.features, names)
    out: List[Dict[str, Any]] = []
    for i, feat in enumerate(frozen.features):
        f = copy.deepcopy(feat)
        props = f.get("properties")
        if not isinstance(props, dict):
            props = {}
            f["properties"] = props
        # 稳定身份（_wfid 等）由深拷贝原样保留；只新增本域派生字段。
        value = values_by_feature[i] if i < len(values_by_feature) else None
        props[field_name] = value
        out.append(f)
    # 有界字节帽（超限明确拒绝，不静默截断）。
    size = len(json.dumps(out, ensure_ascii=False, default=str).encode("utf-8"))
    if size > MAX_DERIVED_BYTES:
        raise AuthoringError(
            "DERIVED_PAYLOAD_TOO_LARGE",
            f"冻结展示副本 {size} 字节超过硬帽 {MAX_DERIVED_BYTES}",
            status_code=413)
    return out


def _derived_fc(frozen: FrozenSource, features: List[Dict[str, Any]],
                identity: Dict[str, Any]) -> Dict[str, Any]:
    fc: Dict[str, Any] = {"type": "FeatureCollection", "features": features}
    fc["metadata"] = {
        "kind": "g41_frozen_display_copy",
        "base_source_ref": frozen.ref_id,
        "base_content_revision": frozen.content_revision,
        "base_content_fingerprint": frozen.content_fingerprint,
        "derived_from": identity,
        "disclosure": "只读派生展示副本；源数据未被写回，撤销即回到基准源表达",
    }
    return fc


def build_bivariate_candidate(
    spec: Any,
    scan: PairedScan,
    result: BivariateResult,
    frozen: FrozenSource,
    *,
    anchor: Dict[str, Any],
    plan_digest: str,
    layer_type: str,
    title: Optional[str],
) -> CandidateExpression:
    if layer_type not in ("fill", "circle", "line"):
        raise AuthoringError(
            "UNSUPPORTED_LAYER_TYPE",
            f"层型 {layer_type!r} 不支持本域双变量矩阵表达", status_code=409)
    legend = build_bivariate_legend(
        spec, scan, result, anchor=anchor, plan_digest=plan_digest, title=title)
    paint_color, warnings = spec_to_paint(legend)
    if paint_color is None:
        raise AuthoringError(
            "PAINT_PROJECTION_FAILED",
            f"双变量 paint 投影失败：{warnings}", status_code=500)
    features = _derive_features(
        frozen, result.class_by_feature, PAIR_CLASS_FIELD, [PAIR_CLASS_FIELD])
    has_biv = any(
        isinstance(f.get("properties"), dict) and "__biv_class" in f["properties"]
        for f in frozen.features)
    return CandidateExpression(
        legend_spec=legend,
        paint={"color": paint_color},
        paint_keys=["color"],
        derived_features=features,
        derived_fields=[PAIR_CLASS_FIELD],
        stats_basis=scan.basis(),
        disclosures=list(legend["disclosures"]),
        source_has_biv_class=has_biv,
        class_field=PAIR_CLASS_FIELD,
    )


def build_reliability_candidate(
    spec: Any,
    result: ReliabilityResult,
    frozen: FrozenSource,
    *,
    anchor: Dict[str, Any],
    plan_digest: str,
    layer_type: str,
    title: Optional[str],
) -> CandidateExpression:
    from app.services.bivariate_reliability_authoring.legend import (
        build_reliability_legend,
    )

    v = spec.visual
    if v.channel == "stroke_width" and layer_type != "circle":
        raise AuthoringError(
            "VISUAL_CHANNEL_LAYER_TYPE_MISMATCH",
            f"stroke_width 第二通道只适用于 circle 层，当前 {layer_type!r}；"
            "拒绝而不是静默删除第二通道",
            field="visual.channel",
            correction_hint="改用 opacity 通道，或把该层改为点符号层。")
    if layer_type not in ("fill", "circle", "line"):
        raise AuthoringError(
            "UNSUPPORTED_LAYER_TYPE",
            f"层型 {layer_type!r} 不支持本域双通道表达", status_code=409)

    legend = build_reliability_legend(
        spec, result, anchor=anchor, plan_digest=plan_digest, title=title)
    paint_color, warnings = spec_to_paint(result.estimate_legend)
    if paint_color is None:
        raise AuthoringError(
            "PAINT_PROJECTION_FAILED",
            f"估计主色投影失败：{warnings}", status_code=500)
    key = "opacity" if v.channel == "opacity" else "strokeWidth"
    paint = {"color": paint_color, key: dict(result.bucket_paint)}
    features = _derive_features(
        frozen, result.bucket_by_feature, RELIABILITY_BUCKET_FIELD,
        [RELIABILITY_BUCKET_FIELD])
    has_biv = any(
        isinstance(f.get("properties"), dict) and "__biv_class" in f["properties"]
        for f in frozen.features)
    return CandidateExpression(
        legend_spec=legend,
        paint=paint,
        paint_keys=["color", key],
        derived_features=features,
        derived_fields=[RELIABILITY_BUCKET_FIELD],
        stats_basis=result.scan.basis(),
        disclosures=list(legend["disclosures"]),
        source_has_biv_class=has_biv,
        bucket_field=RELIABILITY_BUCKET_FIELD,
    )


def derived_identity(frozen: FrozenSource, candidate: CandidateExpression,
                     plan_digest: str) -> Dict[str, Any]:
    return {
        "base_source_ref": frozen.ref_id,
        "base_content_revision": frozen.content_revision,
        "base_content_fingerprint": frozen.content_fingerprint,
        "algorithm_version": frozen.algorithm_version,
        "plan_digest": plan_digest,
        "derived_fields": list(candidate.derived_fields),
        "legend_fingerprint": expression_fingerprint(candidate.legend_spec),
    }


def make_derived_fc(frozen: FrozenSource, candidate: CandidateExpression,
                    plan_digest: str) -> Dict[str, Any]:
    return _derived_fc(
        frozen, candidate.derived_features,
        derived_identity(frozen, candidate, plan_digest))
