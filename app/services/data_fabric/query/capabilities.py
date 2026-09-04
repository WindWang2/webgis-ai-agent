"""Capability Matrix V2：按源类型的 truthful 默认声明（ADR-0094 §4）。

声明即契约 —— AdapterContractTest 验证每一项；adapter 可通过
``capabilities_v2()`` 覆盖默认（例如探测到 CQL2 conformance 后升级
filter_pushdown），但绝不允许声明未实现的能力（V1 的教训：PostGIS
projection / WFS pagination / GeoParquet lazy_batching / S3 range_request）。
"""
from __future__ import annotations

from typing import Dict, Optional

from app.services.data_fabric.query.models import AdapterCapabilitiesV2


def _postgis_defaults() -> AdapterCapabilitiesV2:
    return AdapterCapabilitiesV2(
        source_type="postgis",
        bbox_pushdown=True,
        filter_pushdown=True,
        projection_pushdown=True,
        sort_pushdown=True,
        offset_pagination=True,
        cursor_pagination=True,
        spatial_predicates=["bbox", "intersects", "within", "contains", "touches", "overlaps", "dwithin"],
        temporal_filter=True,
        aggregation=True,
        group_by=True,
        count=True,
        statistics=True,
        server_reprojection=True,
        vector_tiles=True,          # server-side ST_AsMVT 路径（Wave I）
        range_requests=False,
        streaming=False,            # fetchall + LIMIT 有界读（非游标流式）
        max_page_size=10_000,
        server_side_spatial_join=True,
    )


def _ogc_api_defaults() -> AdapterCapabilitiesV2:
    return AdapterCapabilitiesV2(
        source_type="ogc_api",
        bbox_pushdown=True,
        filter_pushdown=False,       # 仅当 conformance 声明 CQL2 时动态升级
        projection_pushdown=False,
        sort_pushdown=False,
        offset_pagination=False,     # 核心 spec 无 offset；pagination 走 links.next
        cursor_pagination=True,      # links.next 遍历
        spatial_predicates=["bbox"],
        temporal_filter=True,
        aggregation=False,
        group_by=False,
        count=True,                  # numberMatched
        statistics=False,
        server_reprojection=False,   # crs 协商受服务端支持限制，默认不声明
        vector_tiles=False,
        range_requests=False,
        streaming=False,
        max_page_size=10_000,
    )


def _wfs_defaults() -> AdapterCapabilitiesV2:
    return AdapterCapabilitiesV2(
        source_type="wfs",
        bbox_pushdown=True,
        filter_pushdown=True,        # FES XML 编译（GET KVP bbox + POST filter）
        projection_pushdown=True,    # propertyName
        sort_pushdown=False,         # SORTBY 支持零散，默认不声明
        offset_pagination=True,      # startIndex（GetCapabilities 声明时）
        cursor_pagination=False,
        spatial_predicates=["bbox"],
        temporal_filter=False,
        aggregation=False,
        group_by=False,
        count=False,
        statistics=False,
        server_reprojection=True,    # srsName 协商
        vector_tiles=False,
        range_requests=False,
        streaming=False,
        max_page_size=10_000,
    )


def _arcgis_defaults() -> AdapterCapabilitiesV2:
    return AdapterCapabilitiesV2(
        source_type="arcgis",
        bbox_pushdown=True,
        filter_pushdown=True,        # where 由 AST 编译（不再 raw 透传）
        projection_pushdown=True,    # outFields
        sort_pushdown=True,          # orderByFields
        offset_pagination=True,      # resultOffset（服务声明 supportsPagination）
        cursor_pagination=False,
        spatial_predicates=["bbox", "intersects", "within", "contains"],
        temporal_filter=True,        # 时间字段 where 片段
        aggregation=False,           # 仅 count-only（R1-M9：裸 bool 无法表达
                                     # count-only；sum/avg 等 → 本地回退/typed）
        group_by=False,              # groupByFieldsForStatistics 支持零散
        count=True,                  # returnCountOnly=true
        statistics=False,
        server_reprojection=True,    # outSR
        vector_tiles=False,
        range_requests=False,
        streaming=False,
        max_page_size=2_000,         # 保守值；实际 maxRecordCount 探测后修正
    )


def _geoparquet_defaults() -> AdapterCapabilitiesV2:
    return AdapterCapabilitiesV2(
        source_type="geoparquet",
        bbox_pushdown=True,          # row-group covering 剪枝 + 读后精确过滤
        filter_pushdown=False,       # 本地逐行求值（诚实声明；pyarrow filters= 未实现）
        projection_pushdown=True,
        sort_pushdown=False,
        offset_pagination=True,
        cursor_pagination=False,
        spatial_predicates=["bbox"],
        temporal_filter=False,       # 本地求值，非下推（诚实声明）
        aggregation=False,           # 聚合在本地 batch 流上执行（bounded）
        group_by=False,
        count=True,                  # metadata num_rows（footer-only）
        statistics=False,            # row-group 剪枝内部使用，不作为查询能力暴露
        server_reprojection=False,
        vector_tiles=False,
        range_requests=True,         # 远程 parquet 经 fsspec range read
        streaming=True,              # iter_batches
        max_page_size=5_000,
    )


def _flatgeobuf_defaults() -> AdapterCapabilitiesV2:
    return AdapterCapabilitiesV2(
        source_type="flatgeobuf",
        bbox_pushdown=True,          # FGB packed RTree（经 GDAL/pyogrio bbox）
        filter_pushdown=False,
        projection_pushdown=True,
        sort_pushdown=False,
        offset_pagination=True,
        cursor_pagination=False,
        spatial_predicates=["bbox"],
        temporal_filter=False,
        aggregation=False,
        group_by=False,
        count=True,                  # pyogrio read_info features
        statistics=False,
        server_reprojection=False,
        vector_tiles=False,
        range_requests=True,         # FGB 远程范围读取（HTTP Range）
        streaming=False,
        max_page_size=5_000,
    )


def _pmtiles_defaults() -> AdapterCapabilitiesV2:
    return AdapterCapabilitiesV2(
        source_type="pmtiles",
        bbox_pushdown=False,
        filter_pushdown=False,
        projection_pushdown=False,
        sort_pushdown=False,
        offset_pagination=False,
        cursor_pagination=False,
        spatial_predicates=[],
        temporal_filter=False,
        aggregation=False,
        group_by=False,
        count=False,
        statistics=False,
        server_reprojection=False,
        vector_tiles=False,          # tile 字节服务归 Raster Runtime（fabric 仅元数据）
        range_requests=True,
        streaming=False,
        max_page_size=1,
    )


def _stac_defaults() -> AdapterCapabilitiesV2:
    return AdapterCapabilitiesV2(
        source_type="stac",
        bbox_pushdown=True,          # /search bbox
        filter_pushdown=False,       # filter 扩展支持零散；仅 cql2-text 探测后
        projection_pushdown=False,
        sort_pushdown=False,
        offset_pagination=False,
        cursor_pagination=True,      # /search next token
        spatial_predicates=["bbox"],
        temporal_filter=True,        # /search datetime
        aggregation=False,
        group_by=False,
        count=True,                  # numberMatched
        statistics=False,
        server_reprojection=False,
        vector_tiles=False,
        range_requests=False,
        streaming=False,
        max_page_size=1_000,
    )


def _generic_defaults(source_type: str) -> AdapterCapabilitiesV2:
    return AdapterCapabilitiesV2(source_type=source_type, max_page_size=1_000)


_DEFAULTS: Dict[str, AdapterCapabilitiesV2] = {
    "postgis": _postgis_defaults(),
    "ogc_api": _ogc_api_defaults(),
    "wfs": _wfs_defaults(),
    "arcgis": _arcgis_defaults(),
    "geoparquet": _geoparquet_defaults(),
    "flatgeobuf": _flatgeobuf_defaults(),
    "pmtiles": _pmtiles_defaults(),
    "stac": _stac_defaults(),
    "wms": _generic_defaults("wms"),
    "s3": _generic_defaults("s3"),
    "generic": _generic_defaults("generic"),
}

# registry 别名 → canonical
_ALIAS_MAP = {
    "postgres": "postgis",
    "postgresql": "postgis",
    "mock": "generic",
    "sample": "generic",
    "demo": "generic",
}


def default_capabilities(source_type: str) -> AdapterCapabilitiesV2:
    canonical = _ALIAS_MAP.get(source_type, source_type)
    caps = _DEFAULTS.get(canonical)
    if caps is None:
        return _generic_defaults(source_type)
    return caps.model_copy()


def get_capabilities(source_type: str, overrides: Optional[dict] = None) -> AdapterCapabilitiesV2:
    """默认矩阵 + adapter 探测覆盖（如 CQL2 conformance / maxRecordCount）。"""
    caps = default_capabilities(source_type)
    if overrides:
        # 仅允许收紧或探测性升级已定义字段；未知字段忽略。
        data = caps.model_dump()
        for k, v in overrides.items():
            if k in data:
                data[k] = v
        caps = AdapterCapabilitiesV2(**data)
    return caps


__all__ = ["default_capabilities", "get_capabilities"]
