"""离线重放器契约测试（B3）：T1 证据级 / T2 变异级 / 确定性双跑。

fixture 契约与生产同源：
- cartography fixture = session 状态 dict（harness 真实重算语义评审）；
- 指纹收敛用 ``$cartographic_fingerprint`` 占位符（重放器替换为
  fixture spec 的规范指纹）；
- L5 视觉证据经 ``webgis_runtime_validate`` 结果的 ``visual_evidence``
  （ADR-0158 record-only 附加路径）。
"""
from __future__ import annotations

import pytest

from app.lib.harness.replay.replayer import (
    FINGERPRINT_PLACEHOLDER,
    OfflineReplayer,
    Scenario,
    ScenarioOp,
    TurnSpec,
    compare_exact,
    replay_mutations,
)

pytestmark = pytest.mark.cartography

#: 能通过确定性语义评审的最小 spec（零 error / 零 warning）。
_VALID_SPEC = {
    "schema_version": "1.2",
    "title": "公园分布",
    "view": {"center": [116.4, 39.9], "zoom": 10},
    "sources": {"src-parks": {
        "type": "geojson", "ref": "ref:geojson-parks",
        "profile": {
            "featureCount": 120,
            "bbox": [116.0, 39.0, 117.0, 40.0],
            "crs": "EPSG:4326", "crs_status": "explicit",
            "geometryTypes": ["Point"],
        },
    }},
    "layers": [{
        "id": "parks", "type": "circle", "source": "src-parks",
        "paint": {"circle-color": "#3182bd", "circle-radius": 5},
    }],
}

_PARKS_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "geometry": {"type": "Point",
                                         "coordinates": [116.4, 39.9]},
         "properties": {"name": "a"}},
        {"type": "Feature", "geometry": {"type": "Point",
                                         "coordinates": [116.5, 39.95]},
         "properties": {"name": "b"}},
    ],
}


def _upsert_op(call_id: str = "call-1", *, with_ref: bool = True) -> ScenarioOp:
    args: dict = {"layer": {"id": "parks", "type": "circle",
                            "source": "src-parks"}}
    if with_ref:
        args["source_ref"] = "ref:geojson-parks"
    return ScenarioOp(
        call_id=call_id,
        tool="webgis_layer_upsert",
        arguments=args,
        result={
            "success": True,
            "is_compiled": True,
            "mapspec_fingerprint": FINGERPRINT_PLACEHOLDER,
        },
    )


def _runtime_validate_op(call_id: str = "call-v") -> ScenarioOp:
    """record-only 视觉证据附加（ADR-0158 路径；无 error → L5 pass）。

    结果携带指纹 → harness 结构性归类为 mutation generation（生产诚实
    语义），故 canned 结果同时带 is_compiled/success（与生产收据同形）。
    """
    return ScenarioOp(
        call_id=call_id,
        tool="webgis_runtime_validate",
        arguments={"mapspec_fingerprint": FINGERPRINT_PLACEHOLDER},
        result={
            "success": True,
            "is_compiled": True,
            "mapspec_fingerprint": FINGERPRINT_PLACEHOLDER,
            "visual_evidence": {"source": "visual_judge",
                                "status": "evaluated", "error_count": 0},
            "report": {"mapLoaded": True},
        },
    )


def _cartography_ok(*, visual_judge: bool = False) -> dict:
    """L4 评审通过的完整 fixture：有效 spec + session 持有的前端观测。

    observation 的指纹/会话用占位符（重放器替换）；样式加载、图层在场
    与可见性对齐 spec —— 满足生产信任阶梯的全部收敛检查。
    """
    fixture = {
        "mapspec": _VALID_SPEC,
        "map_state": {"_cartographic_observation": {
            "source": "frontend_runtime",
            "session_id": "$session_id",
            "sequence": 1,
            "mapspec_fingerprint": "$cartographic_fingerprint",
            "style_loaded": True,
            "reconcile_error": "",
            "layers": [{"id": "parks", "_refId": "ref:geojson-parks",
                        "visible": True, "style_converged": True}],
            "viewport": {"center": [116.4, 39.9], "zoom": 10},
        }},
    }
    if visual_judge:
        fixture["visual_judge"] = True
    return fixture


def _upsert_source_ops() -> list:
    """T2 变异序列：init → source_profile → layer_upsert（真实 facade）。"""
    return [
        {"op": "init_project", "args": {"view": {"center": [116.4, 39.9],
                                                 "zoom": 10}}},
        {"op": "source_profile", "args": {"source_id": "src-parks",
                                          "geojson_data": _PARKS_GEOJSON}},
        {"op": "layer_upsert", "args": {"layer": {
            "id": "parks", "type": "circle", "source": "src-parks"}}},
    ]


class TestT1EvidenceReplay:
    @pytest.mark.asyncio
    async def test_gate_dimensions_evaluated_and_passed(self):
        scenario = Scenario(
            scenario_id="scn-t1-a", category="point_distribution",
            turns=[TurnSpec(
                user_input="把公园做成分布图",
                ops=[_upsert_op(), _runtime_validate_op()],
                refs={"ref:geojson-parks": {"type": "FeatureCollection"}},
                cartography=_cartography_ok(visual_judge=True),
            )],
        )
        result = await OfflineReplayer().replay_scenario(scenario)
        gate = result.turns[0].gate_result
        checks = gate["checks"]
        assert checks["MapSpecValidity"]["evaluated"] is True
        assert checks["MapSpecValidity"]["passed"] is True
        assert checks["CursorResolutionRate"]["evaluated"] is True
        assert checks["CursorResolutionRate"]["passed"] is True
        assert checks["CartographicQuality"]["passed"] is True
        assert result.turns[0].goal_satisfaction["status"] == "pass"
        assert result.ok is True
        assert result.levels_run == ["t1"]

    @pytest.mark.asyncio
    async def test_missing_ref_fails_cursor_check(self):
        """ref 解析失败 → CursorResolutionRate fail（缺证据≠成功，V2 策略）。"""
        scenario = Scenario(
            scenario_id="scn-t1-b", category="point_distribution",
            turns=[TurnSpec(ops=[_upsert_op()], refs={})],
        )
        result = await OfflineReplayer().replay_scenario(scenario)
        check = result.turns[0].gate_result["checks"]["CursorResolutionRate"]
        assert check["evaluated"] is True
        assert check["passed"] is False

    @pytest.mark.asyncio
    async def test_goal_satisfaction_fail_closed_without_visual(self):
        """无视觉证据 → goal not_evaluated（fail-closed，绝不伪造 pass）。"""
        scenario = Scenario(
            scenario_id="scn-t1-c", category="choropleth",
            turns=[TurnSpec(ops=[_upsert_op()],
                            cartography=_cartography_ok())],
        )
        result = await OfflineReplayer().replay_scenario(scenario)
        assert result.turns[0].goal_satisfaction["status"] == "not_evaluated"

    @pytest.mark.asyncio
    async def test_cartography_error_blocks_goal(self):
        """评审 fail 的 spec（引用缺失 source）→ CartographicQuality fail。"""
        broken_spec = {
            "schema_version": "1.2",
            "layers": [{"id": "parks", "type": "circle",
                        "source": "src-missing"}],
        }
        scenario = Scenario(
            scenario_id="scn-t1-e", category="dirty_data",
            turns=[TurnSpec(
                ops=[_upsert_op()],
                cartography={"mapspec": broken_spec},
                refs={"ref:geojson-parks": {}},
            )],
        )
        result = await OfflineReplayer().replay_scenario(scenario)
        checks = result.turns[0].gate_result["checks"]
        assert checks["CartographicQuality"]["passed"] is False
        assert checks["CartographicQuality"]["evaluated"] is True


class TestExpectExact:
    @pytest.mark.asyncio
    async def test_expect_mismatch_marks_scenario_red(self):
        scenario = Scenario(
            scenario_id="scn-x", category="dirty_data",
            turns=[TurnSpec(
                ops=[ScenarioOp(
                    call_id="c1", tool="webgis_layer_upsert",
                    arguments={},
                    result={"success": False, "is_error": True,
                            "error_msg": "invalid geometry"},
                    is_error=True, error_msg="invalid geometry",
                )],
                expect={"gate": {"checks": {"MapSpecValidity": {
                    "passed": True,  # 故意写错 —— 脏数据不该过
                }}}},
            )],
        )
        result = await OfflineReplayer().replay_scenario(scenario)
        assert result.ok is False
        assert result.turns[0].exact_diffs
        diff = result.turns[0].exact_diffs[0]
        assert diff["diff_class"] == "exact"
        assert diff["path"].startswith("gate.checks.MapSpecValidity")

    def test_compare_exact_walks_lists_and_leaves(self):
        assert compare_exact({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}) == []
        diffs = compare_exact({"a": [1, {"b": 2}]}, {"a": [1, {"b": 3}]})
        assert len(diffs) == 1
        assert diffs[0]["path"] == "a[1].b"


class TestT2MutationReplay:
    @pytest.mark.asyncio
    async def test_mutation_fingerprints_deterministic(self, tmp_path, monkeypatch):
        from app.services.mapspec import store as mapspec_store_module

        monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", tmp_path)
        mutations = _upsert_source_ops()
        outs1 = await replay_mutations("sess-m1", mutations)
        outs2 = await replay_mutations("sess-m2", mutations)
        assert all(o["success"] for o in outs1)
        assert all(o["success"] for o in outs2)
        assert outs1[2]["spec_fingerprint"] == outs2[2]["spec_fingerprint"]
        assert outs1[2]["spec_fingerprint"]
        assert outs1[2]["layer_count"] == 1

    @pytest.mark.asyncio
    async def test_unknown_mutation_op_is_honest(self, tmp_path, monkeypatch):
        from app.services.mapspec import store as mapspec_store_module

        monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", tmp_path)
        outs = await replay_mutations("sess-m3", [{"op": "nope", "args": {}}])
        assert outs[0].get("unknown_op") is True

    @pytest.mark.asyncio
    async def test_t2_level_and_replay_digest(self, tmp_path, monkeypatch):
        from app.services.mapspec import store as mapspec_store_module

        monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", tmp_path)
        scenario = Scenario(
            scenario_id="scn-t2", category="district_comparison",
            turns=[TurnSpec(
                mutations=_upsert_source_ops(),
                expect={"mutations": [
                    {"op": "init_project", "success": True},
                    {"op": "source_profile", "success": True},
                    {"op": "layer_upsert", "success": True},
                ]},
            )],
        )
        result = await OfflineReplayer().replay_scenario(scenario)
        assert "t2" in result.levels_run
        assert result.ok is True
        assert result.replay_digest


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_replay_twice_same_digest(self, tmp_path, monkeypatch):
        from app.services.mapspec import store as mapspec_store_module

        monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", tmp_path)
        scenario = Scenario(
            scenario_id="scn-det", category="heatmap",
            turns=[
                TurnSpec(ops=[_upsert_op("c1")],
                         refs={"ref:geojson-parks": {}},
                         cartography=_cartography_ok()),
                TurnSpec(ops=[_upsert_op("c2"), _runtime_validate_op("c3")],
                         cartography=_cartography_ok()),
            ],
        )
        r1 = await OfflineReplayer(seed=7).replay_scenario(scenario)
        r2 = await OfflineReplayer(seed=7).replay_scenario(scenario)
        assert r1.replay_digest == r2.replay_digest

        # 语义漂移（canned 结果翻转为失败）→ digest 变。
        drifted_op = _upsert_op("c2")
        drifted_op.result = {"success": False, "is_error": True,
                             "error_msg": "drift"}
        drifted = Scenario(
            scenario_id="scn-det", category="heatmap",
            turns=[
                TurnSpec(ops=[_upsert_op("c1")],
                         refs={"ref:geojson-parks": {}},
                         cartography=_cartography_ok()),
                TurnSpec(ops=[drifted_op, _runtime_validate_op("c3")],
                         cartography=_cartography_ok()),
            ],
        )
        r3 = await OfflineReplayer(seed=7).replay_scenario(drifted)
        assert r3.replay_digest != r1.replay_digest

    @pytest.mark.asyncio
    async def test_multi_turn_shares_session_context(self):
        """同一场景多 turn 共享 harness session —— 证据跨轮累积。"""
        scenario = Scenario(
            scenario_id="scn-mt", category="user_pin_hide",
            turns=[
                TurnSpec(ops=[_upsert_op("c1")],
                         refs={"ref:geojson-parks": {}},
                         cartography=_cartography_ok()),
                TurnSpec(ops=[ScenarioOp(
                    call_id="c2", tool="webgis_layer_patch",
                    arguments={"layer_id": "parks", "visible": False},
                    result={"success": True, "is_compiled": True,
                            "mapspec_fingerprint": FINGERPRINT_PLACEHOLDER},
                )], cartography=_cartography_ok()),
            ],
        )
        result = await OfflineReplayer(seed=3).replay_scenario(scenario)
        assert result.turns[1].evidence_count >= result.turns[0].evidence_count
        assert result.turns[1].evidence_count >= 2

    @pytest.mark.asyncio
    async def test_dispatch_backed_honest_not_run(self):
        """dispatch_backed 但缺 tool_registry fixture → t3_bind not_run 翻红。"""
        scenario = Scenario(
            scenario_id="scn-t3", category="raster_terrain",
            turns=[TurnSpec(ops=[_upsert_op()])],
            dispatch_backed=True,
        )
        result = await OfflineReplayer().replay_scenario(scenario)
        assert result.not_run == ["t3_bind"]
        assert result.ok is False  # not_run 不静默当绿

    @pytest.mark.asyncio
    async def test_dispatch_backed_bind_gate_replay(self):
        """ADR-0212 决策五：bind gate 重放（生产同函数）+ deferred 披露。"""
        scenario = Scenario(
            scenario_id="scn-t3-bind", category="raster_terrain",
            turns=[TurnSpec(ops=[_upsert_op()])],
            dispatch_backed=True,
            tool_registry={"webgis_layer_upsert": ["layer_management"]},
        )
        result = await OfflineReplayer().replay_scenario(scenario)
        assert "t3_bind" in result.levels_run
        assert result.not_run == []
        # ADR-0214 D6：T4 receipt 级已实装 —— 只对显式 receipt_backed 场景
        # 运行；本场景未声明 → 不跑 T4、无 deferred 披露（诚实缺席）。
        assert result.deferred_levels == []
        assert "t4_receipt" not in result.levels_run
        entries = result.turns[0].dispatch_decisions
        assert entries and entries[0]["tool"] == "webgis_layer_upsert"
        assert entries[0]["allowed"] is True

    @pytest.mark.asyncio
    async def test_dispatch_backed_expect_pins_denial(self):
        """expect["dispatch"] 白名单钉 allow/deny 裁决 —— 假期望翻红。"""
        scenario = Scenario(
            scenario_id="scn-t3-deny", category="recorded",
            turns=[TurnSpec(
                ops=[_upsert_op()],
                expect={"dispatch": {"c1": {"allowed": False}}},
            )],
            dispatch_backed=True,
            tool_registry={"webgis_layer_upsert": ["layer_management"]},
        )
        result = await OfflineReplayer().replay_scenario(scenario)
        assert result.ok is False
        assert any(d["path"].startswith("dispatch.c1")
                   for d in result.turns[0].exact_diffs)
