"""Provider Capability Probing V7（ADR-0119 W3）：探测 → 作用域缓存 → 诚实披露。

职责（Epic 03 Must-have B）：
- 组合各源类型的**既有探测点**（OGC conformance / ArcGIS maxRecordCount /
  STAC filter extension conformance），产出探测后 ``AdapterCapabilitiesV2``；
- 探测结果进 **scoped 缓存**（scope_key + profile_id + profile content
  revision，R-M1），TTL/容量双界；profile 变更 → revision 变化 → 自然失配；
- rate-limit **被动观测**：探测响应头 ``Retry-After`` / ``X-RateLimit-*`` /
  429 → ``RateLimitHints``（诚实：拿不到 = None，绝不猜）；
- 探测代价显式记账（``ProbeCost``：requests/bytes/latency）—— 性能成功
  不能只用 wall-clock（Epic §16）。

诚实红线：探测失败回落静态默认矩阵（``caps_basis="default"``），绝不编造
能力；全部探测请求走 adapter 自带的 SSRF-safe session 与 bounded_get。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.services.data_fabric.query.capabilities import get_capabilities

logger = logging.getLogger(__name__)


class ProbeCost(BaseModel):
    """一轮探测的代价（结构性计数，非 wall-clock 门）。"""

    requests: int = 0
    bytes_downloaded: int = 0
    latency_ms: int = 0


class RateLimitHints(BaseModel):
    """从响应头被动观测的限流提示（全部可未知）。"""

    requests_per_window: Optional[int] = Field(default=None, ge=1)
    window_s: Optional[float] = Field(default=None, gt=0)
    #: header | observed_429 —— 来源诚实披露。
    source: str = "header"
    retry_after_s: Optional[float] = Field(default=None, ge=0)


class ProviderCapabilitiesRecord(BaseModel):
    """一条探测后的能力记录（缓存值；不含 secret）。"""

    profile_id: str
    scope_key: str
    profile_revision: str
    caps: Any  # AdapterCapabilitiesV2（避免反向依赖 models 的 extra=forbid 校验差异）
    #: default | probed | stale（stale = TTL 过后仍被读到的降级披露）。
    caps_basis: str = "probed"
    rate_limit: Optional[RateLimitHints] = None
    probed_at: float = Field(default_factory=time.time)
    expires_at: float = 0.0
    probe_cost: ProbeCost = Field(default_factory=ProbeCost)

    def is_expired(self, *, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at


# ── 探测原语（per source type；每个只做有界请求）────────────────────────


def _rate_limit_from_headers(headers: Any, status_code: int = 200) -> Optional[RateLimitHints]:
    rl = headers.get("X-RateLimit-Limit") or headers.get("X-Rate-Limit-Limit")
    window = headers.get("X-RateLimit-Window") or headers.get("X-Rate-Limit-Reset")
    retry_after = headers.get("Retry-After")
    if rl is None and retry_after is None and status_code != 429:
        return None
    hints = RateLimitHints(source="header")
    try:
        if rl is not None:
            hints.requests_per_window = int(str(rl).strip())
        if window is not None:
            val = str(window).strip()
            hints.window_s = float(val) if "." in val else float(int(val))
        if retry_after is not None:
            hints.retry_after_s = float(str(retry_after).strip())
    except (TypeError, ValueError):
        pass
    return hints


def _probe_arcgis(adapter: Any) -> Dict[str, Any]:
    """ArcGIS：``capabilities_v2()`` 已探测 maxRecordCount/supportsPagination
    （V6 R1-M8 遗产）。返回 override dict（仅与默认矩阵不同的字段）。"""
    default = get_capabilities("arcgis")
    probed = adapter.capabilities_v2()
    overrides = {}
    for k in default.model_dump():
        if getattr(probed, k) != getattr(default, k):
            overrides[k] = getattr(probed, k)
    return overrides


def _probe_ogc_api(adapter: Any, cost: ProbeCost) -> Dict[str, Any]:
    """OGC API Features：conformance 声明 → CQL2-Text/JSON 升级。

    adapter._get_conformance() 自带 60s 缓存；本轮探测后 conformance 的
    CQL2 家族映射到 V7 ``filter_encoding`` 通道（JSON 优先：结构化无注入面）。
    """
    default = get_capabilities("ogc_api")
    probed = adapter.capabilities_v2()
    overrides: Dict[str, Any] = {}
    for k in default.model_dump():
        if getattr(probed, k) != getattr(default, k):
            overrides[k] = getattr(probed, k)
    if probed.filter_pushdown:
        try:
            classes = adapter._get_conformance() or []
            cost.requests += 1
            has_text = any("cql2-text" in c for c in classes)
            has_json = any("cql2-json" in c for c in classes)
            if has_json:
                overrides["filter_encoding"] = "cql2-json"
            elif has_text:
                overrides["filter_encoding"] = "cql2-text"
        except Exception as exc:  # noqa: BLE001 - 探测失败回落默认
            logger.debug("[probing] ogc conformance unavailable: %s", exc)
            overrides.setdefault("filter_encoding", "cql2-text")
    return overrides


def _probe_stac(adapter: Any, cost: ProbeCost) -> Dict[str, Any]:
    """STAC：filter extension conformance（/conformance）→ cql2-text 升级。

    STAC filter 扩展 conformance URI 形如
    ``https://api.stacspec.org/v1.0.0-rc.3/item-search#filter``；仅当声明
    时才升级（V7 前 STAC filter 下推恒关 —— 诚实默认保持）。
    """
    overrides: Dict[str, Any] = {}
    url = (getattr(adapter, "url", "") or "").rstrip("/")
    if not url:
        return overrides
    session = getattr(adapter, "session", None)
    if session is None:
        return overrides
    try:
        from app.services.data_fabric.security import bounded_get

        started = time.monotonic()
        body = bounded_get(session, url + "/conformance", timeout=5, max_bytes=1 << 20)
        cost.requests += 1
        cost.latency_ms += int((time.monotonic() - started) * 1000)
        cost.bytes_downloaded += len(body)
        import json as _json

        data = _json.loads(body.decode("utf-8", errors="strict"))
        conforms = [str(c) for c in (data.get("conformsTo") or [])]
        if any(c.endswith("#filter") and "stacspec.org" in c for c in conforms):
            overrides["filter_pushdown"] = True
            overrides["filter_encoding"] = "cql2-text"
    except Exception as exc:  # noqa: BLE001 - 探测失败回落默认
        logger.debug("[probing] stac conformance unavailable: %s", exc)
    return overrides


#: source_type 别名规范化（V8 导出：runtime 的探测门禁/能力 diff 与探测
#: 原语必须使用同一规范化 —— 否则别名形态源被错误跳过探测或 diff 错基线）。
_CANONICAL_SOURCE_TYPES = {
    "postgres": "postgis",
    "postgresql": "postgis",
    "ogc_api_features": "ogc_api",
    "ogc": "ogc_api",
    "ogcapi": "ogc_api",
    "wfs1": "wfs",
    "wfs2": "wfs",
    "wmts": "wms",
    "wms_wmts": "wms",
    "arcgis_rest": "arcgis",
    "featureserver": "arcgis",
    "mapserver": "arcgis",
    "parquet": "geoparquet",
    "fgb": "flatgeobuf",
    "minio": "s3",
    "object_storage": "s3",
    "mock": "generic",
    "sample": "generic",
}


def canonical_source_type(source_type: str) -> str:
    return _CANONICAL_SOURCE_TYPES.get(
        str(source_type or "").strip().lower(), str(source_type or "").strip().lower()
    )


def _overrides_for(source_type: str, adapter: Any, cost: ProbeCost) -> Dict[str, Any]:
    canonical = canonical_source_type(source_type)
    if canonical == "ogc_api":
        return _probe_ogc_api(adapter, cost)
    if canonical == "stac":
        return _probe_stac(adapter, cost)
    if canonical == "arcgis":
        return _probe_arcgis(adapter)
    return {}


# ── 探测服务（scoped 缓存）───────────────────────────────────────────────


class CapabilityProbeService:
    """探测 + 作用域缓存（TTL/容量双界；profile revision 失配自然失效）。"""

    def __init__(
        self,
        *,
        ttl_s: Optional[float] = None,
        max_entries: int = 1024,
    ):
        self._ttl = float(
            ttl_s if ttl_s is not None else _setting("DATA_FABRIC_V7_PROBE_TTL_S", 300.0)
        )
        self._max = int(max_entries)
        self._entries: "OrderedDict[tuple, ProviderCapabilitiesRecord]" = OrderedDict()
        self._lock = threading.Lock()

    def probe(
        self,
        adapter: Any,
        profile: Any,
        scope_key: str,
        profile_revision: str,
        *,
        force: bool = False,
    ) -> ProviderCapabilitiesRecord:
        """返回探测后能力（缓存优先；``force`` 绕过 TTL）。"""
        key = (scope_key, str(profile.id), profile_revision)
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                if not force and not entry.is_expired(now=now):
                    self._entries.move_to_end(key)
                    return entry
                self._entries.pop(key, None)
        # 锁外探测（网络）。
        cost = ProbeCost()
        overrides: Dict[str, Any] = {}
        basis = "probed"
        try:
            overrides = _overrides_for(str(profile.source_type or ""), adapter, cost)
        except Exception as exc:  # noqa: BLE001 - 探测失败回落默认
            logger.warning("[probing] probe failed for %s: %s", profile.id, exc)
            basis = "default"
            overrides = {}
        caps = get_capabilities(str(profile.source_type or ""), overrides)
        record = ProviderCapabilitiesRecord(
            profile_id=str(profile.id),
            scope_key=scope_key,
            profile_revision=profile_revision,
            caps=caps,
            caps_basis=basis,
            # 当前探测原语（bounded_get）不透出响应头 —— rate_limit 诚实为
            # None；捕获点接入（如 adapter 层透出 last_response_headers）后
            # 用 _rate_limit_from_headers 填充。
            rate_limit=None,
            probed_at=now,
            expires_at=now + self._ttl,
            probe_cost=cost,
        )
        with self._lock:
            self._entries[key] = record
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        return record

    def get(
        self, profile_id: str, scope_key: str, profile_revision: str
    ) -> Optional[ProviderCapabilitiesRecord]:
        """只读缓存（EXPLAIN caps_basis 披露用；过期条目降级标注 stale）。"""
        key = (scope_key, str(profile_id), profile_revision)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                entry.caps_basis = "stale"
            return entry

    def invalidate(self, profile_id: Optional[str] = None) -> int:
        removed = 0
        with self._lock:
            for key in list(self._entries.keys()):
                if profile_id is None or key[1] == str(profile_id):
                    self._entries.pop(key, None)
                    removed += 1
        return removed

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"entries": len(self._entries), "max_entries": self._max}


def _setting(name: str, default):
    try:
        from app.core.config import settings

        return getattr(settings, name, default)
    except Exception:  # noqa: BLE001
        return default


#: 进程级探测服务单例。
_service: Optional[CapabilityProbeService] = None
_service_lock = threading.Lock()


def get_capability_probe_service() -> CapabilityProbeService:
    global _service
    with _service_lock:
        if _service is None:
            _service = CapabilityProbeService()
        return _service


def reset_capability_probe_service() -> None:
    global _service
    with _service_lock:
        _service = None


__all__ = [
    "CapabilityProbeService",
    "canonical_source_type",
    "ProbeCost",
    "ProviderCapabilitiesRecord",
    "RateLimitHints",
    "get_capability_probe_service",
    "reset_capability_probe_service",
    "_rate_limit_from_headers",
]
