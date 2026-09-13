"""AC-09 end-to-end: visual judge & self-healing closed loop (ADR-0158).

Covers the acceptance gates:
- review coverage of the command path (map-change trigger, whitelist);
- visual judge wiring with strict fail-closed semantics (fault injection);
- L5 goal_satisfaction with the visual evidence hierarchy (visual can never
  alone produce L4/L5 PASS);
- self-heal action registry (>=5 types, risk graded, authorization gated);
- repair → re-evaluate → rollback chain and repair_exhausted audit.
"""

import copy
import shutil
import sys
import types
import uuid

import pytest

from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.lib.cartography.selfheal_actions import (
    SELFHEAL_ACTIONS,
    actions_by_id,
    authorized,
    build_presentation_commit,
    candidates_from_rejected,
    quality_improved,
    quality_snapshot,
    quality_worse,
    select_actions,
    triggers_from_review,
)
from app.lib.cartography.verdict_summary import render_verdict_for_llm
from app.lib.harness.evidence import CartographicReviewEvidence
from app.lib.harness.pi_agent_harness import PiAgentHarness
from app.lib.harness.visual_evaluator import (
    attach_visual_judgement,
    derive_goal_satisfaction,
    sanitize_critiques,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager


@pytest.fixture
async def judge_session():
    session_id = f"vj-{uuid.uuid4().hex[:10]}"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)
    shutil.rmtree(BASE_STORAGE_DIR / session_id, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_visual_env(monkeypatch):
    """隔离视觉裁判 env 与进程级记忆化（限流计数不得跨用例泄漏）。"""
    import app.lib.harness.visual_evaluator as ve

    for name in (
        "CARTO_VISUAL_JUDGE",
        "CARTO_VISUAL_JUDGE_VLM",
        "CARTO_VISUAL_JUDGE_SCREENSHOT",
        "CARTO_VISUAL_JUDGE_MODEL",
        "CARTO_VISUAL_JUDGE_MODE",
        "CARTO_SELFHEAL_EXPLICIT",
    ):
        monkeypatch.delenv(name, raising=False)
    ve._judge_memo.clear()
    yield
    ve._judge_memo.clear()


def _inject_judge(monkeypatch, name, callable_):
    """注册 module:callable 形式的注入 judge（与生产 env seam 同通道）。"""
    monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(judge=callable_))
    monkeypatch.setenv("CARTO_VISUAL_JUDGE", f"{name}:judge")


# ── 协议保真 fixtures（与 test_cartographic_quantitative_rules 同形） ─────

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64  # 稳定字节（base64+digest 用）

_SEPARABLE = ["#ffffb2", "#feb24c", "#fd8d3c", "#bd0026"]
_INSEPARABLE = ["#ff0000", "#ff0404", "#ff0808", "#ff0c0c"]
_POINT_PROFILE = {
    "featureCount": 50,
    "bbox": [116.0, 39.8, 116.8, 40.0],
    "geometryTypes": ["Point"],
    "crs": "EPSG:4326",
    "crs_status": "explicit",
    "fields": {"v": {"type": "number", "min": 0, "max": 10, "null_count": 0}},
}


def _thematic_mapspec(*, colors=None, placement=None):
    """graduated choropleth：legend palette_colors ↔ paint step 输出色一致，
    唯一可注入的失败面是低可分色带（rotate_palette 触发）等。"""
    colors = list(colors or _SEPARABLE)
    legend_spec = {
        "type": "graduated", "field": "v", "min": 0, "max": 10,
        "breaks": [0, 2.5, 5.0, 7.5, 10],
        "palette_colors": colors,
    }
    if placement is not None:
        legend_spec["placement"] = placement
    return {
        "version": "1.0",
        "sources": {
            "points": {
                "type": "geojson",
                "inlineData": {"type": "FeatureCollection", "features": []},
                "profile": _POINT_PROFILE,
            }
        },
        "layers": [
            {
                "id": "result",
                "source": "points",
                "type": "circle",
                "paint": {
                    "circle-color": {
                        "method": "step", "field": "v", "default": colors[0],
                        "stops": [
                            [2.5, colors[1]], [5.0, colors[2]], [7.5, colors[3]]
                        ],
                    },
                    "circle-opacity": 0.8,
                },
                "legend_spec": legend_spec,
            }
        ],
        "view": {"center": [116.4, 39.9], "zoom": 4.0},
        "layout": {"legend": {"visible": True}},
    }


def _observation(session_id, fingerprint, *, sequence, mapspec_layer=None,
                 visible=True, opacity=0.8, include_legend=True):
    """runtime 观测；legend 默认与图层逐字一致（RUNTIME_LEGEND_CONVERGENCE），
    include_legend=False 制造图例失收敛。"""
    observed = {
        "id": "result",
        "runtime_store_id": "result",
        "visible": visible,
        "opacity": opacity,
        "style_converged": True,
        "source_converged": True,
        "runtime_layer_count": 1,
        "projection_fingerprint": "runtime-test-projection",
        "intent_generation": sequence,
    }
    if include_legend and mapspec_layer is not None:
        observed["legend_spec"] = copy.deepcopy(mapspec_layer.get("legend_spec"))
    return {
        "session_id": session_id,
        "sequence": sequence,
        "source": "frontend_runtime",
        "mapspec_fingerprint": fingerprint,
        "style_loaded": True,
        "reconcile_error": "",
        "layers": [observed],
    }


def _record_mutation(harness, mapspec, *, observation_seq=0, tool_call_id="call-1"):
    harness.record_tool_call(
        tool_call_id, "webgis_layer_upsert", {"layer": {"id": "result"}}
    )
    harness.record_tool_result(
        tool_call_id,
        "webgis_layer_upsert",
        {
            "success": True,
            "is_compiled": True,
            "mapspec_fingerprint": cartographic_fingerprint(mapspec),
            "runtime_observation_seq": observation_seq,
            "runtime_projection_fingerprint": "runtime-test-projection",
            "cartographic_review": {
                "stage": "desired_state",
                "status": "passed",
                "final_fingerprint": cartographic_fingerprint(mapspec),
                "review": {"status": "pass", "passed": True, "checks": []},
            },
        },
        session_id=harness.session_id,
    )


async def _seed_generation(session_id, mapspec, harness, *, sequence, **obs_kw):
    await mapspec_store_instance.save_mapspec(session_id, mapspec)
    _record_mutation(harness, mapspec, observation_seq=sequence - 1)
    await session_data_manager.set_map_state(
        session_id,
        "_cartographic_observation",
        _observation(
            session_id, cartographic_fingerprint(mapspec), sequence=sequence,
            mapspec_layer=mapspec["layers"][0], **obs_kw,
        ),
    )


async def _ack_action(session_id, action):
    await session_data_manager.append_map_action_event(
        session_id,
        {
            "action_id": action["action_id"],
            "command": action["command"],
            "status": "succeeded",
            "actual": {"confirmed": True},
        },
    )


# ── P1：评审覆盖面（地图变更触发） ────────────────────────────────────────

def test_map_change_whitelist_structure():
    from app.services.cartography_runtime import result_indicates_map_change

    assert result_indicates_map_change({"mapspec_fingerprint": "fp"}) is True
    for command in (
        "LAYER_STYLE_UPDATE", "add_native_heatmap", "add_layer",
        "layer_visibility_update", "APPLY_LAYER_FILTER", "reorder_layer",
        "REMOVE_LAYER", "add_heatmap_raster",
    ):
        assert result_indicates_map_change({"command": command}) is True, command
        assert result_indicates_map_change({"commands": [{"command": command}]}) is True
    for command in ("fly_to", "export_map", "set_map_view", "BASE_LAYER_CHANGE",
                    "draw_measurement", "query_features", "FINALIZE_DISPLAY"):
        assert result_indicates_map_change({"command": command}) is False, command
    assert result_indicates_map_change("not-a-dict") is False
    assert result_indicates_map_change({}) is False


@pytest.mark.asyncio
async def test_command_path_enters_shared_evaluation(judge_session):
    """无 fingerprint 的命令路径（模板 symbology/热力图族）也触发共享评审。"""
    import app.services.cartography_runtime as bridge

    # 真实场景：模板作用在已有 committed 图层的会话上。
    mapspec = _thematic_mapspec()
    await mapspec_store_instance.save_mapspec(judge_session, mapspec)
    harness = bridge._get_session_harness(judge_session, create=True)
    _record_mutation(harness, mapspec, observation_seq=0)
    bridge._harness = harness

    class _Outcome:
        status = "ok"
        llm_payload = "applied"
        raw_result = {
            "status": "template_applied",
            "command": "LAYER_STYLE_UPDATE",
            "params": {"layer_id": "result", "style": {"fillColor": "#123456"}},
        }
        map_actions: list = []

    await bridge.record_cartographic_dispatch_evidence(
        judge_session, "call-cmd-1", "apply_template", {}, _Outcome(), 5
    )

    assert bridge._harnesses.get(judge_session) is not None, (
        "map-change command must create the session harness"
    )
    recorded = [
        result for result in harness.tool_results
        if result.get("tool_call_id") == "call-cmd-1"
    ]
    assert recorded, "command-path evidence must be recorded"
    state = await session_data_manager.get_map_state(judge_session)
    review = state.get("_cartographic_review")
    assert review is not None, "shared evaluation must have run"
    # 命令路径无世代标签 ⇒ 诚实 not_evaluated，绝不伪造收敛证据。
    assert review["cartography"]["status"] == "not_evaluated"
    assert review["overall_passed"] is False
    bridge._harnesses.pop(judge_session, None)


@pytest.mark.asyncio
async def test_camera_commands_do_not_trigger_evaluation(judge_session):
    import app.services.cartography_runtime as bridge

    class _Outcome:
        status = "ok"
        llm_payload = "moved"
        raw_result = {"command": "fly_to", "params": {"center": [0, 0]}}
        map_actions: list = []

    await bridge.record_cartographic_dispatch_evidence(
        judge_session, "call-cam-1", "set_map_view", {}, _Outcome(), 5
    )
    assert bridge._harnesses.get(judge_session) is None


def test_templates_surface_generation_evidence():
    """Plan A：symbology 命令结果回填 lifecycle 生成证据（#722 断裂口）。"""
    from app.tools.templates import _surface_generation_evidence

    result = {"status": "template_applied", "command": "LAYER_STYLE_UPDATE"}
    _surface_generation_evidence(result, {
        "success": True,
        "is_compiled": True,
        "mapspec_fingerprint": "carto-sha256:abc",
        "runtime_observation_seq": 7,
        "mutation_revision": 3,
    })
    assert result["mapspec_fingerprint"] == "carto-sha256:abc"
    assert result["is_compiled"] is True
    assert result["runtime_observation_seq"] == 7
    assert result["mutation_revision"] == 3

    failed = {"status": "template_applied"}
    _surface_generation_evidence(failed, {"success": False, "mapspec_fingerprint": "x"})
    assert "mapspec_fingerprint" not in failed
    _surface_generation_evidence(failed, None)
    assert "mapspec_fingerprint" not in failed


# ── P2：视觉裁判（fail-closed / 限流 / 多图型 / 白名单） ─────────────────

def test_sanitize_critiques_whitelist():
    raw = [
        {"dimension": "readability", "severity": "error", "suggestion": "x" * 500},
        {"dimension": "unknown_dim", "severity": "error", "suggestion": "drop"},
        {"dimension": "readability", "severity": "mega", "suggestion": "normalized"},
        {"dimension": "readability", "severity": "error", "mutation": {"a": 1}},
        "not-a-dict",
        {"dimension": "composition_balance", "severity": "info"},
    ] * 3
    critiques = sanitize_critiques(raw)
    assert len(critiques) <= 12
    dimensions = {c.dimension for c in critiques}
    assert "unknown_dim" not in dimensions
    assert all(c.evidence_class == "visual" for c in critiques)
    first = critiques[0]
    assert first.severity == "error"
    assert len(first.suggestion) <= 300
    normalized = next(c for c in critiques if c.suggestion == "normalized")
    assert normalized.severity == "warning"  # 非法 severity 归一为 warning


def test_visual_judge_mode_block_falls_back_to_record_only(monkeypatch, caplog):
    """阻断语义未实现：CARTO_VISUAL_JUDGE_MODE=block 诚实回退 record_only
    （一次性告警），证据行不得带着未实现的 mode:"block" 误导运营。"""
    import logging

    import app.lib.harness.visual_evaluator as ve

    monkeypatch.setattr(ve, "_BLOCK_FALLBACK_WARNED", False)
    monkeypatch.setenv("CARTO_VISUAL_JUDGE_MODE", "record_only")
    assert ve.visual_judge_mode() == "record_only"
    assert ve.visual_judge_mode() == "record_only"

    with caplog.at_level(logging.WARNING, logger=ve.__name__):
        monkeypatch.setenv("CARTO_VISUAL_JUDGE_MODE", "block")
        assert ve.visual_judge_mode() == "record_only"
        assert ve.visual_judge_mode() == "record_only"  # 回退稳定
    block_warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING and "block" in record.getMessage()
    ]
    assert len(block_warnings) == 1, "block 回退必须只告警一次"

    monkeypatch.setenv("CARTO_VISUAL_JUDGE_MODE", "total_nonsense")
    assert ve.visual_judge_mode() == "record_only"


@pytest.mark.asyncio
async def test_visual_judge_disabled_is_fail_closed(judge_session):
    evidence = CartographicReviewEvidence(session_id=judge_session)
    evidence.mapspec_fingerprint = "carto-sha256:fp"
    await attach_visual_judgement(judge_session, evidence, {})

    summary = evidence.visual_evidence[-1]
    assert summary["source"] == "visual_judge"
    assert summary["status"] == "not_evaluated"
    assert summary["reason"] == "visual_judge_disabled"
    assert evidence.status == "not_evaluated"  # record-only：不改三态
    goal = derive_goal_satisfaction(evidence)
    assert goal["status"] == "not_evaluated"


@pytest.mark.asyncio
async def test_visual_judge_three_map_types_evaluated(
    judge_session, tmp_path, monkeypatch
):
    """验收：visual 类证据在 ≥3 类图型上产出非 not_evaluated 结论。"""
    screenshot = tmp_path / "map.png"
    screenshot.write_bytes(_PNG_BYTES)
    monkeypatch.setenv("CARTO_VISUAL_JUDGE_SCREENSHOT", str(screenshot))
    seen_map_types = []

    def fake_judge(snapshot):
        seen_map_types.append(snapshot["fingerprint"])
        assert snapshot["screenshot"]["data_url"].startswith(
            "data:image/png;base64,"
        )
        return [
            {"dimension": "color_discriminability", "severity": "error",
             "suggestion": "adjacent classes blend together"},
            {"dimension": "polish_completeness", "severity": "warning",
             "suggestion": "legend title missing"},
        ]

    _inject_judge(monkeypatch, "ac09_judge_3types", fake_judge)

    map_types = ["choropleth", "heatmap_raster", "proportional_symbol"]
    for index, map_type in enumerate(map_types):
        evidence = CartographicReviewEvidence(session_id=judge_session)
        evidence.mapspec_fingerprint = f"carto-sha256:{map_type}-{index}"
        evidence.status = "passed"
        evidence.checks = [
            {"rule": "MAPSPEC_FINGERPRINT_CONVERGENCE", "status": "pass"}
        ]
        await attach_visual_judgement(judge_session, evidence, {})
        visual_rows = [
            check for check in evidence.checks
            if check.get("evidence_class") == "visual"
            and check.get("rule") != "VISUAL_ORACLE"
        ]
        assert visual_rows, f"{map_type} must produce visual critique rows"
        assert {row["rule"] for row in visual_rows} == {
            "VISUAL_COLOR_DISCRIMINABILITY", "VISUAL_POLISH_COMPLETENESS"
        }
        failing = visual_rows[0]
        assert failing["status"] == "fail"
        assert failing["repairability"] == "auto_safe"  # 色彩维度 → rotate_palette
        summary = evidence.visual_evidence[-1]
        assert summary["status"] == "evaluated"
        assert summary["error_count"] == 1
    assert len(seen_map_types) == 3


@pytest.mark.asyncio
async def test_visual_judge_fault_injection_never_fakes_pass(
    judge_session, tmp_path, monkeypatch
):
    """故障注入：judge 抛错/超时 ⇒ not_evaluated，绝不产出 pass。"""
    screenshot = tmp_path / "map.png"
    screenshot.write_bytes(_PNG_BYTES)
    monkeypatch.setenv("CARTO_VISUAL_JUDGE_SCREENSHOT", str(screenshot))

    def exploding_judge(snapshot):
        raise TimeoutError("vlm unreachable")

    _inject_judge(monkeypatch, "ac09_judge_boom", exploding_judge)

    evidence = CartographicReviewEvidence(session_id=judge_session)
    evidence.mapspec_fingerprint = "carto-sha256:boom"
    await attach_visual_judgement(judge_session, evidence, {})

    summary = evidence.visual_evidence[-1]
    assert summary["status"] == "not_evaluated"
    assert summary["reason"] == "provider_error"
    oracle_row = next(
        check for check in evidence.checks if check.get("rule") == "VISUAL_ORACLE"
    )
    assert oracle_row["status"] == "not_evaluated"
    assert oracle_row["evidence_class"] == "visual"
    # 全链路不得出现 pass 判定：
    goal = derive_goal_satisfaction(evidence)
    assert goal["status"] == "not_evaluated"
    run = type("R", (), {"evidence": []})()
    levels = PiAgentHarness._success_levels(run, evidence)
    assert levels["goal_satisfaction"]["status"] == "not_evaluated"
    assert levels["cartographic_quality"]["status"] == "not_evaluated"


@pytest.mark.asyncio
async def test_visual_judge_no_screenshot_is_not_evaluated(judge_session, monkeypatch):
    def fake_judge(snapshot):  # pragma: no cover - 不应被调用
        raise AssertionError("judge must not run without a screenshot")

    _inject_judge(monkeypatch, "ac09_judge_noshot", fake_judge)
    evidence = CartographicReviewEvidence(session_id=judge_session)
    evidence.mapspec_fingerprint = "carto-sha256:noshot"
    await attach_visual_judgement(judge_session, evidence, {})
    assert evidence.visual_evidence[-1]["reason"] == "no_screenshot"


@pytest.mark.asyncio
async def test_visual_judge_memoizes_single_call_per_state(
    judge_session, tmp_path, monkeypatch
):
    """限流：同一 (session, fingerprint, 截图) 至多一次真实外呼。"""
    screenshot = tmp_path / "map.png"
    screenshot.write_bytes(_PNG_BYTES)
    monkeypatch.setenv("CARTO_VISUAL_JUDGE_SCREENSHOT", str(screenshot))
    calls = []

    def counting_judge(snapshot):
        calls.append(1)
        return [{"dimension": "readability", "severity": "info"}]

    _inject_judge(monkeypatch, "ac09_judge_count", counting_judge)

    for _ in range(3):
        evidence = CartographicReviewEvidence(session_id=judge_session)
        evidence.mapspec_fingerprint = "carto-sha256:memo"
        await attach_visual_judgement(judge_session, evidence, {})
    assert len(calls) == 1, "memoized judge must be called once per evidence state"


# ── P3：L5 推导（visual 绝不单独判 PASS） ────────────────────────────────

def _evidence_with_visual(status="evaluated", error_count=0, *, l4="passed"):
    evidence = CartographicReviewEvidence(session_id="s")
    evidence.mapspec_fingerprint = "fp"
    evidence.status = l4
    evidence.visual_evidence = [{
        "source": "visual_judge",
        "status": status,
        "error_count": error_count,
        "warning_count": 0,
        "critiques": [],
    }]
    return evidence


def test_l5_visual_fail_downgrades():
    goal = derive_goal_satisfaction(_evidence_with_visual(error_count=2))
    assert goal["status"] == "fail"
    assert goal["reason"] == "visual_error_critique"


def test_l5_visual_concurs_only_with_l4_anchor():
    # L4 锚点通过 + 视觉无 error ⇒ L5 pass。
    goal = derive_goal_satisfaction(_evidence_with_visual(error_count=0))
    assert goal["status"] == "pass"
    assert goal["reason"] == "visual_judge_concurred"
    # L4 未通过 ⇒ visual 不得替 L4 背书：L5 保持 not_evaluated（硬约束）。
    l4_failed = _evidence_with_visual(error_count=0, l4="failed_repairable")
    goal = derive_goal_satisfaction(l4_failed)
    assert goal["status"] == "not_evaluated"
    assert goal["reason"] == "l4_anchor_not_passed"


def test_l5_success_levels_visual_never_alone_passes():
    """显式断言：visual 证据不得单独判 L4/L5 PASS（closed-loop 硬约束）。"""
    evidence = _evidence_with_visual(error_count=0, l4="failed_repairable")
    run = type("R", (), {"evidence": []})()
    levels = PiAgentHarness._success_levels(run, evidence)
    assert levels["cartographic_quality"]["status"] == "failed_repairable"
    assert levels["goal_satisfaction"]["status"] == "not_evaluated"
    assert levels["goal_satisfaction"]["reason"] == "l4_anchor_not_passed"


def test_verdict_extension_keeps_three_state_tokens():
    review = {
        "cartography": {
            "status": "failed_repairable",
            "mapspec_fingerprint": "fp",
            "termination_reason": "desired_quality_failed",
            "selfheal_suggestions": [{
                "action_id": "adjust_classification",
                "risk": "auto_with_semantic_risk",
                "reason": "semantic_risk_requires_authorization",
            }],
            "visual_evidence": [{
                "source": "visual_judge", "status": "evaluated",
                "error_count": 1, "warning_count": 2, "critiques": [],
            }],
            "repair_attempts": [{
                "iteration": 1, "status": "succeeded",
                "repairability": "auto_safe",
                "action_name": "restore_projection", "improved": False,
            }],
        },
        "overall_passed": False,
    }
    rendered = render_verdict_for_llm(review)
    assert '"verdict": "fail"' in rendered
    assert "restore_projection" in rendered
    assert "adjust_classification" in rendered
    assert '"visual"' in rendered
    # 三态 token 语义不变：pass 的旧形状不含新增块。
    passed = render_verdict_for_llm({
        "cartography": {"status": "passed", "mapspec_fingerprint": "fp"}
    })
    assert '"verdict": "pass"' in passed
    assert "selfheal_suggestions" not in passed


# ── P4：自愈动作注册表 ───────────────────────────────────────────────────

def test_action_registry_has_at_least_five_types_with_risk_grading():
    action_ids = {spec.action_id for spec in SELFHEAL_ACTIONS}
    assert len(SELFHEAL_ACTIONS) >= 5
    assert {
        "restore_visibility", "reapply_opacity", "refresh_legend",
        "restore_style_projection",           # 既有投影恢复（行为不变）
        "rotate_palette",                     # 换色带
        "clamp_layout",                       # 改版面
        "adjust_classification",              # 换分类方法/级数
        "clip_value_domain",                  # 重裁值域
        "adjust_labels",                      # 改标注策略
        "switch_map_type",                    # 换图型（explicit_only）
    } <= action_ids
    for spec in SELFHEAL_ACTIONS:
        assert spec.risk in ("auto_safe", "auto_with_semantic_risk", "explicit_only")
        assert spec.surface in ("runtime", "desired_state")
        assert 0.0 <= spec.expected_effect <= 1.0
    assert actions_by_id()["switch_map_type"].risk == "explicit_only"
    assert not authorized(actions_by_id()["switch_map_type"])


def test_action_selection_risk_ascending_effect_descending():
    triggers = {
        "RUNTIME_RESULT_VISIBILITY", "carto.color.separability",
        "VISUAL_READABILITY",
    }
    ranked = select_actions(triggers)
    ids = [spec.action_id for spec in ranked]
    # risk 升序：auto_safe 的 restore_visibility 在最前；语义级
    # adjust_classification 排在 auto_safe 的 rotate_palette 之后。
    assert ids.index("restore_visibility") < ids.index("rotate_palette")
    assert ids.index("rotate_palette") < ids.index("adjust_classification")
    # tried 动作被排除：
    ranked_again = select_actions(
        triggers, tried_actions=["restore_visibility", "rotate_palette"]
    )
    remaining = [spec.action_id for spec in ranked_again]
    assert "rotate_palette" not in remaining
    assert "adjust_classification" in remaining


def test_semantic_actions_require_explicit_authorization(monkeypatch):
    monkeypatch.delenv("CARTO_SELFHEAL_EXPLICIT", raising=False)
    assert not authorized(actions_by_id()["adjust_classification"])
    monkeypatch.setenv("CARTO_SELFHEAL_EXPLICIT", "1")
    assert authorized(actions_by_id()["adjust_classification"])


def test_rejected_candidates_fixture_interface():
    """03 线联动：SymbologyDecision.rejected[] 落选者 = 候选修复动作。"""
    rejected = [{
        "layer_id": "result",
        "method": "quantiles",
        "k": 5,
        "expected_effect": 0.55,
        "reason": "heavy-tailed distribution",
        "legend_spec": {
            "type": "graduated", "colors": ["#a", "#b", "#c", "#d", "#e"]
        },
    }]
    mapspec = _thematic_mapspec()
    mapspec["cartographic_profile"] = {
        "symbology_decision": {"rejected": rejected}
    }
    pool = candidates_from_rejected(mapspec)
    assert pool == rejected
    # 无该键（03 未合入/无裁决）→ 空列表，诚实降级。
    assert candidates_from_rejected(_thematic_mapspec()) == []

    recipe = build_presentation_commit(
        actions_by_id()["adjust_classification"],
        layer=mapspec["layers"][0],
        rejected=pool[0],
    )
    assert recipe["operation"] == "adjust_classification"
    assert recipe["method"] == "quantiles"
    assert recipe["k"] == 5
    # 无落选者时不构造（绝不自行猜新断点）。
    assert build_presentation_commit(
        actions_by_id()["adjust_classification"],
        layer=mapspec["layers"][0],
        rejected=None,
    ) is None


def test_palette_rotation_builder_prefers_perceptual_separability():
    layer = _thematic_mapspec(colors=_INSEPARABLE)["layers"][0]
    recipe = build_presentation_commit(
        actions_by_id()["rotate_palette"], layer=layer
    )
    assert recipe is not None
    assert recipe["operation"] == "rotate_palette"
    assert recipe["before_colors"] == _INSEPARABLE
    assert len(recipe["after_colors"]) == 4
    assert recipe["after_colors"] != recipe["before_colors"]
    # paint 输出色必须同步轮换（防 LEGEND_STYLE_EQUIVALENCE 拒绝提交）。
    assert "paint" in recipe
    rotated_stops = recipe["paint"]["circle-color"]["stops"]
    assert [stop[1] for stop in rotated_stops] == recipe["after_colors"][1:]


def test_clamp_layout_builder_bounds_placement():
    layer = _thematic_mapspec(placement={"x": 1.7, "y": 0.4})["layers"][0]
    recipe = build_presentation_commit(actions_by_id()["clamp_layout"], layer=layer)
    assert recipe["after_placement"] == {"x": 1.0, "y": 0.4}
    # 无放置信息 → 不适用（诚实跳过）。
    plain = _thematic_mapspec()["layers"][0]
    assert build_presentation_commit(
        actions_by_id()["clamp_layout"], layer=plain
    ) is None


def test_quality_snapshot_hierarchy():
    bad_deterministic = quality_snapshot({"checks": [
        {"rule": "carto.color.separability", "status": "fail"},
        {"rule": "VISUAL_READABILITY", "status": "fail", "evidence_class": "visual"},
    ]})
    worse_visual = quality_snapshot({"checks": [
        {"rule": "carto.color.separability", "status": "fail"},
        {"rule": "VISUAL_READABILITY", "status": "fail", "evidence_class": "visual"},
        {"rule": "VISUAL_POLISH", "status": "warning", "evidence_class": "visual"},
    ]})
    assert quality_improved(bad_deterministic, worse_visual) is False
    assert quality_worse(bad_deterministic, worse_visual) is True
    better = quality_snapshot({"checks": [
        {"rule": "VISUAL_READABILITY", "status": "fail", "evidence_class": "visual"},
    ]})
    assert quality_improved(bad_deterministic, better) is True
    assert quality_worse(bad_deterministic, better) is False


def test_triggers_from_review_extracts_rules():
    triggers = triggers_from_review({"checks": [
        {"rule": "carto.color.separability", "status": "fail"},
        {"rule": "VISUAL_READABILITY", "status": "warning",
         "evidence_class": "visual"},
        {"rule": "PASSED_RULE", "status": "pass"},
    ]})
    assert triggers == {"carto.color.separability", "VISUAL_READABILITY"}


# ── P5：修复-重评-回退链路 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_projection_patch_worse_quality_triggers_rollback(judge_session):
    """有害 patch（比修复前更差）→ 下轮回退（before/desired 互换）。"""
    import app.services.cartography_runtime as bridge

    mapspec = _thematic_mapspec()
    fingerprint = cartographic_fingerprint(mapspec)
    await _seed_generation(judge_session, mapspec, bridge._get_session_harness(
        judge_session, create=True
    ), sequence=1, visible=False)

    first = await bridge.evaluate_cartographic_session(judge_session)
    action = first["repair_action"]
    assert action["params"]["repair_patches"][0]["desired"] == {"visible": True}

    # ACK 成功 + 更差的新观察（可见性修复但透明度/图例双双失收敛）。
    await _ack_action(judge_session, action)
    await session_data_manager.set_map_state(
        judge_session,
        "_cartographic_observation",
        _observation(judge_session, fingerprint, sequence=2, visible=True,
                     opacity=0.2, include_legend=False),
    )
    second = await bridge.evaluate_cartographic_session(judge_session)

    rollback = second["repair_action"]
    assert rollback["command"] == "cartographic_runtime_repair"
    rollback_patch = rollback["params"]["repair_patches"][0]
    assert rollback_patch["desired"] == {"visible": False}
    assert rollback_patch["before"]["visible"] is True
    attempts = (await session_data_manager.get_map_state(judge_session))[
        "_cartographic_repair_state"
    ]["attempts"]
    assert attempts[0]["worse"] is True
    assert attempts[1]["kind"] == "rollback"
    bridge._harnesses.pop(judge_session, None)


@pytest.mark.asyncio
async def test_projection_patch_equal_quality_terminates_repeated(judge_session):
    """持平（未改善也未变差）→ 不回退（回退会拉离权威投影），既有
    repeated_runtime_repair 语义保持。"""
    import app.services.cartography_runtime as bridge

    mapspec = _thematic_mapspec()
    fingerprint = cartographic_fingerprint(mapspec)
    harness = bridge._get_session_harness(judge_session, create=True)
    await _seed_generation(judge_session, mapspec, harness,
                           sequence=1, visible=False)
    first = await bridge.evaluate_cartographic_session(judge_session)
    action = first["repair_action"]
    await _ack_action(judge_session, action)
    # ACK 成功但观测未变（同量 fail：1 → 1，持平）。
    await session_data_manager.set_map_state(
        judge_session,
        "_cartographic_observation",
        _observation(judge_session, fingerprint, sequence=2, visible=False,
                     mapspec_layer=mapspec["layers"][0]),
    )
    second = await bridge.evaluate_cartographic_session(judge_session)
    # 持平 → 不发回退；无新动作 ⇒ 诚实终止（既有重复补丁语义）。
    assert second["cartography"]["status"] == "repair_exhausted"
    assert second["cartography"]["termination_reason"] == "repeated_runtime_repair"
    assert "repair_action" not in second
    state = await session_data_manager.get_map_state(judge_session)
    assert state["_cartographic_repair_state"]["attempts"][0]["improved"] is False
    assert state["_cartographic_repair_state"]["attempts"][0]["worse"] is False
    bridge._harnesses.pop(judge_session, None)


@pytest.mark.asyncio
async def test_palette_rotation_commit_heals_deterministic_color_failure(
    judge_session,
):
    """换色带成功样本：确定性色可分失败 → rotate_palette 经 lifecycle 提交
    → 新世代收敛通过（完整修复-重评闭环）。"""
    import app.services.cartography_runtime as bridge

    mapspec = _thematic_mapspec(colors=_INSEPARABLE)
    harness = bridge._get_session_harness(judge_session, create=True)
    await _seed_generation(judge_session, mapspec, harness, sequence=1)

    first = await bridge.evaluate_cartographic_session(judge_session)

    # 无 runtime 规则失败（观测收敛）→ union patch 不适用 → 呈现提交计划，
    # 且提交在 evaluate 返回前已内联执行完成（会话锁外确定性执行）。
    issued = first.get("selfheal_commit_issued")
    assert issued and issued["action_name"] == "rotate_palette"
    assert first["cartography"]["status"] == "not_evaluated"
    assert first["cartography"]["termination_reason"] == (
        "selfheal_presentation_commit_issued"
    )

    committed = await mapspec_store_instance.get_mapspec(judge_session)
    new_colors = committed["layers"][0]["legend_spec"]["palette_colors"]
    assert new_colors != _INSEPARABLE
    assert committed["layers"][0]["legend_spec"].get("palette")
    # paint 输出色同步轮换（防 legend↔paint 漂移被下一轮评审拒绝）。
    rotated_paint = committed["layers"][0]["paint"]["circle-color"]
    assert [stop[1] for stop in rotated_paint["stops"]] == new_colors[1:]
    assert rotated_paint["default"] == new_colors[0]
    # 提交世代进入 harness mutation 台账（防跨代 superseded 断裂）。
    commit_mutations = [
        mutation for mutation in harness.mapspec_mutations
        if mutation.get("tool_name") == "cartographic_selfheal_commit"
    ]
    assert commit_mutations, "commit generation must be recorded as a mutation"

    # 新观察（新指纹）→ 复评收敛通过 = 自愈成功闭环。
    fingerprint_b = cartographic_fingerprint(committed)
    await session_data_manager.set_map_state(
        judge_session,
        "_cartographic_observation",
        _observation(judge_session, fingerprint_b, sequence=2,
                     mapspec_layer=committed["layers"][0]),
    )
    second = await bridge.evaluate_cartographic_session(judge_session)
    assert second["cartography"]["status"] == "passed"
    assert second["overall_passed"] is True
    bridge._harnesses.pop(judge_session, None)


@pytest.mark.asyncio
async def test_commit_no_improvement_reverts_previous_presentation(judge_session):
    """呈现提交未改善 → 回退（重提交变更前呈现，防色带轮换循环）。"""
    import app.services.cartography_runtime as bridge

    bad_colors = _INSEPARABLE
    mapspec = _thematic_mapspec(colors=bad_colors)
    harness = bridge._get_session_harness(judge_session, create=True)
    await _seed_generation(judge_session, mapspec, harness, sequence=1)
    first = await bridge.evaluate_cartographic_session(judge_session)
    assert first.get("selfheal_commit_issued"), "rotation commit expected"
    # 提交内联执行完成（新色带 + harness 台账登记）。
    committed = await mapspec_store_instance.get_mapspec(judge_session)
    assert committed["layers"][0]["legend_spec"]["palette_colors"] != bad_colors
    assert [
        mutation for mutation in harness.mapspec_mutations
        if mutation.get("tool_name") == "cartographic_selfheal_commit"
    ]

    # 构造"提交后世代仍未改善"：一个仍低可分（蓝色族）的新世代 —— 指纹
    # 与其 mutation 台账一致；上一代已成功的 commit 尝试进入 history。
    blue_colors = ["#0000ff", "#0004ff", "#0008ff", "#000cff"]
    committed = await mapspec_store_instance.get_mapspec(judge_session)
    committed["layers"][0]["legend_spec"]["palette_colors"] = list(blue_colors)
    committed["layers"][0]["paint"]["circle-color"] = {
        "method": "step", "field": "v", "default": "#0000ff",
        "stops": [[2.5, "#0004ff"], [5.0, "#0008ff"], [7.5, "#000cff"]],
    }
    fingerprint_b = cartographic_fingerprint(committed)
    await mapspec_store_instance.save_mapspec(judge_session, committed)
    _record_mutation(
        harness, committed, observation_seq=1, tool_call_id="call-genb"
    )
    prior_state = await session_data_manager.get_map_state(judge_session)
    previous_repair = prior_state["_cartographic_repair_state"]
    commit_attempt = next(
        (
            attempt for attempt in reversed(previous_repair["attempts"])
            if attempt.get("kind") == "commit"
            and attempt.get("status") == "succeeded"
        ),
        None,
    )
    assert commit_attempt is not None, "commit attempt must have succeeded"
    seeded_state = {
        "mapspec_fingerprint": fingerprint_b,
        "attempts": [],
        "history": [commit_attempt],
        "inherited_tried": ["rotate_palette"],
    }
    await session_data_manager.set_map_state(
        judge_session, "_cartographic_repair_state", seeded_state
    )
    await session_data_manager.set_map_state(
        judge_session,
        "_cartographic_observation",
        _observation(judge_session, fingerprint_b, sequence=2,
                     mapspec_layer=committed["layers"][0]),
    )
    second = await bridge.evaluate_cartographic_session(judge_session)

    # 未改善（deterministic_fail 持平）→ 回退提交：把世代从失败输出上移开
    # （恢复变更前呈现；lifecycle 会对恢复候选做自己的确定性复审）。
    revert_issued = second.get("selfheal_commit_issued")
    assert revert_issued, "commit revert must be issued when the commit did not help"
    assert revert_issued["action_name"].startswith("revert:")

    # 回退内联执行完成：世代已从蓝色失败输出上移开（lifecycle 复审会对
    # 恢复候选自行 repair），且第二次 selfheal mutation 已登记。
    reverted = await mapspec_store_instance.get_mapspec(judge_session)
    colors = reverted["layers"][0]["legend_spec"]["palette_colors"]
    assert colors != blue_colors
    mutations = [
        mutation for mutation in harness.mapspec_mutations
        if mutation.get("tool_name") == "cartographic_selfheal_commit"
    ]
    assert len(mutations) >= 2
    state = await session_data_manager.get_map_state(judge_session)
    revert_attempts = [
        attempt for attempt in state["_cartographic_repair_state"]["attempts"]
        if attempt.get("kind") == "commit_revert"
    ]
    assert revert_attempts and revert_attempts[-1]["status"] == "succeeded"
    bridge._harnesses.pop(judge_session, None)


@pytest.mark.asyncio
async def test_failed_commit_in_history_never_triggers_revert(judge_session):
    """回归：history 里失败的提交（如被 lifecycle 拒绝）不得做改善判定 ——
    其 before_presentation 是从未生效的陈旧呈现，据其回退会用旧值覆盖
    当前 legend_spec/paint。succeeded-only 同 attempts 循环纪律。"""
    import app.services.cartography_runtime as bridge

    mapspec = _thematic_mapspec(colors=_INSEPARABLE)
    fingerprint = cartographic_fingerprint(mapspec)
    harness = bridge._get_session_harness(judge_session, create=True)
    await mapspec_store_instance.save_mapspec(judge_session, mapspec)
    _record_mutation(harness, mapspec, observation_seq=0)

    # 直接注入一条"失败提交"进 history（同指纹 → 不触发世代重置）。
    stale_legend = dict(mapspec["layers"][0]["legend_spec"],
                        palette_colors=list(_SEPARABLE))
    failed_commit = {
        "iteration": 1,
        "kind": "commit",
        "action_id": "selfheal-failed-gen",
        "action_name": "rotate_palette",
        "covers": ["rotate_palette"],
        "commit_fingerprint": "fp-failed",
        "status": "failed",
        "error": "layer_upsert_rejected",
        "repairability": "auto_safe",
        "quality_before": {
            "deterministic_fail": 1, "deterministic_warning": 0,
            "visual_error": 0, "visual_warning": 0,
        },
        "before_presentation": {
            "result": {
                "legend_spec": stale_legend,
                "paint": copy.deepcopy(mapspec["layers"][0]["paint"]),
            },
        },
    }
    seeded_state = {
        "mapspec_fingerprint": fingerprint,
        "attempts": [],
        "history": [failed_commit],
        "inherited_tried": ["rotate_palette"],
    }
    await session_data_manager.set_map_state(
        judge_session, "_cartographic_repair_state", seeded_state
    )

    cartography = {
        "mapspec_fingerprint": fingerprint,
        "status": "failed_repairable",
        "passed": False,
        "source_tool_call_id": "call-1",
        "checks": [
            # 一个确定性 fail（quality_now 与 quality_before 持平 → 若被判定
            # 就是"未改善"→ 旧缺陷会回退）；规则词不触发任何 desired_state 动作。
            {"rule": "RUNTIME_OPACITY_CONVERGENCE", "status": "fail",
             "evidence": {"layer_id": "result", "runtime_layer_id": "result"}},
        ],
    }
    result = {"cartography": cartography, "overall_passed": False}
    advanced = await bridge._advance_runtime_cartographic_repair(
        session_id=judge_session,
        harness=harness,
        result=result,
        map_state={
            "_cartographic_observation": {"sequence": 2, "layers": []},
            "_cartographic_repair_state": seeded_state,
        },
        actions=[],
    )

    # 失败提交绝不被判定，更绝不触发回退提交。
    assert "selfheal_commit_issued" not in advanced
    assert "_pending_selfheal_commit" not in advanced
    state = await session_data_manager.get_map_state(judge_session)
    history = state["_cartographic_repair_state"]["history"]
    assert history and history[0]["status"] == "failed"
    assert "quality_after" not in history[0], "failed commit must not be judged"
    assert "improved" not in history[0]
    # 世代图层未被陈旧 before_presentation 覆盖。
    current = await mapspec_store_instance.get_mapspec(judge_session)
    assert (
        current["layers"][0]["legend_spec"]["palette_colors"] == _INSEPARABLE
    )
    bridge._harnesses.pop(judge_session, None)


@pytest.mark.asyncio
async def test_clamp_layout_commit_success_sample(judge_session):
    """改版面成功样本（编排器直调）：视觉构图失衡 + runtime 透明度分歧 →
    恢复投影不适用（观测不匹配）→ clamp_layout 提交钳制越界图例。"""
    import app.services.cartography_runtime as bridge

    mapspec = _thematic_mapspec(placement={"x": 1.8, "y": 0.5})
    fingerprint = cartographic_fingerprint(mapspec)
    await mapspec_store_instance.save_mapspec(judge_session, mapspec)
    harness = bridge._get_session_harness(judge_session, create=True)
    _record_mutation(harness, mapspec, observation_seq=0)

    cartography = {
        "mapspec_fingerprint": fingerprint,
        "status": "failed_repairable",
        "passed": False,
        "source_tool_call_id": "call-1",
        "checks": [
            # runtime 投影恢复面（观测不匹配 → planner 无 patch）
            {"rule": "RUNTIME_OPACITY_CONVERGENCE", "status": "fail",
             "evidence": {"layer_id": "result", "runtime_layer_id": "result"}},
            # 视觉构图失衡（record-only 证据，触发 clamp_layout）
            {"rule": "VISUAL_COMPOSITION_BALANCE", "status": "fail",
             "evidence_class": "visual",
             "evidence": {"layer_id": "result"}},
        ],
    }
    observation = {
        "sequence": 3,
        "layers": [],  # 无匹配观测层 → plan_runtime_repairs 无 patch 可发
    }
    result = {"cartography": cartography, "overall_passed": False}
    advanced = await bridge._advance_runtime_cartographic_repair(
        session_id=judge_session,
        harness=harness,
        result=result,
        map_state={"_cartographic_observation": observation},
        actions=[],
    )
    issued = advanced.get("selfheal_commit_issued")
    assert issued and issued["action_name"] == "clamp_layout"
    pending = advanced.get("_pending_selfheal_commit")
    assert pending, "clamp must produce a pending presentation commit"
    # 直调执行器（evaluate 入口在生产中调度同一函数）。
    await bridge._execute_selfheal_commit(judge_session, pending)
    committed = await mapspec_store_instance.get_mapspec(judge_session)
    clamped = committed["layers"][0]["legend_spec"]["placement"]
    assert clamped == {"x": 1.0, "y": 0.5}
    bridge._harnesses.pop(judge_session, None)


@pytest.mark.asyncio
async def test_unauthorized_semantic_actions_surface_as_suggestions(judge_session):
    """语义级动作默认只建议不执行（危险级需显式授权；explicit_only 永建议）。"""
    import app.services.cartography_runtime as bridge

    mapspec = _thematic_mapspec()
    fingerprint = cartographic_fingerprint(mapspec)
    await mapspec_store_instance.save_mapspec(judge_session, mapspec)
    harness = bridge._get_session_harness(judge_session, create=True)
    _record_mutation(harness, mapspec, observation_seq=0)

    cartography = {
        "mapspec_fingerprint": fingerprint,
        "status": "failed_repairable",
        "passed": False,
        "source_tool_call_id": "call-1",
        "checks": [
            {"rule": "RUNTIME_OPACITY_CONVERGENCE", "status": "fail",
             "evidence": {"layer_id": "result", "runtime_layer_id": "result"}},
            {"rule": "VISUAL_READABILITY", "status": "fail",
             "evidence_class": "visual", "evidence": {"layer_id": "result"}},
        ],
    }
    observation = {"sequence": 3, "layers": []}
    result = {"cartography": cartography, "overall_passed": False}
    advanced = await bridge._advance_runtime_cartographic_repair(
        session_id=judge_session,
        harness=harness,
        result=result,
        map_state={"_cartographic_observation": observation},
        actions=[],
    )
    suggestions = advanced["cartography"].get("selfheal_suggestions") or []
    suggestion_ids = {item["action_id"] for item in suggestions}
    assert {"adjust_classification", "adjust_labels", "switch_map_type"} <= (
        suggestion_ids
    )
    # 语义动作绝不执行：无 repair_action、无 presentation_commits。
    assert "repair_action" not in advanced
    assert "presentation_commits" not in advanced
    assert advanced["cartography"]["status"] == "failed_unrepairable"
    bridge._harnesses.pop(judge_session, None)


@pytest.mark.asyncio
async def test_authorized_semantic_incomplete_recipe_is_suggestion_only(
    judge_session, monkeypatch
):
    """回归（D-7）：显式授权（CARTO_SELFHEAL_EXPLICIT=1）下，语义级动作构造
    的 recipe 只有建议参数（method/k，无 legend_spec/paint）——提交它只会
    layer_upsert 一份原样 deep-copy（no-op 提交烧掉尝试配额）。必须只披露
    建议，不提交、不消耗尝试。"""
    import app.services.cartography_runtime as bridge

    monkeypatch.setenv("CARTO_SELFHEAL_EXPLICIT", "1")
    mapspec = _thematic_mapspec()
    mapspec["cartographic_profile"] = {
        "symbology_decision": {
            "rejected": [
                {
                    "method": "quantiles",
                    "k": 5,
                    "expected_effect": 0.55,
                    "reason": "heavy-tailed distribution",
                }
            ],
        },
    }
    fingerprint = cartographic_fingerprint(mapspec)
    await mapspec_store_instance.save_mapspec(judge_session, mapspec)
    harness = bridge._get_session_harness(judge_session, create=True)
    _record_mutation(harness, mapspec, observation_seq=0)

    cartography = {
        "mapspec_fingerprint": fingerprint,
        "status": "failed_repairable",
        "passed": False,
        "source_tool_call_id": "call-1",
        "checks": [
            # VISUAL_READABILITY 触发 adjust_labels / adjust_classification /
            # switch_map_type；本图层无 layout.text-field → adjust_labels 无
            # recipe；adjust_classification 有 recipe 但不含呈现字段。
            {"rule": "VISUAL_READABILITY", "status": "fail",
             "evidence_class": "visual", "evidence": {"layer_id": "result"}},
        ],
    }
    result = {"cartography": cartography, "overall_passed": False}
    advanced = await bridge._advance_runtime_cartographic_repair(
        session_id=judge_session,
        harness=harness,
        result=result,
        map_state={"_cartographic_observation": {"sequence": 3, "layers": []}},
        actions=[],
    )

    # 不完整 recipe 绝不提交：无呈现提交计划、无回退、无修复动作。
    assert "selfheal_commit_issued" not in advanced
    assert "_pending_selfheal_commit" not in advanced
    assert "repair_action" not in advanced
    # 建议保留在证据里（带原因与 recipe 本体），且不消耗尝试配额。
    suggestions = advanced["cartography"].get("selfheal_suggestions") or []
    incomplete = [
        item for item in suggestions
        if item.get("action_id") == "adjust_classification"
    ]
    assert incomplete, "incomplete recipe must stay disclosed as a suggestion"
    entry = incomplete[-1]
    assert entry["reason"] == "incomplete_recipe_suggestion_only"
    recipe = entry["suggestion"]
    assert recipe["operation"] == "adjust_classification"
    assert recipe["method"] == "quantiles" and recipe["k"] == 5
    assert "legend_spec" not in recipe and "paint" not in recipe
    state = await session_data_manager.get_map_state(judge_session)
    assert state["_cartographic_repair_state"]["attempts"] == [], (
        "suggestion-only recipes must not consume a commit attempt"
    )
    # 世代图层未被任何 no-op 提交触碰。
    current = await mapspec_store_instance.get_mapspec(judge_session)
    assert current == mapspec
    assert (
        current["layers"][0]["legend_spec"]["palette_colors"] == _SEPARABLE
    )
    bridge._harnesses.pop(judge_session, None)
