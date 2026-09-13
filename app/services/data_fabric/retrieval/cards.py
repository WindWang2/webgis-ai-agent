"""Dataset cards: searchable projections of the source registry (DS2, ADR-0172).

A card is what retrieval sees: composed searchable text + the structured
facets (coverage / freshness / license / verified / local / quota) the ranker
and the structured filters consume. Cards are honest projections — everything
comes from the declared registry (``config/sources/*.yaml``) and the known
local-asset catalogs; nothing is inferred beyond what is declared.

``to_d1()`` upgrades a card into the frozen D1 ``DatasetDescriptor`` contract
(contracts.py) so downstream waves consume D1, never the card internals.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DatasetCard(BaseModel):
    model_config = ConfigDict(extra="allow")

    card_id: str                      # "<source_id>/<dataset_id>"
    source_id: str
    source_name: str = ""
    dataset_id: str
    title: str
    description: str = ""
    keywords: List[str] = Field(default_factory=list)
    fields: List[str] = Field(default_factory=list)
    data_type: str = "vector"         # vector / raster / table / point / scene
    bbox: Optional[List[float]] = None
    temporal_start: Optional[str] = None
    temporal_end: Optional[str] = None
    granularity: Optional[str] = None
    license: str = "unknown"
    update_frequency: Optional[str] = None
    verified: bool = False
    local: bool = False
    requests_per_minute: Optional[int] = None
    protocol: str = ""

    # ── searchable text ──────────────────────────────────────────────────
    def searchable_text(self) -> str:
        """Deterministic text fed to the BM25 index / embedder."""
        parts = [
            self.title,
            self.source_name,
            self.dataset_id.replace("_", " ").replace("-", " "),
            self.description,
            " ".join(self.keywords),
            " ".join(self.fields),
            self.granularity or "",
            self.data_type,
        ]
        return " ".join(p for p in parts if p).strip().lower()

    # ── D1 upgrade seam ──────────────────────────────────────────────────
    def to_d1(self):
        from app.services.data_fabric.contracts import (
            CostHint,
            D1DatasetDescriptor,
            FreshnessInfo,
            QualitySignals,
            TemporalCoverage,
        )

        return D1DatasetDescriptor(
            id=self.dataset_id,
            source_type=self.protocol,
            source_id=self.source_id,
            title=self.title,
            name=self.title,
            description=self.description,
            data_type=self.data_type,
            bbox=self.bbox,
            crs=None,  # honest: per-dataset CRS is only known post-describe
            fields=[{"name": f, "type": "declared"} for f in self.fields],
            granularity=self.granularity,
            license=self.license,
            temporal_coverage=TemporalCoverage(
                start=self.temporal_start, end=self.temporal_end, declared_only=True
            ) if (self.temporal_start or self.temporal_end) else None,
            freshness=FreshnessInfo(update_frequency=self.update_frequency)
            if self.update_frequency else None,
            quality_signals=QualitySignals(verified=self.verified),
            cost_hint=CostHint(local=self.local, quota=self.requests_per_minute),
        )


_CJK = re.compile(r"[\u4e00-\u9fff]")


def _source_extra_keywords(source_id: str) -> List[str]:
    """Per-source retrieval keywords (Chinese aliases the portal search uses)."""
    return {
        "beijing_gov": ["北京", "首都", "政务", "政府数据"],
        "shanghai_gov": ["上海", "沪", "政务", "公共数据"],
        "guangdong_gov": ["广东", "珠三角", "湾区", "政务"],
        "local_osm": ["本地", "离线", "osm", "路网", "水系", "建筑", "铁路"],
        "local_poi": ["本地", "poi", "兴趣点", "高德", "门店", "设施"],
        "local_yearbook": ["本地", "年鉴", "统计", "县域", "乡镇", "经济指标"],
        "planetary_computer": ["卫星", "遥感", "影像", "全球", "植被", "dem"],
        "copernicus_dataspace": ["哨兵", "卫星", "遥感", "哥白尼", "欧洲"],
        "nasa_cmr_lpcloud": ["nasa", "卫星", "landsat", "modis", "高程"],
        "worldbank_api": ["世界银行", "宏观数据", "国家", "国际", "gdp"],
        "gbif_api": ["物种", "生物多样性", "观测", "动植物", "生态"],
        "overpass_api": ["osm", "在线", "开放地图", "兴趣点", "路网"],
    }.get(source_id, [])


def _local_asset_cards() -> List[DatasetCard]:
    """Cards for the known local assets (local_osm themes / local_poi /
    local_yearbook tables) — declared facts from the owning modules, honest
    about ``verified`` only when the module itself declares them."""
    cards: List[DatasetCard] = []
    try:
        from app.services.local_osm import THEME_SPECS

        theme_kw = {
            "pois": ["兴趣点", "poi", "设施", "门店", "餐馆", "学校", "医院", "amenity", "points of interest"],
            "roads": ["道路", "路网", "街道", "交通", "road", "street network"],
            "railways": ["铁路", "轨道", "地铁", "高铁", "railway", "metro"],
            "waterways": ["水系", "河流", "渠道", "河网", "river", "stream"],
        }
        for theme, spec in THEME_SPECS.items():
            cards.append(DatasetCard(
                card_id=f"local_osm/{theme}",
                source_id="local_osm",
                source_name="本地 OSM 主题数据",
                dataset_id=theme,
                title=f"OSM {theme} 主题（{spec.get('description', '')}）",
                description=spec.get("description", ""),
                keywords=theme_kw.get(theme, []) + ["osm", "本地", "gpkg"],
                data_type="vector",
                granularity="point_or_line",
                license="odBl-1.0",
                update_frequency="manual_ingest",
                verified=True,
                local=True,
                protocol="geopackage",
            ))
    except Exception:  # noqa: BLE001 — local asset import must never break retrieval
        pass
    cards.append(DatasetCard(
        card_id="local_yearbook/township_stats",
        source_id="local_yearbook",
        source_name="本地县域年鉴库",
        dataset_id="township_stats",
        title="县域/乡镇统计年鉴（人口、经济、财政、医疗等指标，2014-2025）",
        description="乡镇基本情况年度指标表（属性表，空间连接由调用方用 adcode 完成）",
        keywords=["统计", "年鉴", "人口", "gdp", "财政", "医疗", "乡镇", "县域", "经济指标", "本地"],
        data_type="table",
        granularity="county",
        temporal_start="2014-01-01",
        temporal_end="2025-12-31",
        license="internal",
        update_frequency="annual",
        verified=True,
        local=True,
        protocol="local_file",
    ))
    return cards


def build_cards(sources: Optional[List[Any]] = None) -> List[DatasetCard]:
    """Build cards from the registry (declared datasets + source-level cards)
    plus the known local assets. Deterministic order."""
    if sources is None:
        from app.services.data_fabric.source_registry import source_registry_service

        sources = source_registry_service.list_sources()

    cards: List[DatasetCard] = list(_local_asset_cards())
    for s in sources:
        local = s.protocol in {"local_file", "geopackage"} or s.source_id.startswith("local_")
        base = dict(
            source_id=s.source_id,
            source_name=s.name,
            update_frequency=s.freshness.get("update_frequency"),
            verified=s.verified,
            protocol=s.protocol,
            local=local,
            requests_per_minute=s.quota.requests_per_minute,
            temporal_start=s.temporal_coverage.start,
            temporal_end=s.temporal_coverage.end,
        )
        extra_kw = _source_extra_keywords(s.source_id)
        if s.datasets:
            for d in s.datasets:
                cards.append(DatasetCard(
                    card_id=f"{s.source_id}/{d.dataset_id}",
                    dataset_id=d.dataset_id,
                    title=d.title or d.dataset_id,
                    description=d.description or "",
                    keywords=list(extra_kw),
                    fields=[f.get("name") for f in d.fields if isinstance(f, dict) and f.get("name")],
                    data_type=d.data_type,
                    bbox=d.bbox,
                    granularity=d.granularity,
                    license=d.license or s.license,
                    **base,
                ))
        else:
            cards.append(DatasetCard(
                card_id=f"{s.source_id}/{s.source_id}",
                dataset_id=s.source_id,
                title=s.name,
                description=s.description,
                keywords=extra_kw + [t for t in s.operations],
                data_type="catalog",
                license=s.license,
                **base,
            ))
    return cards


__all__ = ["DatasetCard", "build_cards"]
