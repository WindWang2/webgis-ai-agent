"""ads-v1 local data asset index (DS7, ADR-0177, gap A9).

One scan → one inventory of the three local libraries (local_osm themes,
local_poi, local_yearbook), each either **available** (with a normalized
meta record and pyogrio/sqlite-backed layer facts) or **explicitly
unavailable** with the ingest hint for its manage.py command — a missing
library is never reported as an empty result and never fabricated.

The normalized meta schema (``META_SCHEMA``) replaces the free-form sidecar
``meta.json`` files: {crs, bbox, rows, temporal_start, temporal_end, fields,
source, ingested_at}. Unknown values stay None — 补齐 means the scan fills
what the owning module knows, never invents.

Cards from the scan merge into the DS2 retrieval index (local assets carry
``cost_hint.local=True`` → they win cost naturally but never displace
higher-relevance online datasets — DS2 ranking already guarantees this).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

#: Normalized sidecar meta schema (DS7.2) — the three libraries converge on this.
META_SCHEMA = {
    "crs": "str|null (e.g. EPSG:4326)",
    "bbox": "[minx,miny,maxx,maxy]|null",
    "rows": "int|null (feature/row count)",
    "temporal_start": "ISO date|null",
    "temporal_end": "ISO date|null",
    "fields": "[{name,type}]",
    "source": "str (provenance, e.g. osm pbf snapshot)",
    "ingested_at": "ISO datetime|null",
}

_REQUIRED_META_KEYS = set(META_SCHEMA)

#: Ingest hints per library (surfaced verbatim in the unavailable report).
INGEST_HINTS = {
    "local_osm": "运行 python manage.py osm-ingest 预处理 china-*.osm.pbf（LOCAL_GEODATA_DIR 下自动发现）",
    "local_poi": "运行 python manage.py gd-poi-ingest 预处理高德 POI 库",
    "local_yearbook": "运行 python manage.py yearbook-ingest 生成县域年鉴 sqlite",
}


def normalize_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Free-form sidecar meta → normalized schema; unknown keys reported."""
    meta = meta or {}
    normalized = {
        "crs": meta.get("crs") or meta.get("srs"),
        "bbox": meta.get("bbox"),
        "rows": meta.get("rows") or meta.get("total_rows") or meta.get("feature_count"),
        "temporal_start": meta.get("temporal_start") or meta.get("year_start"),
        "temporal_end": meta.get("temporal_end") or meta.get("year_end"),
        "fields": meta.get("fields") or [],
        "source": meta.get("source") or meta.get("origin"),
        "ingested_at": meta.get("ingested_at") or meta.get("generated_at"),
    }
    missing = sorted(k for k, v in normalized.items() if v is None and k != "fields")
    return {"normalized": normalized, "missing": missing}


class LocalAsset(BaseModel):
    model_config = ConfigDict(extra="allow")

    library: str                    # local_osm / local_poi / local_yearbook
    asset_id: str
    title: str
    description: str = ""
    path: str
    data_type: str = "vector"
    layers: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)   # normalized meta
    meta_missing: List[str] = Field(default_factory=list)


class UnavailableLibrary(BaseModel):
    model_config = ConfigDict(extra="allow")

    library: str
    reason: str
    ingest_hint: str


class ScanReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    root: str
    available: List[LocalAsset] = Field(default_factory=list)
    unavailable: List[UnavailableLibrary] = Field(default_factory=list)
    scanned_at: str = ""

    @property
    def all_available(self) -> bool:
        return not self.unavailable


# ── scan ─────────────────────────────────────────────────────────────────────


def _root(root: Optional[str] = None) -> Optional[Path]:
    if root:
        return Path(root)
    try:
        from app.core.config import settings

        raw = (settings.LOCAL_GEODATA_DIR or "").strip()
        return Path(raw).expanduser() if raw else None
    except Exception:  # noqa: BLE001 — settings-less operation scans nothing
        return None


def _gpkg_layers(path: Path) -> List[str]:
    try:
        import pyogrio

        return [str(row[0]) for row in pyogrio.list_layers(str(path))]
    except Exception as e:  # noqa: BLE001 — unreadable gpkg → unavailable asset
        logger.warning("[local_index] cannot list layers of %s: %s", path, e)
        return []


def _gpkg_meta(path: Path, layer: str) -> Dict[str, Any]:
    try:
        import pyogrio

        info = pyogrio.read_info(str(path), layer=layer)
        return {
            "crs": str(info.get("crs") or "") or None,
            "rows": int(info["features"]),
            "fields": [
                {"name": str(n), "type": str(t)}
                for n, t in zip(info["fields"].tolist(), info["dtypes"].tolist())
            ],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("[local_index] read_info failed for %s/%s: %s", path, layer, e)
        return {}


def _scan_osm(root: Optional[Path]) -> List[LocalAsset]:
    from app.services.local_osm import THEME_SPECS, osm_gpkg_dir

    # root override (scan-time injection) wins over settings resolution
    gpkg_dir = Path(root) / "osm_gpkg" if root is not None else osm_gpkg_dir()
    if not gpkg_dir.exists():
        return []
    assets: List[LocalAsset] = []
    for theme, spec in THEME_SPECS.items():
        path = gpkg_dir / f"{theme}.gpkg"
        if not path.exists():
            continue
        meta = _gpkg_meta(path, theme)
        base_meta = normalize_meta({
            "source": "OSM PBF snapshot (osm-ingest)",
            **meta,
        })
        assets.append(LocalAsset(
            library="local_osm",
            asset_id=f"local_osm/{theme}",
            title=f"OSM {theme}（{spec.get('description', '')}）",
            description=spec.get("description", ""),
            path=str(path),
            data_type="vector",
            layers=[theme],
            meta=base_meta["normalized"],
            meta_missing=base_meta["missing"],
        ))
    return assets


def _scan_poi(root: Optional[Path]) -> List[LocalAsset]:
    try:
        from app.services.local_poi import gd_poi_available, gd_poi_gpkg_path, _meta_path

        if root is not None:
            gpkg_path = Path(root) / "gd_pois.gpkg"
            available = gpkg_path.exists()
            meta_path = Path(root) / "meta.json"
        else:
            available = gd_poi_available()
            gpkg_path = gd_poi_gpkg_path()
            meta_path = _meta_path()
        if not available:
            return []
        sidecar = {}
        try:
            if meta_path.exists():
                sidecar = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 — sidecar corruption never kills the scan
            logger.warning("[local_index] poi meta.json unreadable: %s", e)
        normalized = normalize_meta({
            "source": "高德 POI 全量库（gcj02→wgs84 已换算）",
            "crs": "EPSG:4326",
            **sidecar,
            "fields": [{"name": "name", "type": "str"}, {"name": "category", "type": "str"},
                       {"name": "subtype", "type": "str"}, {"name": "typecode", "type": "str"}],
        })
        return [LocalAsset(
            library="local_poi",
            asset_id="local_poi/gd_pois",
            title="本地高德 POI 库",
            description="高德 POI 全量点库（本地优先链第一跳），category 为高德一级分类",
            path=str(gpkg_path),
            data_type="vector",
            layers=["gd_pois"],
            meta=normalized["normalized"],
            meta_missing=normalized["missing"],
        )]
    except Exception as e:  # noqa: BLE001
        logger.warning("[local_index] poi scan failed: %s", e)
        return []


def _scan_yearbook(root: Optional[Path]) -> List[LocalAsset]:
    try:
        from app.services.local_yearbook import yearbook_available, yearbook_db_path

        if root is not None:
            db_path = Path(root) / "yearbook" / "yearbook.sqlite"
            available = db_path.exists()
        else:
            db_path = yearbook_db_path()
            available = yearbook_available()
        if not available:
            return []
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            fields: List[Dict[str, str]] = []
            if tables:
                cols = conn.execute(f'PRAGMA table_info("{tables[0]}")').fetchall()  # nosec B608 — identifier from discovered list
                fields = [{"name": c[1], "type": c[2]} for c in cols]
        finally:
            conn.close()
        normalized = normalize_meta({
            "source": "县域年鉴 xlsx 汇编（yearbook-ingest）",
            "temporal_start": "2014-01-01",
            "temporal_end": "2025-12-31",
            "fields": fields,
        })
        return [LocalAsset(
            library="local_yearbook",
            asset_id="local_yearbook/township_stats",
            title="县域/乡镇统计年鉴",
            description="乡镇基本情况年度指标表（属性表，空间连接由调用方用 adcode 完成）",
            path=str(db_path),
            data_type="table",
            layers=tables,
            meta=normalized["normalized"],
            meta_missing=normalized["missing"],
        )]
    except Exception as e:  # noqa: BLE001
        logger.warning("[local_index] yearbook scan failed: %s", e)
        return []


def scan_local_assets(root: Optional[str] = None) -> ScanReport:
    """One inventory pass over the three local libraries (honest availability)."""
    resolved = _root(root)
    now = datetime.now(timezone.utc).isoformat()
    if resolved is None or not resolved.exists():
        return ScanReport(
            root=str(resolved or ""),
            unavailable=[
                UnavailableLibrary(library=lib, reason="LOCAL_GEODATA_DIR 未配置或不存在", ingest_hint=INGEST_HINTS[lib])
                for lib in INGEST_HINTS
            ],
            scanned_at=now,
        )

    available: List[LocalAsset] = []
    unavailable: List[UnavailableLibrary] = []

    osm_assets = _scan_osm(resolved)
    if osm_assets:
        available.extend(osm_assets)
    else:
        unavailable.append(UnavailableLibrary(library="local_osm", reason="osm_gpkg 主题文件缺失", ingest_hint=INGEST_HINTS["local_osm"]))

    poi = _scan_poi(resolved)
    if poi:
        available.extend(poi)
    else:
        unavailable.append(UnavailableLibrary(library="local_poi", reason="gd_pois.gpkg 未生成", ingest_hint=INGEST_HINTS["local_poi"]))

    yearbook = _scan_yearbook(resolved)
    if yearbook:
        available.extend(yearbook)
    else:
        unavailable.append(UnavailableLibrary(library="local_yearbook", reason="yearbook.sqlite 未生成", ingest_hint=INGEST_HINTS["local_yearbook"]))

    return ScanReport(root=str(resolved), available=available, unavailable=unavailable, scanned_at=now)


def assets_to_cards(assets: List[LocalAsset]) -> List[Any]:
    """Scan assets → DS2 DatasetCards (merged into the retrieval index)."""
    from app.services.data_fabric.retrieval.cards import DatasetCard

    cards: List[Any] = []
    for a in assets:
        cards.append(DatasetCard(
            card_id=a.asset_id,
            source_id=a.library,
            source_name=a.library,
            dataset_id=a.asset_id.split("/", 1)[-1],
            title=a.title,
            description=a.description,
            keywords=["本地", "离线", a.library],
            fields=[f["name"] for f in a.meta.get("fields", []) if isinstance(f, dict)],
            data_type=a.data_type,
            license="internal" if a.library != "local_osm" else "odBl-1.0",
            update_frequency="manual_ingest",
            verified=True,
            local=True,
            protocol="geopackage" if a.data_type == "vector" else "local_file",
        ))
    return cards


__all__ = [
    "META_SCHEMA",
    "INGEST_HINTS",
    "normalize_meta",
    "LocalAsset",
    "UnavailableLibrary",
    "ScanReport",
    "scan_local_assets",
    "assets_to_cards",
]
