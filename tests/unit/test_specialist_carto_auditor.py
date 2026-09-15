"""agent-swarm/05 专家包单测 —— Cartographer 制图专家与 CriticAuditor 审计裁判。

对应规格 ``docs/dev/carto-auditor-spec.md`` §8 测试矩阵（T1–T22）与
ADR-0189。分组：

- 制图（T1–T9）：分类裁决（Jenks/head_tail）、色彩梯度、categorical、
  版面必配、规范化、契约边界、RBAC、心跳熔断；
- 审计（T10–T16）：审计单形状、四条一票否决红线、fail-closed、只读
  不变量、工具面两两不相交；
- 对抗收敛（T17–T19）：duo session ≤2 轮收敛、不可修复诚实升级、
  Governor 并发纪律；
- 总控挂接（T20–T22）：registry 注册、InProcessSpecialistRuntime 路由
  与 decomposer 相位、delegate 提示词边界。
"""
from __future__ import annotations

import asyncio
import copy
from typing import Any, Dict, List

import pytest

from app.lib.cartography.palettes import min_adjacent_delta_e
from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.services.agent_swarm.contracts import (
    SERIALIZATION_BUDGET_BYTES,
    DeliveryAuditReport,
    MapSpecDeliveryRef,
)
from app.services.agent_swarm.base import (
    SpecialistTimeoutError,
    SpecialistToolDeniedError,
)
from app.services.agent_swarm.delegation_contracts import (
    SpecialistAssignment,
    SwarmSpecialistRole,
    SwarmTaskDescriptor,
    WorldStateProjection,
)
from app.services.agent_swarm.duo_session import (
    CartoAuditDuoSession,
    InProcessSpecialistRuntime,
)
from app.services.agent_swarm.orchestrator import (
    HeuristicSpatialDecomposer,
    validate_swarm_graph,
)
from app.services.agent_swarm.registry import (
    SPECIALIST_REGISTRY,
    ensure_subagent_roles_registered,
    get_specialist,
)
from app.services.agent_swarm.specialists.auditor import CriticAuditorAgent
from app.services.agent_swarm.specialists.cartographer import CartographerAgent
from app.services.agent_swarm.specialists.ledger import ArtifactLedger

# ─────────────────────────── 通用假件 ───────────────────────────


class FakeClock:
    """手动推时钟（心跳/熔断确定性测试标准件，同 test_specialist_data_compute）。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class StubToolRegistry:
    """只认白名单内工具的注册表桩（构造期存在性校验 / dispatch 边界用）。"""

    def __init__(self, names) -> None:
        self._names = set(names)
        self.dispatched: List[str] = []

    def descriptor(self, name: str):
        return {"name": name} if name in self._names else None

    async def dispatch(self, tool_name: str, payload: Dict[str, Any], **_: Any):
        self.dispatched.append(tool_name)
        return {"success": True}


class RecordingDispatcher:
    """delegate 路径记录器（同 test_specialist_data_compute 模式）。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def run(self, *, task: str, **kwargs: Any):
        self.calls.append({"task": task, **kwargs})
        return {"success": True, "summary": "ok"}


class SabotageOnceCartographer(CartographerAgent):
    """首次 compose 后悄悄关掉图例 —— 制造 V1 缺陷供对抗回路修复。"""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.sabotaged = False

    def compose(self, request: Dict[str, Any]) -> MapSpecDeliveryRef:
        ref = super().compose(request)
        if not self.sabotaged:
            self.sabotaged = True
            payload = self._ledger.get(ref.ref_id)
            payload["layout"]["legend"]["visible"] = False
            ref.legend_visible = False
            ref.mapspec_fingerprint = cartographic_fingerprint(payload)
            self._ledger.put(ref.ref_id, payload)
        return ref


class EmptyFeaturesCartographer(CartographerAgent):
    """每次 compose 都产出空要素图层 —— 不可修复缺陷（V4 数据空洞）。"""

    def compose(self, request: Dict[str, Any]) -> MapSpecDeliveryRef:
        request = dict(request)
        request["geojson"] = {"type": "FeatureCollection", "features": []}
        return super().compose(request)


# ─────────────────────────── fixture 工厂 ───────────────────────────

_DISTRICTS = [
    "锦江区", "青羊区", "金牛区", "武侯区", "成华区",
    "龙泉驿区", "青白江区", "新都区", "温江区", "双流区",
]

#: 适度偏态（skew≈0.33，非近均匀）→ choose_classification 中等偏态分支
#: → natural_breaks(Jenks)。
_MODERATE_VALUES = [10, 14, 18, 25, 30, 40, 52, 68, 90, 120]
#: 重尾计数形态（mean ≫ median）→ head_tail。
_HEAVY_VALUES = [1, 1, 2, 2, 3, 3, 4, 5, 6, 400]


def _square(lng: float, lat: float) -> Dict[str, Any]:
    d = 0.02
    return [[
        [lng, lat], [lng + d, lat], [lng + d, lat + d], [lng, lat + d],
        [lng, lat],
    ]]


def _district_geojson(values: List[float], field: str = "schools") -> Dict[str, Any]:
    features = []
    for i, name in enumerate(_DISTRICTS):
        features.append({
            "type": "Feature",
            "properties": {
                "district": name,
                field: values[i % len(values)],
                "zone_type": ["urban", "suburban", "rural"][i % 3],
            },
            "geometry": {"type": "Polygon", "coordinates": _square(104.0 + i * 0.05, 30.5 + i * 0.05)},
        })
    return {"type": "FeatureCollection", "features": features}


def _numeric_request(values: List[float], **overrides: Any) -> Dict[str, Any]:
    req = {
        "geojson": _district_geojson(values),
        "field": "schools",
        "title": "成都各区学校数量分布",
        "purpose": "screen_16_9",
        "source_id": "districts",
    }
    req.update(overrides)
    return req


def _map_chapter(**overrides: Any) -> Dict[str, Any]:
    """仅含 map 需求的最小 chapter（需求派生面）。"""
    ch = {
        "query": "成都各区学校数量分布专题图",
        "intent": {
            "query": "成都各区学校数量分布专题图",
            "task": "distribution_overview",
            "scope": {"name": "成都", "level": "city"},
            "output_intents": ["map"],
        },
    }
    ch.update(overrides)
    return ch


def _chapter_with_export(**overrides: Any) -> Dict[str, Any]:
    ch = _map_chapter()
    ch["intent"] = dict(ch["intent"], export_intents=["png"])
    ch.update(overrides)
    return ch


def _ready_product(**overrides: Any) -> Dict[str, Any]:
    block = {
        "product_verdict": {"verdict": "READY"},
        "final_map_status": "verified",
        "render_status": "verified",
        "checked_revision": "7",
    }
    block.update(overrides)
    return block


@pytest.fixture()
def ledger() -> ArtifactLedger:
    return ArtifactLedger()


@pytest.fixture()
def cartographer(ledger: ArtifactLedger) -> CartographerAgent:
    return CartographerAgent(ledger=ledger)


@pytest.fixture()
def auditor() -> CriticAuditorAgent:
    return CriticAuditorAgent()


def _payload(ledger: ArtifactLedger, ref: MapSpecDeliveryRef) -> Dict[str, Any]:
    return ledger.get(ref.ref_id)


def _thematic_layer(mapspec: Dict[str, Any]) -> Dict[str, Any]:
    return next(
        layer for layer in mapspec["layers"]
        if layer.get("legend_spec")
    )


# ═════════════════════ 制图（T1–T9）═════════════════════


class TestCartographerComposition:
    def test_t1_moderate_skew_chooses_jenks_natural_breaks(self, cartographer, ledger):
        """T1：适度偏态多字段 GeoJSON → natural_breaks(Jenks) 胜出，k 夹 [3,7]。"""
        ref = cartographer.compose(_numeric_request(_MODERATE_VALUES))
        assert ref.classification["field"] == "schools"
        assert ref.classification["method"] == "natural_breaks"
        assert 3 <= ref.classification["k"] <= 7
        payload = _payload(ledger, ref)
        spec = _thematic_layer(payload)["legend_spec"]
        assert spec["type"] == "graduated"
        assert spec["method"] == "natural_breaks"
        breaks = spec["breaks"]
        assert breaks == sorted(breaks)
        assert len(breaks) - 1 == spec["k"]

    def test_t2_heavy_tail_chooses_head_tail_with_rejections(self, cartographer):
        """T2：重尾计数 → head_tail；被推翻的旧默认必须留痕。"""
        ref = cartographer.compose(_numeric_request(_HEAVY_VALUES))
        assert ref.classification["method"] == "head_tail"
        assert ref.classification.get("rejected_methods"), "落选分类法必须留痕"

    def test_t3_palette_colors_separable_gradient(self, cartographer, ledger):
        """T3：色彩对比梯度 —— palette_colors 数与类别数一致且相邻色可分辨。"""
        ref = cartographer.compose(_numeric_request(_MODERATE_VALUES))
        spec = _thematic_layer(_payload(ledger, ref))["legend_spec"]
        colors = spec["palette_colors"]
        assert len(colors) == spec["k"]
        assert min_adjacent_delta_e(colors) >= 5.0

    def test_t4_categorical_field_builds_categorical_legend(self, cartographer, ledger):
        """T4：类别字段 → categorical 图例 + qualitative 色板，颜色互异。"""
        req = _numeric_request(_MODERATE_VALUES, field="zone_type")
        ref = cartographer.compose(req)
        spec = _thematic_layer(_payload(ledger, ref))["legend_spec"]
        assert spec["type"] == "categorical"
        keys = [c["key"] for c in spec["categories"]]
        assert keys == ["rural", "suburban", "urban"]
        assert len({c["color"] for c in spec["categories"]}) == 3

    def test_t5_required_components_and_layout_selfheal(self, cartographer, ledger):
        """T5：必配组件基线全在场 + 图例开启 + 布局无未愈合抑制。"""
        ref = cartographer.compose(_numeric_request(_MODERATE_VALUES))
        payload = _payload(ledger, ref)
        types = {c["type"] for c in payload["layout"]["components"]}
        assert {"title", "scale_bar", "north_arrow", "attribution", "legend"} <= types
        assert payload["layout"]["legend"]["visible"] is True
        assert ref.warnings == [] or all("布局" not in w for w in ref.warnings)

    def test_t6_canonical_schema_and_fingerprint_stable(self, cartographer, ledger):
        """T6：产物过 canonicalize_mapspec；同输入两次 compose 指纹一致。"""
        from app.lib.cartography.mapspec_schema import canonicalize_mapspec

        ref = cartographer.compose(_numeric_request(_MODERATE_VALUES))
        payload = _payload(ledger, ref)
        canonical = canonicalize_mapspec(payload)
        assert canonical["version"]
        ref2 = CartographerAgent(ledger=ArtifactLedger()).compose(
            _numeric_request(_MODERATE_VALUES))
        assert ref.mapspec_fingerprint == ref2.mapspec_fingerprint
        assert ref.mapspec_fingerprint == cartographic_fingerprint(payload)

    def test_t7_delivery_ref_bounded_and_ledger_roundtrip(self, cartographer, ledger):
        """T7：交付券序列化 < 8KB（Zero Big Data in Context）；载荷可凭 ref 取回。"""
        ref = cartographer.compose(_numeric_request(_MODERATE_VALUES))
        assert ref.ref_id and ref.ref_id.startswith("ref:mapspec-")
        assert len(ref.to_json_bytes()) < SERIALIZATION_BUDGET_BYTES
        payload = ledger.get(ref.ref_id)
        assert payload["sources"]["districts"]["type"] == "geojson"

    def test_t9_heartbeat_deadline_enforced(self):
        """T9：墙钟熔断 —— FakeClock 推进超限 → SpecialistTimeoutError。"""
        clock = FakeClock()
        agent = CartographerAgent(clock=clock, deadline_s=100.0)
        agent.heartbeat("compose")
        clock.advance(200.0)
        with pytest.raises(SpecialistTimeoutError):
            agent.check_deadline()


class TestCartographerRBAC:
    def test_t8_tool_allowlist_enforced(self, ledger):
        """T8：白名单外工具 typed 拒绝；dispatch 边界返回 TOOL_NOT_ALLOWLISTED。"""
        registry = StubToolRegistry(CartographerAgent.TOOL_ALLOWLIST)
        carto = CartographerAgent(registry=registry, ledger=ledger)
        with pytest.raises(SpecialistToolDeniedError, match="query_dataset"):
            carto.authorize_tool("query_dataset")
        result = carto.guarded_registry().dispatch("query_dataset", {})
        assert result["code"] == "TOOL_NOT_ALLOWLISTED"
        assert registry.dispatched == []

    def test_t8b_rotten_allowlist_fails_closed(self):
        """T8b：白名单引用不存在工具 → 构造期 ValueError（防腐烂）。"""
        registry = StubToolRegistry({"create_thematic_map"})
        with pytest.raises(ValueError, match="cartographer"):
            CartographerAgent(registry=registry)


# ═════════════════════ 审计（T10–T16）═════════════════════


class TestCriticAuditor:
    def test_t10_clean_delivery_passes_with_full_goal_score(
        self, cartographer, auditor, ledger,
    ):
        """T10：完整交付 + READY 产物证据 → verdict=pass，goal_score=1.0。"""
        ref = cartographer.compose(_numeric_request(_MODERATE_VALUES))
        report = auditor.audit(
            ref, chapter=_map_chapter(), map_product=_ready_product(),
            ledger=ledger,
        )
        assert report.verdict == "pass"
        assert report.goal_score == 1.0
        assert report.goal_score_derivation
        assert report.vetoes == []
        assert report.uncovered_requirements == []

    def test_t11_missing_legend_vetoes_with_causal_chain(
        self, cartographer, auditor, ledger,
    ):
        """T11：故意缺图例 → V1 一票否决 + 不可抵赖因果链 + 修复建议。"""
        ref = cartographer.compose(_numeric_request(_MODERATE_VALUES))
        payload = _payload(ledger, ref)
        payload["layout"]["legend"]["visible"] = False
        broken = ref.model_copy(update={
            "legend_visible": False,
            "mapspec_fingerprint": cartographic_fingerprint(payload),
        })
        ledger.put(broken.ref_id, payload)
        report = auditor.audit(broken, ledger=ledger)
        assert report.verdict == "fail"
        veto_ids = {v["veto_id"] for v in report.vetoes}
        assert "V1" in veto_ids
        v1 = next(v for v in report.vetoes if v["veto_id"] == "V1")
        assert v1["rule_id"] == "carto.legend.completeness"
        assert v1["audited_fingerprint"] == broken.mapspec_fingerprint
        assert v1["suggested_fix"], "veto 必须携带可执行修复建议"
        assert report.improvement_notes

    def test_t12_empty_data_veto(self, ledger, auditor):
        """T12：零要素数据空洞 → V4 否决（对抗 false_pass 反例）。"""
        carto = EmptyFeaturesCartographer(ledger=ledger)
        ref = carto.compose(_numeric_request(_MODERATE_VALUES))
        report = auditor.audit(ref, ledger=ledger)
        assert report.verdict == "fail"
        assert "V4" in {v["veto_id"] for v in report.vetoes}

    def test_t13_metric_mismatch_uncovered_requirements(
        self, cartographer, auditor, ledger,
    ):
        """T13：要求导出 PNG 但无回执 → uncovered + V3 + goal_score 阈值不达。"""
        ref = cartographer.compose(_numeric_request(_MODERATE_VALUES))
        report = auditor.audit(
            ref, chapter=_chapter_with_export(), map_product=_ready_product(),
            ledger=ledger,
        )
        assert report.verdict == "fail"
        assert report.uncovered_requirements, "未满足的 required 需求必须列明"
        assert report.goal_score is not None and report.goal_score < 1.0
        assert "V3" in {v["veto_id"] for v in report.vetoes}
        assert report.success_threshold == 1.0

    def test_t14_fail_closed_never_pass_without_evidence(
        self, cartographer, auditor, ledger,
    ):
        """T14：审计对象不可达 / 证据不全 → 永不 pass（fail-closed）。"""
        ref = cartographer.compose(_numeric_request(_MODERATE_VALUES))
        orphan = ref.model_copy(update={"ref_id": "ref:mapspec-nonexistent"})
        report = auditor.audit(orphan, ledger=ledger)
        assert report.verdict == "fail"
        assert any(v["veto_id"] == "V0" for v in report.vetoes)

        # 证据面缺失（源 profile 被剥离）→ not_evaluated，同样不入 pass。
        payload = _payload(ledger, ref)
        payload["sources"]["districts"].pop("profile", None)
        ledger.put(ref.ref_id, payload)
        report2 = auditor.audit(ref, ledger=ledger)
        assert report2.verdict != "pass"

    def test_t15_audit_is_readonly_with_fingerprint_lock(
        self, cartographer, auditor, ledger,
    ):
        """T15：只读不变量 —— 审计前后载荷不变；指纹锁定被审代际。"""
        ref = cartographer.compose(_numeric_request(_MODERATE_VALUES))
        payload_before = copy.deepcopy(_payload(ledger, ref))
        report = auditor.audit(ref, ledger=ledger)
        assert _payload(ledger, ref) == payload_before
        assert report.audited_fingerprint == cartographic_fingerprint(payload_before)

    def test_t16_audit_report_shape_bounded(self, cartographer, auditor, ledger):
        """T16：审计单契约形状 —— bounded 模型、verdict 词表、review_status。"""
        ref = cartographer.compose(_numeric_request(_MODERATE_VALUES))
        report = auditor.audit(ref, ledger=ledger)
        assert isinstance(report, DeliveryAuditReport)
        assert report.verdict in ("pass", "fail", "not_evaluated")
        assert report.review_status in (
            "", "passed", "passed_with_warnings", "partial",
            "failed_repairable", "failed_unrepairable", "not_evaluated",
        )
        assert len(report.to_json_bytes()) < SERIALIZATION_BUDGET_BYTES


class TestAuditRBAC:
    def test_t16b_auditor_tool_surface_readonly_and_disjoint(self, ledger):
        """T16b：auditor 拒绝制图变更面；四专家工具面两两不相交。"""
        from app.services.agent_swarm.data_scout import DataScoutAgent
        from app.services.agent_swarm.geocompute import GeoComputeAgent

        registry = StubToolRegistry(CriticAuditorAgent.TOOL_ALLOWLIST)
        auditor = CriticAuditorAgent(registry=registry)
        with pytest.raises(SpecialistToolDeniedError, match="create_thematic_map"):
            auditor.authorize_tool("create_thematic_map")

        surfaces = [
            set(DataScoutAgent.TOOL_ALLOWLIST),
            set(GeoComputeAgent.TOOL_ALLOWLIST),
            set(CartographerAgent.TOOL_ALLOWLIST),
            set(CriticAuditorAgent.TOOL_ALLOWLIST),
        ]
        for i, a in enumerate(surfaces):
            for j, b in enumerate(surfaces):
                if i < j:
                    assert a.isdisjoint(b), f"专家 {i} 与 {j} 工具面相交"


# ═════════════════════ 对抗收敛（T17–T19）═════════════════════


class TestDuoSession:
    async def test_t17_adversarial_loop_converges_within_two_rounds(
        self, ledger,
    ):
        """T17：R0 缺图例 fail → revise 按建议修复 → R1 pass（≤2 轮）。"""
        session = CartoAuditDuoSession(
            cartographer=SabotageOnceCartographer(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        result = await session.run(
            _numeric_request(_MODERATE_VALUES),
            chapter=_map_chapter(),
            map_product=_ready_product(),
        )
        assert result.status == "converged"
        assert result.rounds_used <= CartoAuditDuoSession.MAX_REPAIR_ROUNDS
        assert result.final_report.verdict == "pass"
        assert result.final_delivery.mapspec_fingerprint == (
            cartographic_fingerprint(ledger.get(result.final_delivery.ref_id)))
        assert len(result.rounds) == result.rounds_used + 1

    async def test_t18_unrecoverable_fails_escalated_honestly(self, ledger):
        """T18：不可修复缺陷 → 超限 failed_escalated，全程证据上交，绝不 pass。"""
        session = CartoAuditDuoSession(
            cartographer=EmptyFeaturesCartographer(ledger=ledger),
            auditor=CriticAuditorAgent(),
            ledger=ledger,
        )
        result = await session.run(_numeric_request(_MODERATE_VALUES))
        assert result.status == "failed_escalated"
        assert result.rounds_used == CartoAuditDuoSession.MAX_REPAIR_ROUNDS
        assert result.final_report.verdict == "fail"
        assert len(result.receipts) >= (CartoAuditDuoSession.MAX_REPAIR_ROUNDS + 1) * 2

    async def test_t19_governor_discipline_and_receipts_normalized(self, ledger):
        """T19：并发双会话共享 Governor —— 在飞 ≤3、结束零滞留、券全部合法。"""
        from app.services.agent_swarm.orchestrator import (
            get_swarm_concurrency_governor,
        )

        governor = get_swarm_concurrency_governor()

        async def one() -> CartoAuditDuoSession:
            ledger = ArtifactLedger()
            session = CartoAuditDuoSession(
                cartographer=CartographerAgent(ledger=ledger),
                auditor=CriticAuditorAgent(),
                ledger=ledger,
                governor=governor,
            )
            await session.run(
                _numeric_request(_MODERATE_VALUES),
                chapter=_map_chapter(),
                map_product=_ready_product(),
            )
            return session

        sessions = await asyncio.gather(one(), one(), one(), one())
        for session in sessions:
            assert session.result.status == "converged"
            for receipt in session.result.receipts:
                assert receipt.status in ("succeeded", "degraded", "failed")
                assert receipt.assignment_id
        assert governor.snapshot()["active_count"] == 0


# ═════════════════════ 总控挂接（T20–T22）═════════════════════


class TestSwarmWiring:
    def test_t20_registry_registration_and_roles(self, ledger):
        """T20：两专家入注册表；角色档幂等注册；未知名 fail-closed。"""
        ensure_subagent_roles_registered()
        ensure_subagent_roles_registered()  # 幂等
        assert "cartographer" in SPECIALIST_REGISTRY
        assert "critic_auditor" in SPECIALIST_REGISTRY
        assert isinstance(get_specialist("cartographer", ledger=ledger),
                          CartographerAgent)
        assert isinstance(get_specialist("critic_auditor"), CriticAuditorAgent)
        from app.services.subagent_roles import get_subagent_role

        carto_role = get_subagent_role("cartography_specialist")
        audit_role = get_subagent_role("audit_judge")
        assert audit_role.allow_mutation is False
        assert audit_role.failure_behavior == "fail_closed"
        assert carto_role.name == "cartography_specialist"
        with pytest.raises(ValueError, match="未知专家"):
            get_specialist("no_such_specialist")

    async def test_t21_inprocess_runtime_routes_and_decomposer_phases(
        self, ledger,
    ):
        """T21：capability 路由产出合法券；decomposer 相位含制图/审计且过图校验。"""
        carto = CartographerAgent(ledger=ledger)
        auditor = CriticAuditorAgent()
        runtime = InProcessSpecialistRuntime(
            cartographer=carto, auditor=auditor, ledger=ledger,
        )
        runtime.register_request(
            "swarm.cartography.compose", _numeric_request(_MODERATE_VALUES))
        projection = WorldStateProjection(
            session_id="sess-t21",
            goal_summary="成都各区学校数量分布专题图",
        )
        compose_task = SwarmTaskDescriptor(
            task_id="swarm.cartography.compose",
            goal="按制图规范完成专题图排版与出图",
            role=SwarmSpecialistRole.CARTOGRAPHY_SPECIALIST,
            capability="swarm.cartography.compose",
            expected_outputs=("thematic_map",),
        )
        assignment = SpecialistAssignment(
            assignment_id="a-t21-1",
            task=compose_task,
            projection=projection,
            upstream_receipts=[],
            issued_at=0.0,
        )
        receipt = await runtime.execute(assignment)
        assert receipt.status.value == "succeeded"
        assert receipt.produced_refs[0].startswith("ref:mapspec-")

        audit_task = SwarmTaskDescriptor(
            task_id="swarm.audit.judge",
            goal="对产物与过程证据执行质量审计与合规校核",
            role=SwarmSpecialistRole.AUDIT_JUDGE,
            capability="swarm.audit.judge",
            depends_on=("swarm.cartography.compose",),
            optional=True,
            expected_outputs=("audit_report",),
        )
        audit_assignment = SpecialistAssignment(
            assignment_id="a-t21-2",
            task=audit_task,
            projection=projection,
            upstream_receipts=[receipt],
            issued_at=0.0,
        )
        audit_receipt = await runtime.execute(audit_assignment)
        assert audit_receipt.status.value == "succeeded"
        assert audit_receipt.produced_refs[0].startswith("ref:audit-")

        tasks = HeuristicSpatialDecomposer().decompose(
            "制作成都各区学校数量分布专题图", projection,
        )
        task_ids = {t.task_id for t in tasks}
        assert {"swarm.cartography.compose", "swarm.audit.judge"} <= task_ids
        assert validate_swarm_graph(tasks)  # 无环、有界、machine 兼容

    async def test_t22_delegate_injects_role_and_prompt_boundary(self, ledger):
        """T22：LLM 委派路径 —— 专属提示词头 + 角色名注入派遣器。"""
        registry = StubToolRegistry(CartographerAgent.TOOL_ALLOWLIST)
        carto = CartographerAgent(registry=registry, ledger=ledger)
        dispatcher = RecordingDispatcher()
        await carto.delegate(dispatcher, task="画一张分布图")
        call = dispatcher.calls[0]
        assert call["role"] == "cartography_specialist"
        assert call["task"].startswith("[cartographer]")
        assert "视觉变量" in call["task"]
