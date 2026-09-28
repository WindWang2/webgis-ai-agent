"""DataScoutAgent 数据猎手领域方法（ADR-0188 D5）。

跨源检索 + 声明式回退链 + 轻量元数据探测。全部 seam 注入（source
registry / chain executor / adapter factory / clock），不打真实数据源、
不打 ADS fallback 引擎。诚实失败路径：源未注册 / 回退链全败 / 适配器
返回非 dict —— 均产出 ``DataScoutReport(ok=False)``，绝不虚构数据。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest

from app.services.agent_swarm.base import SpecialistTimeoutError
from app.services.agent_swarm.data_scout import (
    DataScoutAgent,
    _default_adapter_factory,
)
from app.services.data_fabric.contracts import FallbackDecision
from app.services.data_fabric.fallback import ChainResult
from app.services.data_fabric.source_registry import (
    SourceDefinition,
    SourceRegistryError,
)


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class StubRegistry:
    """source_registry 替身（get 未知源抛 SourceRegistryError）。"""

    def __init__(self, definitions: dict[str, SourceDefinition]) -> None:
        self._definitions = definitions
        self.requested: list[str] = []

    def get(self, source_id: str) -> SourceDefinition:
        self.requested.append(source_id)
        if source_id not in self._definitions:
            raise SourceRegistryError(
                Path("test"), f"source {source_id!r} not in registry"
            )
        return self._definitions[source_id]


class StubAdapter:
    """preview 级轻量探测适配器（脚本化 features / 异常 / 非 dict）。"""

    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.calls: list[tuple[str, int]] = []

    def preview(self, dataset_key: str, *, limit: int = 10) -> Any:
        self.calls.append((dataset_key, limit))
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


def _definition(source_id: str = "osm", **kw: Any) -> SourceDefinition:
    kw.setdefault("name", f"源 {source_id}")
    kw.setdefault("protocol", "local_file")
    return SourceDefinition(source_id=source_id, **kw)


def _chain_result(
    *,
    source_used: Optional[str] = "osm",
    features: Optional[list[dict]] = None,
    decisions: Optional[list[FallbackDecision]] = None,
    error: Optional[BaseException] = None,
    fact_bytes: int = 0,
) -> ChainResult:
    from app.services.data_fabric.contracts import AcquisitionFact

    return ChainResult(
        source_used=source_used,
        features=features or [],
        decisions=decisions or [],
        fact=AcquisitionFact(
            request_id="scout-test", dataset_key="osm/roads", bytes=fact_bytes
        ),
        error=error,
    )


def _agent(
    *,
    registry: Any = None,
    chain_executor: Any = None,
    adapter_factory: Any = None,
    clock: Any = None,
    **base_kwargs: Any,
) -> DataScoutAgent:
    return DataScoutAgent(
        source_registry=registry,
        chain_executor=chain_executor,
        adapter_factory=adapter_factory,
        clock=clock or FakeClock(),
        **base_kwargs,
    )


class TestScoutHappyPath:
    async def test_descriptor_from_probed_facts(self):
        definitions = {"osm": _definition(verified=True, license="ODbL")}
        registry = StubRegistry(definitions)
        features = [
            {"名称": "道路甲", "经度": 116.1, "纬度": 39.9},
            {"名称": "道路乙", "经度": 116.2, "纬度": 39.8},
        ]

        def _executor(source_id, runner, *, chain, request_id, dataset_key):
            return _chain_result(features=features, fact_bytes=2048)

        agent = _agent(
            registry=registry,
            chain_executor=_executor,
            adapter_factory=lambda d: StubAdapter({"features": features, "bytes": 999}),
        )
        report = agent.scout("osm/roads", limit=5)
        assert report.ok is True
        assert report.source_used == "osm"
        descriptor = report.descriptor
        assert descriptor is not None
        assert descriptor.id == "osm/roads"
        assert descriptor.source_id == "osm"
        assert descriptor.source_type == "local_file"
        assert descriptor.feature_count == 2
        assert [f["name"] for f in descriptor.fields] == ["名称", "经度", "纬度"]
        assert descriptor.quality_signals.declared_crs is None  # 未知即 None
        assert descriptor.quality_signals.verified is True
        assert descriptor.cost_hint.rows == 2
        # bytes 口径来自 AcquisitionFact（chain 事实源 2048），非 adapter
        # 载荷（999）—— 两者不同值以区分来源
        assert descriptor.cost_hint.bytes == 2048
        assert descriptor.cost_hint.local is True
        assert descriptor.metadata["license"] == "ODbL"

    def test_heterogeneous_field_alignment(self):
        aligned = DataScoutAgent._align_fields(
            [
                {"名称": "a", "lng": 1.0, "latitude": 2.0, "类型": "road"},
                {"LNG": 3.0, "Latitude": 4.0, "类别": "rail"},
            ]
        )
        assert aligned == {
            "名称": "name",
            "lng": "lon",
            "latitude": "lat",
            "类型": "category",
            "LNG": "lon",
            "Latitude": "lat",
            "类别": "category",
        }

    def test_align_fields_ignores_unknown_keys(self):
        aligned = DataScoutAgent._align_fields([{"zzz_custom": 1, "经度": 2.0}])
        assert aligned == {"经度": "lon"}

    def test_align_fields_skips_non_dict_features(self):
        assert DataScoutAgent._align_fields(["not-a-dict", 42]) == {}

    def test_probe_uses_smaller_limit(self):
        features = [{"经度": 1.0}]
        adapter = StubAdapter({"features": features})

        def _executor(source_id, runner, *, chain, request_id, dataset_key):
            feats, _meta = runner("osm")  # 真实链经 runner 走适配器探测
            return _chain_result(features=feats)

        scout = _agent(
            registry=StubRegistry({"osm": _definition()}),
            chain_executor=_executor,
            adapter_factory=lambda d: adapter,
        )
        report = scout.probe("osm/roads")
        assert report.ok is True
        assert report.descriptor is not None
        assert report.descriptor.feature_count == 1
        # probe 的 limit=5 口径钉在适配器调用上（scout 缺省 10）
        assert adapter.calls == [("osm/roads", 5)]


class TestScoutHonestFailures:
    def test_unregistered_primary_source_fails(self):
        agent = _agent(registry=StubRegistry({}))
        report = agent.scout("ghost/roads")
        assert report.ok is False
        assert report.error.startswith("源未注册:")
        assert report.descriptor is None

    def test_chain_exhausted_failure_disclosed(self):
        agent = _agent(
            registry=StubRegistry({"osm": _definition()}),
            chain_executor=lambda *a, **kw: _chain_result(
                source_used=None, error=RuntimeError("all sources down")
            ),
        )
        report = agent.scout("osm/roads")
        assert report.ok is False
        assert "回退链全部失败" in report.error
        assert "all sources down" in report.error

    def test_adapter_non_dict_payload_fails_chain(self):
        """runner 对非 dict 载荷 fail-loud（SourceRegistryError 穿透回退链）。"""
        captured: dict = {}

        def _executor(source_id, runner, *, chain, request_id, dataset_key):
            try:
                runner("osm")
            except SourceRegistryError as exc:
                captured["error"] = str(exc)
                return _chain_result(source_used=None, error=exc)
            raise AssertionError("非 dict 载荷必须抛错")

        agent = _agent(
            registry=StubRegistry({"osm": _definition()}),
            chain_executor=_executor,
            adapter_factory=lambda d: StubAdapter(["not", "a", "dict"]),
        )
        report = agent.scout("osm/roads")
        assert report.ok is False
        assert "non-dict payload" in captured["error"]

    def test_adapter_exception_folds_into_chain_failure(self):
        """真实 execute_fallback_chain 会吞适配器异常并折算链失败；桩同构。"""

        def _executor(source_id, runner, **kw):
            try:
                runner("osm")
            except ConnectionError as exc:
                return _chain_result(source_used=None, error=exc)
            raise AssertionError("适配器异常必须被折算")

        agent = _agent(
            registry=StubRegistry({"osm": _definition()}),
            chain_executor=_executor,
            adapter_factory=lambda d: StubAdapter(ConnectionError("endpoint down")),
        )
        report = agent.scout("osm/roads")
        assert report.ok is False
        assert "回退链全部失败" in report.error
        assert "endpoint down" in report.error

    def test_empty_features_descriptor_is_honest(self):
        agent = _agent(
            registry=StubRegistry({"osm": _definition()}),
            chain_executor=lambda *a, **kw: _chain_result(features=[]),
            adapter_factory=lambda d: StubAdapter({"features": [], "bytes": 0}),
        )
        report = agent.scout("osm/roads")
        assert report.ok is True
        descriptor = report.descriptor
        assert descriptor is not None
        assert descriptor.feature_count is None  # 零要素 → None（不虚构 0 行）
        assert descriptor.fields == []
        assert descriptor.cost_hint.rows is None
        assert descriptor.cost_hint.bytes is None

    def test_deadline_breach_inside_chain_runner_raises(self):
        """回退链执行途中墙钟超限：runner 内 check_deadline typed 熔断。"""
        clock = FakeClock()
        captured: dict = {}

        def _executor(source_id, runner, **kw):
            clock.advance(10.0)  # 链执行途中时钟推进（scout:chain 心跳之后）
            try:
                runner("osm")
            except SpecialistTimeoutError:
                captured["timeout"] = True
                raise
            raise AssertionError("runner 内应熔断")

        agent = _agent(
            registry=StubRegistry({"osm": _definition()}),
            chain_executor=_executor,
            adapter_factory=lambda d: StubAdapter({"features": []}),
            clock=clock,
            deadline_s=5.0,
        )
        with pytest.raises(SpecialistTimeoutError):
            agent.scout("osm/roads")
        assert captured["timeout"] is True


class TestCrsAlignmentNotes:
    """CRS 对齐披露：GCJ-02 声明 / 换源不可比。"""

    def test_gcj02_declaration_disclosed(self):
        definition = _definition(options={"known_issues": "gcj02_offset"})
        note = DataScoutAgent._crs_alignment_note(definition, [])
        assert note == "source declares gcj02 coordinates; offset handling required"

    def test_non_comparable_fallback_disclosed(self):
        definition = _definition()
        decision = FallbackDecision(
            trigger="timeout", from_source="osm", to_source="backup", reason="slow"
        )
        assert decision.comparable is False
        note = DataScoutAgent._crs_alignment_note(definition, [decision])
        assert note is not None and "non-comparable" in note

    def test_comparable_fallback_no_note(self):
        definition = _definition()
        decision = FallbackDecision(
            trigger="timeout",
            from_source="osm",
            to_source="backup",
            reason="slow",
            comparable=True,
        )
        assert DataScoutAgent._crs_alignment_note(definition, [decision]) is None

    def test_plain_source_no_note(self):
        assert DataScoutAgent._crs_alignment_note(_definition(), []) is None


class TestDefaultAdapterFactory:
    """协议 → 适配器：未接线协议拒绝瞎猜（fail-loud）。"""

    def test_local_file_protocol_builds_local_adapter(self):
        adapter = _default_adapter_factory(_definition(protocol="local_file"))
        assert type(adapter).__name__ == "LocalFileAdapter"

    def test_unwired_protocol_refuses_to_guess(self):
        with pytest.raises(SourceRegistryError, match="未接线协议"):
            _default_adapter_factory(_definition(protocol="carrier_pigeon"))

    def test_fabric_profile_shape(self):
        profile = _definition(source_id="osm", endpoint="https://x.test").fabric_profile()
        assert profile["id"] == "osm"
        assert profile["source_type"] == "local_file"
        assert profile["endpoint_url"] == "https://x.test"


class TestSpecialistIdentity:
    """专家身份面（提示词纪律即契约的一部分）。"""

    def test_name_and_role(self):
        agent = _agent()
        assert agent.name == "data_scout"
        assert agent.role_name == "data_scout"

    def test_allowlist_is_read_surface(self):
        allowlist = DataScoutAgent.TOOL_ALLOWLIST
        assert "connect_data_source" in allowlist
        assert "query_dataset" in allowlist
        # 与制图/审计面两两不相交（角色隔离）
        assert "create_thematic_map" not in allowlist
        assert "audit_spatial_quality" not in allowlist

    def test_prompt_forbids_full_data_pull(self):
        prompt = DataScoutAgent.SPECIALIST_PROMPT
        assert "绝不拉全量数据" in prompt
        assert "绝不伪造 EPSG:4326" in prompt

    def test_default_deadline_from_role(self):
        agent = _agent()
        assert agent._deadline_s == 240.0
