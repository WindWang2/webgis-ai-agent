"""WFS (Web Feature Service) Data Source Adapter — V2 (ADR-0094).

相对 V1 的升级（修复审计 C1/M3/M4 + Wave E 契约）：
- CRS dict 支持：GeoJSON ``crs`` 成员可为 ``{"type":"name","properties":
  {"name":"urn:..."}}``（GeoServer 常见）——正确提取并归一化，不再误判拒绝
  （审计 C1：此前 str(dict) 后拒绝 → 熔断全源不可用）。
- 轴序安全：WFS 1.1+/2.0 srsName 使用 URN 形式（CRS84 明确 lon/lat），
  WFS 1.0 保留 EPSG 短形式（审计 M3）。
- startIndex 分页（offset 下推，1.1+；1.0 不发送并如实标注）。
- FES 过滤器：typed AST → 模板化 FES XML（POST GetFeature，实体转义）。
- propertyName 投影下推。
- DescribeFeatureType 解析 XSD → 字段 schema（此前 fields 恒空）。
- 全部 GET 经 bounded_get（Content-Length + 解压后字节上限）。
- V2：normalize → plan → 执行；QueryResult 附 plan/evidence；
  numberMatched 作为 total_matching；truncation 如实（不再 2000=全部）。
"""
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import (
    DataFabricError,
    InvalidQueryError,
    SourceBadResponseError,
)
from app.services.data_fabric.metadata import normalize_crs
from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.compilers import (
    compile_bbox_fes,
    compile_predicate_fes,
)
from app.services.data_fabric.query.evidence import build_evidence
from app.services.data_fabric.query.models import QuerySpecV2
from app.services.data_fabric.query.normalize import normalize_query_spec
from app.services.data_fabric.query.planner import plan_query
from app.services.data_fabric.security import (
    DataFabricSecurity,
    bounded_get,
    make_safe_session,
)
from app.schemas.data_fabric_schema import (
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
    ConnectionProfile,
)

logger = logging.getLogger(__name__)

MAX_PREVIEW_LIMIT = 100
MAX_QUERY_LIMIT = 10_000

_URN_CRS84 = "urn:ogc:def:crs:OGC:1.3:CRS84"


def extract_geojson_crs(declared: Any) -> Optional[str]:
    """GeoJSON ``crs`` 成员 → 规范名。支持 str 与 GeoServer dict 形式
    （审计 C1）。返回 None 表示未声明。"""
    if declared is None:
        return None
    if isinstance(declared, dict):
        props = declared.get("properties")
        if isinstance(props, dict):
            name = props.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        t = declared.get("type")
        if isinstance(t, str) and t.strip():
            return t.strip()
        return None
    if isinstance(declared, str):
        return declared.strip() or None
    return None


def _is_wgs84_name(raw: str, normalized: Optional[str]) -> bool:
    """CRS 名是否为 WGS84 lon/lat（接受 EPSG:4326 / CRS84 / URN 形式）。"""
    u = (normalized or raw or "").upper()
    return (
        u in ("EPSG:4326", "CRS84", "OGC:CRS84")
        or "CRS84" in u
        or "EPSG::4326" in u
        or "EPSG:4326" in u
    )


def _srsname_for_version(version: str) -> str:
    """WFS 版本感知的输出 CRS 形式（轴序安全，审计 M3）。

    - 1.0.0：``EPSG:4326``（1.0 无 URN 惯例，短形式即 lon/lat）
    - 1.1.0/2.0.x：URN CRS84（显式 lon/lat，避免 EPSG:4326 的 lat/lon 歧义）
    """
    if version.startswith("1.0"):
        return "EPSG:4326"
    return _URN_CRS84


class WFSAdapter(GeospatialDataSourceAdapter):
    """OGC WFS adapter（1.0/1.1/2.0 KVP + 过滤器 POST）。"""

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.raw_url = self.profile.url or ""
        self.url = (
            DataFabricSecurity.validate_url(self.raw_url, allow_private=self.profile.allow_private)
            if self.raw_url
            else ""
        )
        self.options = self.profile.options or {}
        self.version = str(self.options.get("version", "2.0.0"))
        self.session = make_safe_session(allow_private=self.profile.allow_private)
        if "headers" in self.options:
            self.session.headers.update(self.options["headers"])
        self._caps_cache: Optional[Tuple[float, Any]] = None  # (monotonic, tree)

    # ── 基础契约 ───────────────────────────────────────────────────────

    def probe(self) -> bool:
        if not self.url:
            return False
        try:
            params = {
                "SERVICE": "WFS",
                "REQUEST": "GetCapabilities",
                "VERSION": self.version,
            }
            body = bounded_get(self.session, self.url, params=params, timeout=8, max_bytes=8 * 1024 * 1024)
            return bool(body)
        except Exception as e:
            logger.debug("WFS probe failed for %s: %s", DataFabricSecurity.redact_url(self.url), e)
            return False

    def capabilities(self) -> List[str]:
        return [
            "pushdown_bbox",
            "pushdown_filter",
            "projection_pushdown",
            "startIndex_pagination",
            "vector_features",
            "wfs",
            "ogc_standard",
        ]

    def _capabilities_tree(self, max_age_s: float = 60.0):
        """GetCapabilities（带短 TTL 缓存；describe/list 共享）。"""
        import time as _time

        now = _time.monotonic()
        if self._caps_cache and (now - self._caps_cache[0]) < max_age_s:
            return self._caps_cache[1]
        params = {
            "SERVICE": "WFS",
            "REQUEST": "GetCapabilities",
            "VERSION": self.version,
        }
        body = bounded_get(self.session, self.url, params=params, timeout=10, max_bytes=16 * 1024 * 1024)
        tree = DataFabricSecurity.parse_safe_xml(body)
        self._caps_cache = (now, tree)
        return tree

    @staticmethod
    def _local_tag(elem) -> str:
        return elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

    def list_datasets(self) -> List[Dict[str, Any]]:
        if not self.url:
            return []
        try:
            tree = self._capabilities_tree()
            datasets = []
            for elem in tree.iter():
                if self._local_tag(elem) == "FeatureType":
                    ft_name = ""
                    ft_title = ""
                    ft_abstract = ""
                    for child in elem:
                        child_tag = self._local_tag(child)
                        if child_tag == "Name":
                            ft_name = (child.text or "").strip()
                        elif child_tag == "Title":
                            ft_title = (child.text or "").strip()
                        elif child_tag == "Abstract":
                            ft_abstract = (child.text or "").strip()
                    if ft_name:
                        datasets.append({
                            "id": ft_name,
                            "title": ft_title or ft_name,
                            "description": ft_abstract,
                            "source_type": "wfs",
                        })
            return datasets
        except Exception as e:
            logger.warning("WFS list_datasets failed: %s", e)
            return []

    def _get_capabilities_entry(self, dataset_id: str) -> Optional[Dict[str, str]]:
        tree = self._capabilities_tree()
        for elem in tree.iter():
            if self._local_tag(elem) != "FeatureType":
                continue
            info: Dict[str, str] = {}
            for child in elem:
                child_tag = self._local_tag(child)
                if child_tag in ("DefaultSRS", "SRS", "OtherSRS") and "srs" not in info:
                    info["srs"] = (child.text or "").strip()
                elif child_tag == "Name" and (child.text or "").strip() == dataset_id:
                    info["name"] = dataset_id
                elif child_tag == "WGS84BoundingBox":
                    corners = [
                        (c.text or "").strip()
                        for c in child
                        if self._local_tag(c) in ("LowerCorner", "UpperCorner")
                    ]
                    if len(corners) == 2:
                        info["lower"] = corners[0]
                        info["upper"] = corners[1]
            if info.get("name") == dataset_id:
                return info
        return None

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        if not self.url:
            raise InvalidQueryError("WFS endpoint URL is missing in connection profile")

        srs: Optional[str] = None
        bbox = None
        describe_error: Optional[str] = None
        try:
            entry = self._get_capabilities_entry(dataset_id)
        except Exception as e:
            entry = None
            describe_error = str(e)
            logger.warning("WFS describe GetCapabilities failed for '%s': %s", dataset_id, e)

        if entry is not None:
            raw_srs = entry.get("srs")
            if raw_srs:
                srs = normalize_crs(raw_srs)
            lower = entry.get("lower", "")
            upper = entry.get("upper", "")
            try:
                if lower and upper:
                    lx, ly = (float(v) for v in lower.split()[:2])
                    ux, uy = (float(v) for v in upper.split()[:2])
                    bbox = [lx, ly, ux, uy]
            except ValueError:
                bbox = None

        # DescribeFeatureType → 字段 schema（此前恒为空）
        fields: List[Dict[str, Any]] = []
        try:
            fields = self._describe_feature_type_fields(dataset_id)
        except Exception as e:
            logger.debug("WFS DescribeFeatureType failed for '%s': %s", dataset_id, e)

        metadata: Dict[str, Any] = {
            # redact（审计 F-4：descriptor 内 URL 不携带 userinfo）
            "endpoint_url": DataFabricSecurity.redact_url(self.url),
            "feature_type": dataset_id,
            "wfs_version": self.version,
        }
        if describe_error:
            metadata["describe_error"] = describe_error
        elif entry is None:
            metadata["describe_error"] = f"FeatureType '{dataset_id}' not found in GetCapabilities"

        return DatasetDescriptor(
            id=dataset_id,
            title=dataset_id,
            description=f"WFS FeatureType {dataset_id}",
            source_type="wfs",
            geometry_type="Feature",
            srs=srs,
            bbox=bbox,
            feature_count=None,
            fields=fields,
            metadata=metadata,
        )

    def _describe_feature_type_fields(self, dataset_id: str) -> List[Dict[str, Any]]:
        """DescribeFeatureType (W3C XSD) → 字段名/类型（defusedxml 解析）。"""
        params = {
            "SERVICE": "WFS",
            "REQUEST": "DescribeFeatureType",
            "VERSION": self.version,
            "TYPENAME": dataset_id,
        }
        body = bounded_get(self.session, self.url, params=params, timeout=10, max_bytes=8 * 1024 * 1024)
        root = DataFabricSecurity.parse_safe_xml(body)
        fields: List[Dict[str, Any]] = []
        local_name = dataset_id.split(":")[-1]
        for elem in root.iter():
            tag = self._local_tag(elem)
            # schema/complexType/sequence/element；elementFormQualified 变体兼容
            if tag == "element" and elem.get("type") is None:
                name = elem.get("name")
                if name and not name.startswith("ns:"):
                    # 顶层 element 是类型本身；内层 element 是属性
                    fields.append({"name": name, "type": "unknown"})
        # 过滤掉与类型同名的顶层 element，保留属性 element
        fields = [f for f in fields if f["name"] != local_name]
        # 尽可能提取带类型的 element（任意深度的 sequence）
        typed: List[Dict[str, Any]] = []
        for elem in root.iter():
            if self._local_tag(elem) == "element":
                name = elem.get("name")
                etype = elem.get("type") or "unknown"
                if name and name != local_name:
                    typed.append({"name": name, "type": str(etype).split(":")[-1]})
        return typed or fields

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        bounded_limit = max(1, min(limit, MAX_PREVIEW_LIMIT))
        q_res = self.query(dataset_id, QuerySpec(limit=bounded_limit))
        return {
            "schema": {"feature_type": dataset_id},
            "properties": q_res.features[0].get("properties", {}) if q_res.features else {},
            "features": q_res.features,
            "bbox": None,
        }

    # ── 查询 ───────────────────────────────────────────────────────────

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        started = time.monotonic()
        v2 = normalize_query_spec(query_spec)  # 失败抛 typed InvalidQueryError
        try:
            return self._execute_v2(dataset_id, v2, started)
        except DataFabricError:
            raise
        except Exception as e:
            logger.warning("WFS query error for '%s': %s", dataset_id, e)
            from app.services.data_fabric.errors import SourceBadResponseError

            raise SourceBadResponseError(f"WFS query error: {e}") from e

    def _execute_v2(self, dataset_id: str, v2: QuerySpecV2, started: float) -> QueryResult:
        if not self.url:
            raise InvalidQueryError("WFS adapter unconfigured (missing URL)")

        descriptor = self.describe(dataset_id)
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)
        caps = get_capabilities("wfs")
        plan = plan_query(v2, descriptor, caps, source_id=self.profile.id, dataset_fingerprint=fp)

        page = v2.page
        limit = page.limit
        offset = page.offset if hasattr(page, "offset") else 0

        srs_name = _srsname_for_version(self.version)
        params: Dict[str, Any] = {
            "SERVICE": "WFS",
            "REQUEST": "GetFeature",
            "VERSION": self.version,
            "TYPENAME": dataset_id,
            "TYPENAMES": dataset_id,
            "OUTPUTFORMAT": "application/json",
            "COUNT": limit,
            "MAXFEATURES": limit,
            "SRSNAME": srs_name,
            "srsName": srs_name,
        }

        # startIndex（1.1+；1.0 不支持 → 如实记录 local 限制）
        offset_pushed = False
        if offset and not self.version.startswith("1.0"):
            params["STARTINDEX"] = offset
            offset_pushed = True
        elif offset:
            plan = plan.model_copy(update={
                "warnings": plan.warnings + ["WFS 1.0 lacks startIndex; offset not pushed"],
                "local_filters": plan.local_filters + ["offset(slice-local)"],
            })

        # propertyName 投影
        if v2.select is not None:
            params["PROPERTYNAME"] = ",".join(v2.select)

        # bbox（KVP；跨反子午线 bbox 由 BBOX OR 不支持 → 拆分执行一侧会被
        # WFS 拒绝，这里显式报 typed error）
        if v2.spatial is not None:
            if v2.spatial.op != "bbox":
                raise InvalidQueryError(
                    f"WFS supports bbox spatial pushdown only (got '{v2.spatial.op}'); "
                    "narrow the query or use the PostGIS source"
                )
            from app.services.data_fabric.query.predicates import bbox_crosses_antimeridian

            if bbox_crosses_antimeridian(v2.spatial.bbox):
                raise InvalidQueryError(
                    "antimeridian-crossing bbox is not supported by WFS BBOX pushdown"
                )
            minx, miny, maxx, maxy = v2.spatial.bbox
            params["BBOX"] = f"{minx},{miny},{maxx},{maxy},{_URN_CRS84 if not self.version.startswith('1.0') else 'EPSG:4326'}"

        # 属性过滤器 → FES XML（POST）
        post_xml: Optional[str] = None
        if v2.filter is not None:
            filter_xml = compile_predicate_fes(v2.filter)
            if v2.spatial is not None:
                bbox_xml = compile_bbox_fes(v2.spatial.bbox)
                filter_xml = f'<ogc:And xmlns:ogc="http://www.opengis.net/ogc">{filter_xml}{bbox_xml}</ogc:And>'
                params.pop("BBOX", None)
            post_xml = self._build_get_feature_xml(dataset_id, limit, offset if offset_pushed else 0, filter_xml)

        try:
            if post_xml is not None:
                resp = self.session.post(
                    self.url,
                    data=post_xml.encode("utf-8"),
                    headers={"Content-Type": "text/xml; charset=utf-8"},
                    timeout=min(v2.execution.deadline_s, 30.0),
                    stream=True,
                )
                resp.raise_for_status()
                chunks = []
                total = 0
                cap = 64 * 1024 * 1024
                try:
                    for chunk in resp.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            total += len(chunk)
                            if total > cap:
                                raise SourceBadResponseError(f"WFS response exceeded {cap} bytes")
                            chunks.append(chunk)
                finally:
                    resp.close()
                import json as _json

                data = _json.loads(b"".join(chunks).decode("utf-8", errors="strict"))
            else:
                body = bounded_get(
                    self.session, self.url, params=params,
                    timeout=min(v2.execution.deadline_s, 30.0),
                )
                # #766 语义保留：200 但非 JSON（WFS 2.0 忽略 OUTPUTFORMAT 返回
                # GML）→ typed fetch failure，绝不是静默空成功。
                stripped = body.lstrip()
                if not stripped.startswith(b"{"):
                    raise SourceBadResponseError(
                        "WFS GetFeature returned a non-JSON 200 body; "
                        "JSON output not supported by this server",
                        details={"body_prefix": stripped[:64].decode("utf-8", "replace")},
                    )
                import json as _json

                data = _json.loads(body.decode("utf-8", errors="strict"))
        except DataFabricError:
            raise
        except Exception as e:
            raise SourceBadResponseError(f"WFS GetFeature failed: {e}") from e

        features = data.get("features", []) if isinstance(data, dict) else []
        if not isinstance(features, list):
            features = []

        # CRS 校验（审计 C1：dict 形式正确解析）
        declared = data.get("crs") if isinstance(data, dict) else None
        crs_name = extract_geojson_crs(declared)
        if crs_name:
            normalized = normalize_crs(crs_name)
            if not _is_wgs84_name(crs_name, normalized):
                raise SourceBadResponseError(
                    f"unsupported CRS in response: {crs_name} (normalized {normalized}); "
                    "only EPSG:4326/CRS84 is supported",
                    details={"crs": crs_name},
                )

        total_matched = data.get("numberMatched") if isinstance(data, dict) else None
        if isinstance(total_matched, str) and total_matched.isdigit():
            total_matched = int(total_matched)
        elif not isinstance(total_matched, int):
            total_matched = None

        returned = len(features)
        truncated = returned >= limit
        if total_matched is not None:
            truncated = total_matched > offset + returned

        evidence = build_evidence(
            plan, started_at=started, result_count=returned,
            total_matching=total_matched, truncated=truncated,
            rows_fetched=returned, rows_returned=returned, http_requests=1,
        )
        return QueryResult(
            dataset_id=dataset_id,
            features=features,
            total_count=returned,
            total_matching=total_matched,
            returned_count=returned,
            truncated=truncated,
            has_more=truncated,
            result_mode="features",
            execution_time_seconds=round(time.monotonic() - started, 4),
            schema_info={"returned": returned, "wfs_version": self.version},
            metadata={
                "exec_time_ms": round((time.monotonic() - started) * 1000, 2),
                "pushdown_bbox": plan.pushed_spatial,
                "pushdown_filter": bool(plan.pushed_filters),
                "offset_pushed": offset_pushed,
                "srs_negotiated": srs_name,
                "query_plan": plan.model_dump(),
                "query_evidence": evidence.model_dump(),
                "is_demo": False,
            },
        )

    def _build_get_feature_xml(
        self, dataset_id: str, limit: int, offset: int, filter_xml: str
    ) -> str:
        """模板化 GetFeature POST 体（dataset_id 经实体转义；无用户值拼接）。"""
        from xml.sax.saxutils import escape as _esc

        wfs_ns = (
            "http://www.opengis.net/wfs/2.0"
            if self.version.startswith("2.")
            else "http://www.opengis.net/wfs"
        )
        type_attr = "typeNames" if self.version.startswith("2.") else "typeName"
        count_attr = "count" if self.version.startswith("2.") else "maxFeatures"
        start_attr = "startIndex"
        parts = [
            f'<wfs:GetFeature service="WFS" version="{_esc(self.version)}" '
            f'xmlns:wfs="{wfs_ns}" xmlns:gml="http://www.opengis.net/gml" '
            f'{count_attr}="{int(limit)}"'
        ]
        if offset:
            parts.append(f' {start_attr}="{int(offset)}"')
        parts.append(">")
        parts.append(
            f'<wfs:Query {type_attr}="{_esc(dataset_id)}" srsName="{_URN_CRS84}">'
            f"{filter_xml}</wfs:Query></wfs:GetFeature>"
        )
        return "".join(parts)

    def health(self) -> DataFabricHealth:
        start_time = time.time()
        if not self.url:
            return DataFabricHealth(status="unreachable", message="WFS URL missing")
        try:
            ok = self.probe()
            latency = round((time.time() - start_time) * 1000, 2)
            if ok:
                return DataFabricHealth(
                    status="healthy",
                    message="WFS service online and responsive",
                    latency_ms=latency,
                )
            return DataFabricHealth(
                status="unreachable",
                message="WFS probe returned non-200 status",
                latency_ms=latency,
            )
        except Exception:
            latency = round((time.time() - start_time) * 1000, 2)
            return DataFabricHealth(
                status="unreachable",
                message="WFS health check failed",
                latency_ms=latency,
            )
