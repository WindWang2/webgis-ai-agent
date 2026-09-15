"""DataScoutAgent — 数据猎手（ADR-0188 D5）。

专职跨源检索、格式适配、CRS 自动对齐与**轻量**元数据探测（preview 级，
绝不拉全量数据）。检索失败自愈走 ADS 既有原语：

- ``source_registry_service`` 解析源声明（声明式回退链的事实源）；
- ``fallback.resolve_chain`` 展开声明式回退链；
- ``fallback.execute_fallback_chain`` 执行（逐跳重试 + 熔断短 路 + D3
  FallbackDecision / D4 AcquisitionFact 全程留痕；OPEN 源自动跳过）；
- 适配器经 ``SourceDefinition.fabric_profile()`` 构造（LocalFileAdapter /
  StatsApiAdapter 等，可注入桩）。

成功产出 D1 ``D1DatasetDescriptor``（只填已探测事实；CRS/bbox/行数未知
即 None，诚实默认）；全链失败 → ``DataScoutReport(ok=False)`` 诚实失败，
绝不虚构数据。GIS 实体对齐纪律（异构字段映射、GCJ-02 纠偏声明）由
``SPECIALIST_PROMPT`` 承载给子代理 LLM；``aligned_fields`` 的确定性映射
只基于有证据的观察键。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.agent_swarm.base import BaseSpecialistAgent
from app.services.agent_swarm.contracts import DataScoutReport
from app.services.data_fabric import fallback as _fb
from app.services.data_fabric.contracts import (
    CostHint,
    D1DatasetDescriptor,
    QualitySignals,
)
from app.services.data_fabric.source_registry import (
    SourceDefinition,
    SourceRegistryError,
    source_registry_service,
)

#: 异构字段 → 标准字段词典（有证据才映射；键一律 strip+lower 后比对）。
_FIELD_ALIASES: Dict[str, str] = {
    "名称": "name",
    "名字": "name",
    "地址": "address",
    "经度": "lon",
    "纬度": "lat",
    "lng": "lon",
    "long": "lon",
    "longitude": "lon",
    "latitude": "lat",
    "x": "lon",
    "y": "lat",
    "人口": "population",
    "行政区划代码": "admin_code",
    "区划代码": "admin_code",
    "类别": "category",
    "类型": "category",
}

#: descriptor 元数据的字段列上限（有界）。
_MAX_FIELDS = 32


def _default_adapter_factory(definition: SourceDefinition) -> Any:
    """协议 → fabric 适配器（生产路径；测试注入桩）。"""
    from app.schemas.data_fabric_schema import ConnectionProfile

    profile = ConnectionProfile(**definition.fabric_profile())
    if definition.protocol == "local_file":
        from app.services.data_fabric.adapters.local_file_adapter import (
            LocalFileAdapter,
        )

        return LocalFileAdapter(profile)
    if definition.protocol == "stats_api":
        from app.services.data_fabric.adapters.stats_api_adapter import (
            StatsApiAdapter,
        )

        return StatsApiAdapter(profile)
    raise SourceRegistryError(
        Path("agent_swarm/data_scout"),
        f"DataScout 未接线协议 {definition.protocol!r}（source "
        f"{definition.source_id}）；refusing to guess an adapter",
    )


class DataScoutAgent(BaseSpecialistAgent):
    """数据猎手：跨源检索 + 声明式回退链 + 轻量元数据探测。"""

    name = "data_scout"
    role_name = "data_scout"

    TOOL_ALLOWLIST = frozenset({
        # ADS v1 取数面
        "connect_data_source",
        "inspect_data_source",
        "list_datasets",
        "search_datasets",
        "search_spatial_catalog",
        "describe_dataset",
        "profile_dataset",
        "query_dataset",
        "query_federated_chain",
        "query_federated_data",
        "plan_data_query",
        "materialize_dataset",
        "refresh_data_source",
        # 域检索面（OSM / POI / 政务）
        "query_local_osm",
        "query_osm_boundary",
        "query_osm_buildings",
        "query_osm_poi",
        "query_osm_roads",
        "search_and_extract_poi",
    })

    SPECIALIST_PROMPT = (
        "你是数据猎手（Data Scout），专职数据发现与清洗，不做空间计算、不改地图。"
        "纪律：(1) 检索失败时沿声明式回退链换源重试，绝不直接停滞；换源后必须"
        "披露 comparable 与降级说明；(2) 只做 preview 级轻量探测，绝不拉全量数据"
        "进上下文；(3) CRS 只填源声明或已验证值（GCJ-02 偏移必须显式声明"
        "known_issues=gcj02_offset），未知即 unknown，绝不伪造 EPSG:4326；"
        "(4) 异构字段映射到标准字段（name/lon/lat/population…），无证据不猜测；"
        "(5) 最终输出 D1DatasetDescriptor + FallbackDecision 溯源。"
    )

    def __init__(
        self,
        *,
        source_registry: Optional[Any] = None,
        chain_executor: Optional[Callable[..., Any]] = None,
        adapter_factory: Optional[Callable[[SourceDefinition], Any]] = None,
        **base_kwargs: Any,
    ) -> None:
        super().__init__(**base_kwargs)
        self._source_registry = source_registry or source_registry_service
        self._chain_executor = chain_executor or _fb.execute_fallback_chain
        self._adapter_factory = adapter_factory or _default_adapter_factory

    # ── 公共领域方法 ────────────────────────────────────────

    def scout(
        self,
        dataset_key: str,
        *,
        bbox: Optional[List[float]] = None,
        limit: int = 10,
    ) -> DataScoutReport:
        """沿声明式回退链探测 ``dataset_key``，产出 D1 descriptor 报告。

        ``dataset_key`` 形如 ``{source_id}/{dataset}``；首段为首选源。
        bbox 仅透传给支持空间下推的适配器（本层不做几何运算）。
        """
        self.heartbeat("scout:start")
        self.check_deadline()
        source_id = dataset_key.split("/", 1)[0]

        try:
            primary = self._get_definition(source_id)
        except SourceRegistryError as exc:
            return DataScoutReport(ok=False, error=f"源未注册: {exc}")

        chain = _fb.resolve_chain(source_id, service=self._source_registry)

        def _runner(sid: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
            self.check_deadline()
            definition = self._get_definition(sid)
            adapter = self._adapter_factory(definition)
            payload = adapter.preview(dataset_key, limit=limit) or {}
            if not isinstance(payload, dict):
                raise SourceRegistryError(
                    Path("agent_swarm/data_scout"),
                    f"adapter preview for {sid} returned non-dict payload",
                )
            features = payload.get("features") or payload.get("rows") or []
            meta = {
                "bytes": int(payload.get("bytes") or 0),
                "empty_result": not features,
            }
            return list(features), meta

        self.heartbeat("scout:chain")
        result = self._chain_executor(
            source_id,
            _runner,
            chain=chain,
            request_id=f"scout-{uuid.uuid4().hex[:12]}",
            dataset_key=dataset_key,
        )

        if not result.ok:
            self.heartbeat("scout:failed")
            reason = str(result.error) if result.error is not None else "unknown"
            return DataScoutReport(
                ok=False,
                error=f"回退链全部失败（primary={source_id}）: {reason}",
                decisions=list(result.decisions),
                fact=result.fact,
            )

        used = result.source_used or source_id
        try:
            used_def = self._get_definition(used)
        except SourceRegistryError:
            used_def = primary
        descriptor = self._build_descriptor(
            dataset_key, used_def, features=result.features, meta=result.fact,
        )
        self.heartbeat("scout:settle")
        return DataScoutReport(
            ok=True,
            descriptor=descriptor,
            source_used=used,
            decisions=list(result.decisions),
            fact=result.fact,
            aligned_fields=self._align_fields(result.features),
            crs_alignment=self._crs_alignment_note(used_def, result.decisions),
        )

    def probe(self, dataset_key: str, *, limit: int = 5) -> DataScoutReport:
        """纯元数据探测（更小 limit；语义与 scout 相同，只取更少样本）。"""
        return self.scout(dataset_key, limit=limit)

    # ── helpers ─────────────────────────────────────────────

    def _get_definition(self, source_id: str) -> SourceDefinition:
        if self._source_registry is None:
            raise SourceRegistryError("source registry 未绑定")
        return self._source_registry.get(source_id)

    def _build_descriptor(
        self,
        dataset_key: str,
        definition: SourceDefinition,
        *,
        features: List[Dict[str, Any]],
        meta: Any,
    ) -> D1DatasetDescriptor:
        """探测事实 → D1 descriptor（未知字段留 None/空，诚实默认）。"""
        observed: List[str] = []
        for feature in features[:64]:
            if isinstance(feature, dict):
                for key in feature:
                    if str(key) not in observed:
                        observed.append(str(key))
        declared_crs = definition.options.get("crs") or definition.options.get("srs")
        bytes_seen = int(getattr(meta, "bytes", 0) or 0)
        return D1DatasetDescriptor(
            id=dataset_key,
            source_id=definition.source_id,
            source_type=definition.protocol,
            title=definition.name,
            feature_count=len(features) or None,
            fields=[{"name": name} for name in observed[:_MAX_FIELDS]],
            quality_signals=QualitySignals(
                declared_crs=str(declared_crs) if declared_crs else None,
                verified=bool(definition.verified),
            ),
            cost_hint=CostHint(
                rows=len(features) or None,
                bytes=bytes_seen or None,
                local=definition.protocol == "local_file",
            ),
            metadata={
                "probe_limit_reached": len(features) >= 64,
                "fallback_hops": None,  # 由 report.decisions 承载（单一事实源）
                "license": definition.license,
            },
        )

    @staticmethod
    def _align_fields(features: List[Dict[str, Any]]) -> Dict[str, str]:
        """观察键 → 标准字段（词典映射；无证据不猜测）。"""
        aligned: Dict[str, str] = {}
        for feature in features[:64]:
            if not isinstance(feature, dict):
                continue
            for key in feature:
                text = str(key)
                canonical = _FIELD_ALIASES.get(text.strip().lower())
                if canonical and text not in aligned:
                    aligned[text] = canonical
        return aligned

    @staticmethod
    def _crs_alignment_note(
        definition: SourceDefinition,
        decisions: List[Any],
    ) -> Optional[str]:
        """CRS 对齐说明：GCJ-02 声明 / 换源可比性（诚实披露）。"""
        options_text = str(definition.options).lower()
        if "gcj02" in options_text:
            return "source declares gcj02 coordinates; offset handling required"
        if any(not decision.comparable for decision in decisions):
            return "fallback source may differ in granularity/coverage (non-comparable)"
        return None
