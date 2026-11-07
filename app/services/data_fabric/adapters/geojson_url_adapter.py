"""Remote GeoJSON URL Adapter（G03 GD-02 / ADR-0286 D5）。

背景（BASELINE 断链 3）：``geojson`` alias 在 #767 被移除（它对真实远程
URL 静默返回合成要素）之后，用户提供的 URL 型 GeoJSON 进不了 fabric
查询/物化链。本 adapter 补上**真实实现**（非 demo）：

- ``probe``：受控 GET（``make_safe_session`` —— 私网/IMDS/重定向内网
  SSRF 防线复用）+ Content-Type 白名单（geo+json/json）+ FC 形状校验；
  非 FC 体 → ``SourceBadResponseError``（typed，绝不静默空结果）；
- 预算纪律：下载字节帽（``limits`` 单一口径）+ 要素帽（服务端全量取回
  后截断到 ``max_features``，如实披露 ``truncated`` —— GeoJSON 无分页
  协议，诚实截断优于谎称完整）；
- ``preview``/``describe``/``query`` 共享一次受控取回（进程内短缓存）。

URL 声明面：``config/sources/*.yaml``（source_registry 声明式源，与
local_file 同纪律——未声明即 typed unavailable，不做通配抓取）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DataFabricHealth,
    DatasetDescriptor,
    QueryResult,
    QuerySpec,
)
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import (
    SourceBadResponseError,
    SourceUnreachableError,
)
from app.services.data_fabric.limits import (
    max_features,
    max_response_bytes,
    query_timeout,
)
from app.services.data_fabric.security import (
    DataFabricSecurity,
    make_safe_session,
)

logger = logging.getLogger(__name__)

#: Content-Type 白名单（声明即数据格式事实；假类型 → typed 拒绝）。
_ALLOWED_CONTENT_TYPES = (
    "application/geo+json",
    "application/json",
    "application/vnd.geo+json",
    "text/json",
)
#: 取回结果短缓存（同一 adapter 实例内；probe→preview→query 不重复拉全量）。
_FETCH_CACHE_TTL = 30.0
MAX_PREVIEW_LIMIT = 100


def _content_type_allowed(content_type: str) -> bool:
    base = str(content_type or "").split(";")[0].strip().lower()
    return base in _ALLOWED_CONTENT_TYPES


class GeojsonUrlAdapter(GeospatialDataSourceAdapter):
    """单文件远程 GeoJSON（FC）真实数据源 adapter。"""

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.raw_url = self.profile.url or self.profile.endpoint_url or ""
        self.url = (
            DataFabricSecurity.validate_url(
                self.raw_url, allow_private=self.profile.allow_private)
            if self.raw_url else ""
        )
        self.session = make_safe_session(
            allow_private=self.profile.allow_private)
        self.options = self.profile.options or {}
        self._cache: Optional[Tuple[float, Dict[str, Any]]] = None

    # ── 受控取回（预算 + Content-Type + FC 形状三重守卫）─────────────
    def _fetch_fc(self) -> Dict[str, Any]:
        now = time.monotonic()
        if self._cache and (now - self._cache[0]) < _FETCH_CACHE_TTL:
            return self._cache[1]
        if not self.url:
            raise SourceUnreachableError(
                "geojson_url source has no URL declared",
                details={"hint": "declare url in config/sources/*.yaml"},
            )
        # 评审 F13：慢滴服务器在字节帽内可拖长总时长——deadline 以
        # (字节帽 / chunk) × per-read 超时 封顶（120s 硬顶），防慢速 DoS。
        per_read_timeout = query_timeout()
        overall_deadline = time.monotonic() + min(
            (max_response_bytes() // (64 * 1024) + 1) * per_read_timeout, 120.0
        )
        try:
            resp = self.session.get(
                self.url, timeout=per_read_timeout, stream=True,
            )
        except Exception as e:  # noqa: BLE001 — 网络面统一 typed 不可达
            raise SourceUnreachableError(
                f"geojson url unreachable: {e}") from e
        if resp.status_code >= 400:
            raise SourceUnreachableError(
                f"geojson url returned HTTP {resp.status_code}",
                details={"url": self.url[:200]},
            )
        if not _content_type_allowed(resp.headers.get("Content-Type")):
            raise SourceBadResponseError(
                "geojson url Content-Type is not a declared GeoJSON type: "
                f"{str(resp.headers.get('Content-Type'))[:64]}",
                details={"allowed": list(_ALLOWED_CONTENT_TYPES)},
            )
        max_bytes = max_response_bytes()
        chunks: List[bytes] = []
        total = 0
        # 复审 N4：流中断/超时抛原生 requests 异常——统一 typed 不可达，
        # 直连调用者（preview/query 面）不暴露传输细节。
        try:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if time.monotonic() > overall_deadline:
                    raise SourceBadResponseError(
                        "geojson url fetch exceeded overall deadline "
                        "(slow-drip source)", details={"url": self.url[:200]})
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise SourceBadResponseError(
                        f"geojson url body exceeds budget "
                        f"({max_bytes // (1024*1024)}MiB)",
                        details={"hint": "declare a smaller file or a "
                                         "paginated source"},
                    )
                chunks.append(chunk)
        except SourceBadResponseError:
            raise
        except Exception as e:  # noqa: BLE001 — 断流/超时统一 typed
            raise SourceUnreachableError(
                f"geojson url stream interrupted: {e}") from e
        try:
            doc = json.loads(b"".join(chunks))
        except ValueError as e:
            raise SourceBadResponseError(
                f"geojson url body is not valid JSON: {e}") from e
        if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
            raise SourceBadResponseError(
                "geojson url body is not a GeoJSON FeatureCollection "
                "(no fabrication: not Features, not geometry objects)",
                details={"url": self.url[:200]},
            )
        features = doc.get("features")
        if not isinstance(features, list):
            raise SourceBadResponseError(
                "geojson url FeatureCollection has no features array")
        self._cache = (now, doc)
        return doc

    def _bounded_features(self, limit: int) -> Tuple[List[Dict[str, Any]], bool, int]:
        doc = self._fetch_fc()
        features = doc.get("features") or []
        total = len(features)
        # 评审 F7：负数 limit 会变尾部切片——钳到 [0, cap]。
        cap = min(max(int(limit), 0), max_features())
        bounded = list(features[:cap])
        return bounded, total > cap, total

    # ── adapter contract ────────────────────────────────────────────
    def probe(self) -> bool:
        try:
            self._fetch_fc()
            return True
        except Exception:  # noqa: BLE001 — probe never raises
            return False

    def capabilities(self) -> List[str]:
        # 评审 F6：单文件源不做列裁剪/谓词下推——只声明真实能力。
        return ["limit"]

    def list_datasets(self) -> List[Dict[str, Any]]:
        doc = self._fetch_fc()
        name = str(self.options.get("name") or "geojson_url")
        return [{
            "id": name,
            "name": name,
            "data_type": "vector",
            "feature_count": len(doc.get("features") or []),
        }]

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        doc = self._fetch_fc()
        features = doc.get("features") or []
        geom_types: List[str] = []
        props: Dict[str, Any] = {}
        for f in features[:50]:
            if isinstance(f, dict):
                g = f.get("geometry") or {}
                gt = str(g.get("type") or "") if isinstance(g, dict) else ""
                if gt and gt not in geom_types:
                    geom_types.append(gt)
                if not props and isinstance(f.get("properties"), dict):
                    props = f["properties"]
        bbox = self.options.get("bbox")
        return DatasetDescriptor(
            id=str(dataset_id),
            source_type="geojson_url",
            source_id=self.profile.id,
            title=str(self.options.get("name") or "remote GeoJSON"),
            # 评审 F15：混合几何 FC 不得以首见类型代表——join 披露。
            geometry_type="+".join(geom_types) if geom_types else "Unknown",
            srs="EPSG:4326" if self.options.get("assume_wgs84") else None,
            bbox=list(bbox) if isinstance(bbox, (list, tuple)) else None,
            feature_count=len(features),
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        bounded, truncated, total = self._bounded_features(
            min(int(limit), MAX_PREVIEW_LIMIT) or 10)
        return {
            "features": bounded,
            "feature_count": len(bounded),
            "total_available": total,
            "truncated": truncated,
            "source_type": "geojson_url",
        }

    def query(self, dataset_id: str, query_spec: Optional[QuerySpec]) -> QueryResult:
        import time as _time

        t0 = _time.monotonic()
        requested = getattr(query_spec, "limit", None) if query_spec else None
        limit = int(requested) if requested else max_features()
        bounded, truncated, total = self._bounded_features(limit)
        return QueryResult(
            dataset_id=str(dataset_id),
            query_spec=query_spec,
            features=bounded,
            total_count=total,
            total_matching=total,
            returned_count=len(bounded),
            truncated=truncated,
            is_pushed_down=False,  # 截断发生在取回后（诚实披露）
            payload_type="geojson",
            execution_time_seconds=round(_time.monotonic() - t0, 3),
            metadata={
                "source_url": self.url[:200],
                "bounded_by": "feature_cap",
                # 评审 F6：单文件 GeoJSON 无服务端谓词——如实披露未求值。
                "filters_not_evaluated": True,
            },
        )

    def health(self) -> DataFabricHealth:
        reachable = self.probe()
        return DataFabricHealth(
            status="healthy" if reachable else "unhealthy",
            message="remote GeoJSON reachable" if reachable else "unreachable",
        )


__all__ = ["GeojsonUrlAdapter"]
