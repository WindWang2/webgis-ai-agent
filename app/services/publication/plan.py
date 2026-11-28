"""发布计划（WP1/WP2/WP5 共用）——spec 可发布性检查与层投影计划。

`plan_publication` 是唯一权威：preview REST、draft 创建、Pi 工具
（inspect/prepare）都消费它。产出：
- 每层 projectability（materializable / typed blocker）
- 字段候选与建议白名单（真实数据采样，有界）
- 可访问性清单预览（WP4 接线点）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel

from app.services.publication.contract import (
    DataMode,
    PublicationBlocker,
    PublicationBlockerReason,
    PublicationScope,
)
from app.services.publication.projection import (
    DEFAULT_GEOMETRY_PRECISION,
    PUBLICATION_LAYER_FEATURE_LIMIT,
)

logger = logging.getLogger(__name__)

#: 字段候选采样上界（要素级；大层不逐个枚举属性键）。
_FIELD_SAMPLE_LIMIT = 500
#: 单层字段候选上限（超出 → 建议白名单截断 + 诚实披露）。
_FIELD_CANDIDATE_LIMIT = 64


def _spec_layers(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    layers = spec.get("layers")
    if not isinstance(layers, list):
        return []
    return [lyr for lyr in layers if isinstance(lyr, dict)]


def _layer_title(layer: Dict[str, Any]) -> str:
    legend = layer.get("legend") or {}
    if isinstance(legend, dict) and legend.get("title"):
        return str(legend["title"])[:200]
    return str(layer.get("id", ""))[:200]


def classify_source(source_entry: Any) -> Tuple[str, Optional[PublicationBlocker]]:
    """source entry → (data_kind, blocker)。

    data_kind: ``geojson_ref``（可物化）/ ``geojson_inline``（可物化）/
    ``unsupported``（typed 阻断，理由如实）。
    """
    if not isinstance(source_entry, dict):
        return "unsupported", PublicationBlocker(
            reason=PublicationBlockerReason.SOURCE_NOT_AUTHORIZABLE,
            message="source 条目缺失或形状非法",
        )
    stype = source_entry.get("type")
    ref = source_entry.get("ref") or source_entry.get("ref_id")
    if stype == "geojson":
        if isinstance(source_entry.get("inlineData"), dict):
            return "geojson_inline", None
        if isinstance(ref, str) and ref.startswith("ref:"):
            return "geojson_ref", None
        url = source_entry.get("url")
        if isinstance(url, str) and url.startswith("ref:"):
            return "geojson_ref", None
        if isinstance(url, str) and url and "://" in url:
            return "unsupported", PublicationBlocker(
                reason=PublicationBlockerReason.SOURCE_NOT_AUTHORIZABLE,
                message="外部 URL 源无法验证许可与凭证，不纳入发布投影",
            )
        return "unsupported", PublicationBlocker(
            reason=PublicationBlockerReason.SOURCE_NOT_AUTHORIZABLE,
            message="geojson 源无可解析数据载体（ref/inline）",
        )
    # raster / vector(MVT) / data_fabric / wms/wmts/pmtiles / numeric_raster：
    # v1 发布投影只覆盖矢量 FeatureCollection；其余 typed 阻断（诚实说明）。
    return "unsupported", PublicationBlocker(
        reason=PublicationBlockerReason.SOURCE_NOT_AUTHORIZABLE,
        message=f"source 类型 {stype!r} 暂不在发布投影范围（仅矢量 FeatureCollection）",
    )


def candidate_fields_from_fc(fc: Any) -> List[str]:
    """有界采样 properties 键（稳定序：首见顺序，去重截断）。"""
    candidates: List[str] = []
    seen = set()
    if isinstance(fc, dict) and isinstance(fc.get("features"), list):
        for feature in fc["features"][:_FIELD_SAMPLE_LIMIT]:
            props = feature.get("properties") if isinstance(feature, dict) else None
            if not isinstance(props, dict):
                continue
            for key in props:
                key_s = str(key)
                if key_s and key_s not in seen:
                    seen.add(key_s)
                    candidates.append(key_s)
                    if len(candidates) >= _FIELD_CANDIDATE_LIMIT:
                        return candidates
    return candidates


class LayerPlan(BaseModel):
    """单层计划（preview/draft/prepare 共用形状）。"""

    layer_id: str
    title: str
    source_id: str
    source_kind: str
    projectable: bool
    feature_count: int = 0
    candidate_fields: List[str] = []
    field_whitelist: List[str] = []
    data_mode: DataMode = DataMode.materialized
    blocker: Optional[PublicationBlocker] = None
    #: 层在源 spec 中默认可见（visible 不为 False）。
    visible_by_default: bool = True


class PublicationPlan(BaseModel):
    """一次发布计划（preview/prepare 返回体核心）。"""

    spec_fingerprint: str
    spec_revision: Optional[int] = None
    layers: List[LayerPlan]
    blockers: List[PublicationBlocker]
    scope: PublicationScope
    title: str
    #: 源 MapSpec 是否通过权威 schema 校验（preview 阶段真跑）。
    spec_valid: bool

    @property
    def publishable(self) -> bool:
        return (
            self.spec_valid
            and any(lyr.projectable for lyr in self.layers)
            and not any(b.reason == PublicationBlockerReason.MAPSPEC_INVALID for b in self.blockers)
        )


def canonical_fingerprint(spec: Dict[str, Any]) -> str:
    """源 spec 指纹（canonical-JSON sha256 —— 与 mapspec store sidecar 同算法）。"""
    import hashlib
    import json

    payload = json.dumps(
        spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_spec_schema(spec: Dict[str, Any]) -> Tuple[bool, str]:
    """权威 schema 校验（MapSpecDocument）；返回 (ok, message)。"""
    try:
        from app.lib.cartography.mapspec_schema import MapSpecDocument

        MapSpecDocument.model_validate(spec)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — pydantic 校验面 → typed 拒绝
        return False, str(exc)[:300]


def derive_scope(spec: Dict[str, Any], precision: int = DEFAULT_GEOMETRY_PRECISION) -> PublicationScope:
    """从 spec 提取范围语义（CRS/时间；bbox 由物化数据统计，不在本函数）。"""
    view = spec.get("view") if isinstance(spec.get("view"), dict) else {}
    crs = str(view.get("projection") or view.get("crs") or "EPSG:3857")[:80]
    time_cfg = spec.get("time") if isinstance(spec.get("time"), dict) else {}
    start = time_cfg.get("start") or time_cfg.get("from")
    end = time_cfg.get("end") or time_cfg.get("to")
    return PublicationScope(
        crs=crs,
        bbox=None,
        time_start=str(start)[:64] if start else None,
        time_end=str(end)[:64] if end else None,
        geometry_precision=precision,
    )


def plan_layers(
    spec: Dict[str, Any],
    *,
    hydrated: Optional[Dict[str, Dict[str, Any]]] = None,
    selected_layer_ids: Optional[Sequence[str]] = None,
    field_whitelists: Optional[Dict[str, Sequence[str]]] = None,
) -> Tuple[List[LayerPlan], List[PublicationBlocker]]:
    """逐层计划。``hydrated``: source_key → FC（调用方已水合的会话数据）。

    ``selected_layer_ids``：作者选择发布的层（缺省 = 全部可投影层）；
    ``field_whitelists``：作者逐层白名单（缺省 = 候选字段建议）。
    """
    hydrated = hydrated or {}
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
    plans: List[LayerPlan] = []
    blockers: List[PublicationBlocker] = []
    for layer in _spec_layers(spec):
        layer_id = str(layer.get("id", ""))
        if not layer_id:
            continue
        source_id = str(layer.get("source", ""))
        source_entry = sources.get(source_id)
        kind, src_blocker = classify_source(source_entry)
        visible_default = layer.get("visible") is not False
        fc = hydrated.get(source_id)
        if kind == "unsupported":
            blocker = PublicationBlocker(
                reason=src_blocker.reason if src_blocker else PublicationBlockerReason.SOURCE_NOT_AUTHORIZABLE,
                message=src_blocker.message if src_blocker else "source 不可授权",
                layer_id=layer_id,
                source_id=source_id,
            )
            if selected_layer_ids is None or layer_id in selected_layer_ids:
                blockers.append(blocker)
            plans.append(LayerPlan(
                layer_id=layer_id, title=_layer_title(layer), source_id=source_id,
                source_kind=kind, projectable=False, blocker=blocker,
                visible_by_default=visible_default,
            ))
            continue
        if fc is None:
            plans.append(LayerPlan(
                layer_id=layer_id, title=_layer_title(layer), source_id=source_id,
                source_kind=kind, projectable=False,
                blocker=PublicationBlocker(
                    reason=PublicationBlockerReason.SOURCE_DATA_UNAVAILABLE,
                    message="源数据未水合（会话数据不可用或未提供）",
                    layer_id=layer_id, source_id=source_id,
                ),
                visible_by_default=visible_default,
            ))
            continue
        features = fc.get("features") if isinstance(fc, dict) else None
        feature_count = len(features) if isinstance(features, list) else 0
        candidates = candidate_fields_from_fc(fc)
        if feature_count > PUBLICATION_LAYER_FEATURE_LIMIT:
            plans.append(LayerPlan(
                layer_id=layer_id, title=_layer_title(layer), source_id=source_id,
                source_kind=kind, projectable=False, feature_count=feature_count,
                candidate_fields=candidates,
                blocker=PublicationBlocker(
                    reason=PublicationBlockerReason.SNAPSHOT_BUDGET_EXCEEDED,
                    message=f"要素数 {feature_count} 超过单层发布上限",
                    layer_id=layer_id, source_id=source_id,
                ),
                visible_by_default=visible_default,
            ))
            continue
        whitelist = list(field_whitelists.get(layer_id, candidates)) if field_whitelists else list(candidates)
        blocker = None
        if selected_layer_ids is not None and layer_id in selected_layer_ids and not whitelist:
            blocker = PublicationBlocker(
                reason=PublicationBlockerReason.FIELD_WHITELIST_EMPTY,
                message="所选层字段白名单为空（无属性可公开）",
                layer_id=layer_id, source_id=source_id,
            )
        plans.append(LayerPlan(
            layer_id=layer_id, title=_layer_title(layer), source_id=source_id,
            source_kind=kind, projectable=True, feature_count=feature_count,
            candidate_fields=candidates, field_whitelist=whitelist,
            visible_by_default=visible_default, blocker=blocker,
        ))
    return plans, blockers


__all__ = [
    "LayerPlan",
    "PublicationPlan",
    "classify_source",
    "candidate_fields_from_fc",
    "canonical_fingerprint",
    "validate_spec_schema",
    "derive_scope",
    "plan_layers",
]
