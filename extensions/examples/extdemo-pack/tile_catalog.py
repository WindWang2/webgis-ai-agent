"""ExtDemo 静态瓦片目录 adapter（ADR-0104 Wave 14 示例包）。

给扩展作者演示 ``GeospatialDataSourceAdapter`` ABC 的最小**诚实**实现：

- 数据来自同目录捆绑的 ``catalog.json``（静态、离线），不做任何网络 I/O；
- 能力旗标显式声明（is_raster_tile 源），pushdown 全部 False（最保守）；
- 镜像核心 ``WMSWMTSAdapter`` 的栅格语义：raster/tile 源**不回答矢量要素
  查询**——query 诚实返回空结果 + 原因元数据，绝不伪造要素；
- ``describe`` 对未知 dataset_id 显式抛错（fail loud），不静默编造条目。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DataFabricHealth,
    DatasetDescriptor,
    QueryResult,
    QuerySpec,
)
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter

SOURCE_TYPE = "demo_tile_catalog"

_CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"


def _load_catalog() -> Dict[str, Any]:
    """读取捆绑目录文件；坏文件显性失败（拒绝静默空目录）。"""
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"extdemo catalog.json unreadable: {exc}") from exc
    if not isinstance(data.get("datasets"), list):
        raise RuntimeError("extdemo catalog.json missing 'datasets' list")
    return data


class DemoTileCatalogAdapter(GeospatialDataSourceAdapter):
    """静态目录型栅格瓦片源：list/describe 来自捆绑 JSON，零网络。"""

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self._catalog = _load_catalog()

    def _entry(self, dataset_id: str) -> Dict[str, Any]:
        for entry in self._catalog.get("datasets", []):
            if entry.get("id") == dataset_id:
                return entry
        known = [e.get("id") for e in self._catalog.get("datasets", [])]
        raise ValueError(f"dataset {dataset_id!r} not in extdemo demo tile catalog (known: {known})")

    def probe(self) -> bool:
        """本地静态目录：永远可达；不做任何网络 I/O。"""
        return True

    def capabilities(self) -> List[str]:
        """能力旗标：纯栅格瓦片目录，无任何 pushdown。"""
        return ["raster_tile", "static_catalog", "offline"]

    def list_datasets(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": entry["id"],
                "title": entry.get("title", entry["id"]),
                "source_type": SOURCE_TYPE,
                "geometry_type": "Raster",
            }
            for entry in self._catalog.get("datasets", [])
        ]

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        entry = self._entry(dataset_id)
        return DatasetDescriptor(
            id=entry["id"],
            title=entry.get("title", entry["id"]),
            description=entry.get("description", ""),
            source_type=SOURCE_TYPE,
            geometry_type="Raster",
            data_type="raster",
            srs=entry.get("srs"),
            bbox=entry.get("bbox"),
            # 栅格源没有要素计数——诚实未知（None），绝不伪造 0 行矢量。
            feature_count=None,
            fields=[],
            metadata={
                "catalog_id": self._catalog.get("catalog_id"),
                "min_zoom": entry.get("min_zoom"),
                "max_zoom": entry.get("max_zoom"),
                "tile_size": entry.get("tile_size"),
                "format": entry.get("format"),
                "offline": True,
            },
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        entry = self._entry(dataset_id)
        # 栅格/瓦片源没有矢量样本可预览（与核心 WMSWMTSAdapter 同语义）。
        return {
            "schema": {"layer": dataset_id, "type": "Raster"},
            "properties": {
                "layer_name": entry.get("title", dataset_id),
                "min_zoom": entry.get("min_zoom"),
                "max_zoom": entry.get("max_zoom"),
                "format": entry.get("format"),
            },
            "features": [],
            "bbox": entry.get("bbox"),
        }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        self._entry(dataset_id)
        # 诚实语义：raster/tile 源不支持矢量要素查询 → 空结果 + 原因元数据。
        return QueryResult(
            dataset_id=dataset_id,
            features=[],
            total_count=0,
            schema_info={"geometry_type": "Raster", "layer": dataset_id},
            metadata={
                "reason": "raster_tile_source_has_no_vector_features",
                "pushdown_bbox": bool(query_spec.bbox),
                "offline": True,
            },
        )

    def health(self) -> DataFabricHealth:
        """无网络的诚实健康：捆绑目录可读、有条目即 healthy。"""
        count = len(self._catalog.get("datasets", []))
        healthy = count > 0
        return DataFabricHealth(
            status="healthy" if healthy else "degraded",
            source_type=SOURCE_TYPE,
            adapter="DemoTileCatalogAdapter",
            reachable=True,
            message=f"static bundled catalog ({count} datasets); no network access",
        )
