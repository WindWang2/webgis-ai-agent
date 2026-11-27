"""读者面（WP2/WP3）—— share_code → 授权只读投影。

读者永远拿不到冻结 spec 原样：map 端点返回**投影后的 reader spec**
（数据源重写为读者端点、被阻断层剔除、编辑面字段剥离）；features/summary
端点在物化产物上按快照白名单二次过滤（纵深防御 —— 与写时投影同一实现）。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from app.services.publication import store as pub_store
from app.services.publication.access import reader_share_denied_reason
from app.services.publication.contract import (
    DataMode,
    PublicationSnapshotDocument,
)
from app.services.publication.projection import project_properties

#: 读者 map spec 中剥离的编辑面键（authoring 语义不进读者面）。
_READER_SPEC_STRIP_KEYS = ("scenario_mode",)


async def resolve_reader_publication(db, share_code: str):
    """share_code → (publication, denial_reason)。

    denial_reason None = 放行；否则读者端点统一 404（不泄露存在性/原因差）。
    """
    from app.services.publication.store import get_publication_by_share_code

    publication = await get_publication_by_share_code(db, share_code)
    reason = reader_share_denied_reason(publication)
    if reason is not None:
        return None, reason
    # 常量时间复核（唯一索引定位 + compare_digest —— 纵深防御）。
    from app.services.publication.access import share_code_matches

    if not share_code_matches(publication, share_code):
        return None, "not_found"
    return publication, None


def snapshot_document_of(publication) -> Optional[PublicationSnapshotDocument]:
    snapshot = getattr(publication, "snapshot", None)
    if not isinstance(snapshot, dict):
        return None
    try:
        return PublicationSnapshotDocument(**snapshot)
    except Exception:  # noqa: BLE001 — 快照损坏按不可用处理（诚实 503）
        return None


def build_reader_map_spec(
    snapshot: PublicationSnapshotDocument, *, share_code: str
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """冻结 spec → 读者 map spec（源按层重写 + 阻断层剔除 + 编辑面剥离）。

    R1 P2-4：源重写按**层**展开 —— MapSpec 允许多层共享一个 source 且各层
    字段白名单不同；共享源若指向同一产物 URL 会让兄弟层渲染错误的字段集。
    每个可投影层获得独立 source 条目（publication:{layer_id} → 自己的
    白名单产物），layer.source 随之改指。

    返回 (reader_spec, degradations)：degradation 记录读者可见的降级
    （被剔除的层/源），诚实呈现 —— 不静默。
    """
    spec = copy.deepcopy(snapshot.spec)
    degradations: List[Dict[str, Any]] = []
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}

    materialized_layers = [
        lyr for lyr in snapshot.layers if lyr.data_mode == DataMode.materialized
    ]
    materialized_layer_ids = {lyr.layer_id for lyr in materialized_layers}
    source_meta = {
        lyr.source_id: sources.get(lyr.source_id)
        for lyr in materialized_layers
        if isinstance(sources.get(lyr.source_id), dict)
    }

    # 源重写：可投影层 → 每层独立 reader source（自己的白名单产物 URL）；
    # 其余源（不可投影/底图瓦片类）剔除并降级披露。
    next_sources: Dict[str, Any] = {}
    for lyr in materialized_layers:
        entry = source_meta.get(lyr.source_id) or {}
        next_sources[f"publication:{lyr.layer_id}"] = {
            "type": "geojson",
            "name": entry.get("name") or lyr.source_id,
            "attribution": entry.get("attribution") or "",
            "url": f"/api/v1/publications/shared/{share_code}/layers/{lyr.layer_id}/data",
        }
    for source_key, entry in sources.items():
        if source_key not in {lyr.source_id for lyr in materialized_layers}:
            degradations.append({
                "kind": "source_dropped",
                "source_id": str(source_key),
                "message": "该数据源不在发布投影范围内（需会话凭证或无法验证许可），已从读者地图剔除",
            })
    spec["sources"] = next_sources

    # 层剔除：非 materialized 层不进读者面（阻断已在 snapshot.blockers 披露）。
    layers = spec.get("layers")
    if isinstance(layers, list):
        kept = []
        for lyr in layers:
            if not isinstance(lyr, dict):
                continue
            layer_id = lyr.get("id")
            if layer_id in materialized_layer_ids:
                # 指向该层独立的 reader source（R1 P2-4：共享源不再错配）。
                lyr["source"] = f"publication:{layer_id}"
                kept.append(lyr)
            else:
                degradations.append({
                    "kind": "layer_dropped",
                    "layer_id": str(layer_id),
                    "message": "该层无法安全投影发布，未包含在读者地图中",
                })
        spec["layers"] = kept

    for key in _READER_SPEC_STRIP_KEYS:
        spec.pop(key, None)
    return spec, degradations


def reader_doc(snapshot: PublicationSnapshotDocument) -> Dict[str, Any]:
    """读者元数据文档（无 spec；等价信息投影 —— WP4）。

    只含授权投影字段：meta/scope/layers（白名单+计数）/blockers/
    accessibility/qa。snapshot_fingerprint 用于读者校验内容版本。
    """
    return {
        "spec_version": snapshot.spec_version,
        "product_id": snapshot.product_id,
        "version": snapshot.version,
        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
        "meta": snapshot.meta.model_dump(mode="json"),
        "scope": snapshot.scope.model_dump(mode="json"),
        "layers": [
            {
                "layer_id": lyr.layer_id,
                "title": lyr.title,
                "field_whitelist": list(lyr.field_whitelist),
                "feature_count": lyr.feature_count,
                "reader_visibility_allowed": lyr.reader_visibility_allowed,
            }
            # R1 P3-14：只输出 materialized 层 —— 未物化层（阻断）已在
            # blockers 披露；数据表/统计面只对可投影层有意义。
            for lyr in snapshot.layers
            if lyr.data_mode == DataMode.materialized
        ],
        "blockers": [b.model_dump(mode="json") for b in snapshot.blockers],
        "accessibility": snapshot.accessibility,
        "qa": snapshot.qa.model_dump(mode="json"),
    }


def load_layer_fc(publication, layer_id: str) -> Optional[Dict[str, Any]]:
    """物化产物读取（store 层；损坏 → None → 端点 503）。"""
    return pub_store.load_layer_artifact(publication.id, layer_id)


def filter_layer_fields(
    fc: Dict[str, Any], *, field_whitelist: List[str], fields: Optional[List[str]]
) -> Dict[str, Any]:
    """读时二次过滤：客户端 fields ∩ 白名单（缺省 = 白名单）。

    单一实现（projection.project_properties）—— 与写时投影同语义。
    """
    effective = list(field_whitelist)
    if fields:
        wanted = [f for f in fields if f in set(field_whitelist)]
        effective = wanted or list(field_whitelist)
    features = fc.get("features")
    if not isinstance(features, list):
        return {"type": "FeatureCollection", "features": []}
    out = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        out.append({
            "type": "Feature",
            "id": feature.get("id"),
            "properties": project_properties(
                feature.get("properties"), field_whitelist=effective
            ),
            "geometry": feature.get("geometry"),
        })
    return {"type": "FeatureCollection", "features": out}


__all__ = [
    "resolve_reader_publication",
    "snapshot_document_of",
    "build_reader_map_spec",
    "reader_doc",
    "load_layer_fc",
    "filter_layer_fields",
]
