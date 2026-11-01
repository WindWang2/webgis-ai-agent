"""OGC API - Coverages 发现 adapter（GOAL-02 / M1+M3，read-only discovery）。

职责边界（诚实封闭）：

- 只发现：collections（itemType=coverage / coverage 链接）、domainset、
  rangetype、声明输出格式 —— 全部只读 GET；
- 数值读取（subset → bounded raster → ref）在 ``coverage_read``（M3）；
  本 adapter 的 ``query`` 对要素语义 typed 拒绝；
- 三态资格：网格轴/波段/nodata/格式**以服务声明为准**，缺失 → unknown，
  绝不猜；声明互斥（格式均不支持）→ unsupported；
- 版本化 descriptor：``metadata["descriptor_version"] =
  "ogc-coverages-descriptor/1"``；
- 轴名/单位/分辨率来自 domainset 声明（subset 参数名跟着服务走，不
  硬编码 Lat/Long）。
"""
from __future__ import annotations

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
    DataFabricError,
    QueryUnsupportedError,
    SourceBadResponseError,
    SourceUnreachableError,
)
from app.services.data_fabric.ogc_capability import (
    RC_COVERAGE_FORMAT_NOT_DECLARED,
    SUPPORTED_COVERAGE_FORMATS,
    assess_coverage,
    normalize_coverage_format,
)
from app.services.data_fabric.security import (
    DataFabricSecurity,
    make_safe_session,
    safe_json_get,
)

logger = logging.getLogger(__name__)

DESCRIPTOR_VERSION = "ogc-coverages-descriptor/1"

_META_MAX_BYTES = 4 * 1024 * 1024

_COVERAGE_RELS = {
    "http://www.opengis.net/def/rel/ogc/1.0/coverage",
    "coverage",
}


class OGCCoveragesAdapter(GeospatialDataSourceAdapter):
    """OGC API - Coverages 的 coverage 发现与三态资格判定。"""

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.raw_url = self.profile.url or ""
        self.url = (
            DataFabricSecurity.validate_url(self.raw_url, allow_private=self.profile.allow_private)
            if self.raw_url
            else ""
        )
        self.options = self.profile.options or {}
        self.session = make_safe_session(allow_private=self.profile.allow_private)
        if "headers" in self.options:
            self.session.headers.update(self.options["headers"])
        self._conformance_cache: Optional[Tuple[float, List[str]]] = None

    def _get_conformance(self) -> List[str]:
        if self._conformance_cache and (time.monotonic() - self._conformance_cache[0]) < 60:
            return self._conformance_cache[1]
        classes: List[str] = []
        try:
            data = safe_json_get(
                self.session, self.url.rstrip("/") + "/conformance",
                timeout=5, max_bytes=_META_MAX_BYTES,
            )
            raw = data.get("conformsTo", []) if isinstance(data, dict) else []
            classes = [str(c) for c in raw if isinstance(c, str)]
        except Exception:
            classes = []
        self._conformance_cache = (time.monotonic(), classes)
        return classes

    def _declares_coverages(self) -> bool:
        return any("ogcapi-coverages" in c for c in self._get_conformance())

    def _get_json(self, url: str, timeout: float = 10.0) -> Dict[str, Any]:
        try:
            data = safe_json_get(self.session, url, timeout=timeout, max_bytes=_META_MAX_BYTES)
        except DataFabricError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SourceBadResponseError(f"OGC Coverages metadata fetch failed: {e}") from e
        if not isinstance(data, dict):
            raise SourceBadResponseError(
                f"OGC Coverages metadata at {DataFabricSecurity.redact_url(url)} is not a JSON object"
            )
        return data

    # ── adapter 契约 ──────────────────────────────────────────────────

    def probe(self) -> bool:
        """协议可辨识探测：conformance 声明 ogcapi-coverages，或存在
        coverage 集合（itemType=coverage / coverage 链接）。"""
        if not self.url:
            return False
        try:
            if self._declares_coverages():
                return True
            return bool(self._coverage_collections())
        except Exception as e:  # noqa: BLE001
            logger.debug("[ogc-coverages] probe failed for %s: %s", self.url, e)
            return False

    def capabilities(self) -> List[str]:
        return [
            "ogc_api_coverages",
            "coverage_discovery",
            "numeric_raster_source",
            "subset_read_ready",
        ]

    def _coverage_collections(self) -> List[Dict[str, Any]]:
        data = self._get_json(self.url.rstrip("/") + "/collections")
        cols = data.get("collections") or []
        out = []
        for col in cols if isinstance(cols, list) else []:
            if not isinstance(col, dict):
                continue
            is_coverage = str(col.get("itemType") or "").lower() == "coverage"
            has_link = any(
                isinstance(lnk, dict) and str(lnk.get("rel") or "") in _COVERAGE_RELS
                for lnk in col.get("links", []) or []
            )
            if is_coverage or has_link:
                out.append(col)
        return out

    def list_datasets(self) -> List[Dict[str, Any]]:
        if not self.url:
            return []
        try:
            cols = self._coverage_collections()
        except DataFabricError as e:
            logger.warning("[ogc-coverages] collections listing failed for %s: %s", self.url, e)
            return []
        return [
            {
                "id": str(c.get("id") or ""),
                "title": str(c.get("title") or c.get("id") or ""),
                "item_type": "coverage",
                "source_type": "ogc_api_coverages",
            }
            for c in cols
            if c.get("id")
        ]

    def _collection_doc(self, dataset_id: str) -> Dict[str, Any]:
        from urllib.parse import quote

        # R1 P1-6：dataset_id 来自远端响应 —— 段级编码封死注入。
        url = self.url.rstrip("/") + f"/collections/{quote(dataset_id, safe='')}"
        return self._get_json(url)

    @staticmethod
    def _declared_formats(col: Dict[str, Any]) -> List[str]:
        fmts = []
        for lnk in col.get("links", []) or []:
            if not isinstance(lnk, dict):
                continue
            rel = str(lnk.get("rel") or "")
            if rel in _COVERAGE_RELS or rel == "alternate":
                t = lnk.get("type")
                if t:
                    fmts.append(str(t))
        # 去重保序
        seen: set = set()
        out = []
        for f in fmts:
            if f not in seen:
                seen.add(f)
                out.append(f)
        return out

    @staticmethod
    def _axes_summary(domainset: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """domainset.axes → 每轴摘要（values 或 start/stop/num 正则轴）。"""
        if not domainset:
            return {}
        axes = domainset.get("axes") or {}
        out: Dict[str, Dict[str, Any]] = {}
        if not isinstance(axes, dict):
            return out
        for name, spec in axes.items():
            if not isinstance(spec, dict):
                continue
            entry: Dict[str, Any] = {}
            vals = spec.get("values")
            if isinstance(vals, list) and vals:
                try:
                    nums = [float(v) for v in vals]
                except (TypeError, ValueError):
                    entry["kind"] = "unknown"
                else:
                    entry = {
                        "kind": "values",
                        "count": len(nums),
                        "first": nums[0],
                        "last": nums[-1],
                    }
                    if len(nums) >= 2:
                        entry["resolution"] = (nums[-1] - nums[0]) / (len(nums) - 1)
            elif isinstance(spec.get("start"), (int, float)) and isinstance(spec.get("stop"), (int, float)):
                num = spec.get("num")
                entry = {
                    "kind": "regular",
                    "first": float(spec["start"]),
                    "last": float(spec["stop"]),
                }
                if isinstance(num, int) and num > 1:
                    entry["count"] = num
                    entry["resolution"] = (float(spec["stop"]) - float(spec["start"])) / (num - 1)
            else:
                entry = {"kind": "unknown"}
            out[str(name)] = entry
        return out

    @staticmethod
    def _bands_summary(rangetype: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not rangetype:
            return []
        bands = rangetype.get("bands") or rangetype.get("fields") or []
        out = []
        for b in bands if isinstance(bands, list) else []:
            if not isinstance(b, dict):
                continue
            out.append({
                "name": str(b.get("name") or b.get("identifier") or ""),
                "datatype": str(b.get("datatype") or "") or None,
                "nodata": b.get("nodata"),
                "unit": (b.get("unit") or {}).get("symbol")
                if isinstance(b.get("unit"), dict) else b.get("unit"),
            })
        return out

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        if not self.url:
            raise ValueError("OGC Coverages endpoint URL is missing in connection profile")
        try:
            col = self._collection_doc(dataset_id)
        except DataFabricError as e:
            raise SourceUnreachableError(
                f"OGC Coverages collection fetch failed for '{dataset_id}': {e}"
            ) from e

        base = self.url.rstrip("/")
        has_endpoint = any(
            isinstance(lnk, dict) and str(lnk.get("rel") or "") in _COVERAGE_RELS
            for lnk in col.get("links", []) or []
        )
        domainset: Optional[Dict[str, Any]] = None
        rangetype: Optional[Dict[str, Any]] = None
        if has_endpoint:
            try:
                domainset = self._get_json(f"{base}/collections/{dataset_id}/domainset")
            except DataFabricError as e:
                logger.debug("[ogc-coverages] domainset unavailable for '%s': %s", dataset_id, e)
            try:
                rangetype = self._get_json(f"{base}/collections/{dataset_id}/rangetype")
            except DataFabricError as e:
                logger.debug("[ogc-coverages] rangetype unavailable for '%s': %s", dataset_id, e)

        axes = self._axes_summary(domainset)
        bands = self._bands_summary(rangetype)
        declared_formats = self._declared_formats(col)
        supported_formats = [
            f for f in declared_formats
            if normalize_coverage_format(f) in {normalize_coverage_format(s) for s in SUPPORTED_COVERAGE_FORMATS}
        ]
        assessment = assess_coverage(
            has_coverage_endpoint=has_endpoint,
            has_domainset=domainset is not None,
            has_rangetype=rangetype is not None,
            grid_axis_names=list(axes.keys()),
            declared_formats=declared_formats,
        )

        bbox = None
        try:
            bbox = col["extent"]["spatial"]["bbox"][0]
            bbox = [float(v) for v in bbox]
        except (KeyError, IndexError, TypeError, ValueError):
            bbox = None  # 未声明 → None，绝不伪造

        return DatasetDescriptor(
            id=dataset_id,
            title=str(col.get("title") or dataset_id),
            description="OGC API - Coverages collection (numeric raster source; subset reads are budgeted)",
            source_type="ogc_api_coverages",
            source_id=self.profile.id,
            geometry_type="coverage",
            feature_type="raster",
            data_type="raster",
            srs=None,  # CRS 以 domainset/coverage 响应声明为准；collection 级不猜
            bbox=bbox,
            feature_count=None,
            fields=[
                {"name": b["name"], "type": "band", "description": f"nodata={b.get('nodata')}"}
                for b in bands
            ],
            metadata={
                "descriptor_version": DESCRIPTOR_VERSION,
                "protocol": "ogc_api_coverages",
                "has_coverage_endpoint": has_endpoint,
                "coverage_url": (
                    DataFabricSecurity.redact_url(f"{base}/collections/{dataset_id}/coverage")
                    if has_endpoint else None
                ),
                "axes": axes,
                "bands": bands,
                "formats_declared": declared_formats,
                "formats_supported": supported_formats,
                "formats_state": (
                    "supported" if supported_formats
                    else "unsupported" if declared_formats
                    else RC_COVERAGE_FORMAT_NOT_DECLARED
                ),
                "capabilities_conformance_declared": self._declares_coverages(),
                "capability": assessment.to_metadata(),
                "subset_read_ready": assessment.state == "supported",
            },
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        desc = self.describe(dataset_id)
        cap = desc.metadata.get("capability") or {}
        return {
            "schema": {
                "descriptor_version": DESCRIPTOR_VERSION,
                "axes": desc.metadata.get("axes"),
                "bands": desc.metadata.get("bands"),
                "formats": desc.metadata.get("formats_declared"),
            },
            "properties": {
                "capability_state": cap.get("state"),
                "subset_read_ready": desc.metadata.get("subset_read_ready"),
            },
            "features": [],
            "bbox": desc.bbox or [-180.0, -90.0, 180.0, 90.0],
        }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        raise QueryUnsupportedError(
            "OGC API - Coverages is a numeric raster source; feature queries "
            "are unsupported — use read_coverage_subset for budgeted subset reads",
            details={"dataset_id": dataset_id, "protocol": "ogc_api_coverages"},
        )

    def health(self) -> DataFabricHealth:
        import time as _time

        started = _time.time()
        if not self.url:
            return DataFabricHealth(
                status="unreachable",
                message="OGC Coverages URL missing",
                details={"hint": "Specify valid HTTP/HTTPS URL in connection profile."},
            )
        try:
            resp = self.session.get(self.url.rstrip("/") + "/conformance", timeout=5)
            latency = round((_time.time() - started) * 1000, 2)
            if resp.status_code == 200:
                return DataFabricHealth(
                    status="healthy",
                    message="OGC API Coverages service online and responsive",
                    details={"declares_coverages": self._declares_coverages()},
                    latency_ms=latency,
                )
            return DataFabricHealth(
                status="degraded",
                message=f"OGC API Coverages service responded with HTTP {resp.status_code}",
                details={"status_code": resp.status_code},
                latency_ms=latency,
            )
        except Exception as e:  # noqa: BLE001
            return DataFabricHealth(
                status="unreachable",
                message=f"OGC Coverages health check failed: {e}",
                details={"hint": f"Unable to reach {self.url}."},
                latency_ms=round((_time.time() - started) * 1000, 2),
            )


__all__ = ["DESCRIPTOR_VERSION", "OGCCoveragesAdapter"]
