"""F15 生产视觉 provider 单元回归（ADR-0214 决策二）。

不变式：
1. 默认关（``GIS_VISUAL_EVALUATOR`` 未配置）= 零行为变化；配置后失败
   路径全部诚实降级（空 findings / not_evaluated reason），deterministic
   verifier 永不被阻断；
2. 反翻转：provider 输出经 seam 白名单二次消毒——非 visual domain、
   mutation 意图字段在结构上进不了披露面；视觉 error 不得把非 READY
   状态提升为完成，也不得掩盖 deterministic error；
3. rules-half 只回答像素可度量的问题（不产 label_collision/legend 等
   几何/语义类），无截图 → not_evaluated 诚实缺席。
"""

from __future__ import annotations

import uuid

import pytest

from app.lib.harness.visual_judge.contracts import VisualJudgeReport
from app.services.gis_harness.completion.pipeline import (
    _assemble_visual_snapshot,
    _maybe_run_visual_evaluation,
)
from app.services.gis_harness.visual_observation import provider as vp
from app.services.gis_harness.visual_observation.contracts import (
    VisualObservationInput,
    VisualScreenshotRef,
)
from app.services.gis_harness.visual_evaluator import (
    get_visual_evaluator,
    run_visual_evaluation,
)


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    for key in ("GIS_VISUAL_EVALUATOR", "GIS_VISUAL_PROVIDER_MODE",
                "GIS_VISUAL_PROVIDER_TIMEOUT_S"):
        monkeypatch.delenv(key, raising=False)


def _ref():
    return VisualScreenshotRef(
        ref="vshot-" + "b" * 64, sha256="b" * 64, size=2048,
        width=480, height=360, mapspec_revision=3,
    )


def _obs(mode=None, screenshot="ref"):
    return VisualObservationInput(
        trigger="finalization", session_id="s-test", mapspec_revision=3,
        mapspec_fingerprint="carto-sha256:abc",
        screenshot=_ref() if screenshot == "ref" else None,
    )


# ── 1. 默认关 = 零行为变化 ─────────────────────────────────────────────────

def test_seam_unconfigured_is_inert(monkeypatch):
    monkeypatch.delenv("GIS_VISUAL_EVALUATOR", raising=False)
    assert get_visual_evaluator() is None
    assert _maybe_run_visual_evaluation({}, None, []) == []


def test_seam_configured_routes_to_production_provider(monkeypatch):
    monkeypatch.setenv(
        "GIS_VISUAL_EVALUATOR",
        "app.services.gis_harness.visual_observation.provider:evaluate",
    )
    evaluator = get_visual_evaluator()
    assert evaluator is not None
    # 无截图（rules_only 缺省）→ 诚实缺席：空 findings，不抛。
    out = _maybe_run_visual_evaluation({}, None, [], session_id="s1")
    assert out == []


# ── 2. rules-only 模式（像素判据）─────────────────────────────────────────

def test_rules_mode_requires_screenshot():
    res = vp.evaluate_observation(_obs(screenshot=None))
    assert res.status == "not_evaluated"
    assert res.reason == "no_screenshot"
    assert res.findings == ()


def test_rules_mode_unresolvable_ref_is_honest(monkeypatch):
    monkeypatch.setattr(vp, "resolve_visual_screenshot", lambda e: None)
    res = vp.evaluate_observation(_obs())
    assert res.status == "not_evaluated"
    assert res.reason == "screenshot_unresolvable"
    assert res.screenshot_sha256 == "b" * 64


def test_rules_mode_blank_canvas_yields_empty_space(monkeypatch):
    from PIL import Image

    import io

    buf = io.BytesIO()
    Image.new("RGB", (200, 150), (255, 255, 255)).save(buf, "PNG")
    data = buf.getvalue()
    monkeypatch.setattr(vp, "resolve_visual_screenshot", lambda e: data)
    res = vp.evaluate_observation(_obs())
    assert res.status == "evaluated"
    codes = {f.code for f in res.findings}
    assert "visual_empty_space" in codes
    assert all(f.degradation_only for f in res.findings)
    assert all(f.blocks_completion is False for f in res.findings)
    assert res.taxonomy_counts.get("empty_space") >= 1


def test_rules_mode_never_claims_geometry_or_semantics(monkeypatch):
    """W9 边界：布局/语义类是确定性 verifier 领地，rules-half 永不越界。"""
    from PIL import Image

    import io

    buf = io.BytesIO()
    Image.new("RGB", (120, 120), (10, 10, 10)).save(buf, "PNG")
    monkeypatch.setattr(vp, "resolve_visual_screenshot", lambda e: buf.getvalue())
    res = vp.evaluate_observation(_obs())
    forbidden = {"visual_label_collision", "visual_legend_mismatch",
                 "visual_crop", "visual_overlap"}
    assert not forbidden & {f.code for f in res.findings}


# ── 3. vlm 模式（fail-closed 矩阵）────────────────────────────────────────

def test_vlm_mode_without_key_is_not_evaluated(monkeypatch):
    monkeypatch.setenv("GIS_VISUAL_PROVIDER_MODE", "vlm")
    from app.lib.harness.visual_judge.golden_images import render_golden_image

    monkeypatch.setattr(vp, "resolve_visual_screenshot",
                        lambda e: render_golden_image("clean_balanced_map"))
    res = vp.evaluate_observation(_obs())
    assert res.status == "not_evaluated"
    assert res.reason in ("no_api_key", "not_configured")
    assert res.findings == ()


def _fake_engine(monkeypatch, report):
    class _Engine:
        async def evaluate(self, **kwargs):
            return report

    monkeypatch.setattr(
        "app.lib.harness.visual_judge.critic_engine.build_critic_engine",
        lambda: _Engine(),
    )


def test_vlm_mode_evaluated_report_maps_through_taxonomy(monkeypatch):
    from app.lib.harness.visual_judge.contracts import (
        VisualCritiqueItem,
        VisualDimension,
        VisualDimensionScore,
    )

    critiques = [
        VisualCritiqueItem(dimension=VisualDimension.READABILITY,
                           confidence=0.9, suggestion="注记 overlap 重叠",
                           defect_type="collision"),
        VisualCritiqueItem(dimension=VisualDimension.SPATIAL_ALIGNMENT,
                           confidence=0.8, suggestion="底图 occlud 遮挡"),
    ]
    scores = [
        VisualDimensionScore(dimension=d, score=8.0, confidence=0.5)
        for d in VisualDimension
    ]
    report = VisualJudgeReport(
        status="evaluated", critiques=critiques, dimension_scores=scores,
    )
    _fake_engine(monkeypatch, report)
    monkeypatch.setenv("GIS_VISUAL_PROVIDER_MODE", "vlm")
    monkeypatch.setattr(vp, "resolve_visual_screenshot", lambda e: b"png")
    res = vp.evaluate_observation(_obs())
    assert res.status == "evaluated"
    codes = {f.code for f in res.findings}
    assert codes == {"visual_label_collision", "visual_overlap"}
    assert all(f.domain == "visual" for f in res.findings)
    assert all(f.repair_class == "" for f in res.findings)


def test_vlm_mode_provider_failure_is_fail_closed(monkeypatch):
    report = VisualJudgeReport.skipped("provider_timeout")
    _fake_engine(monkeypatch, report)
    monkeypatch.setenv("GIS_VISUAL_PROVIDER_MODE", "vlm")
    monkeypatch.setattr(vp, "resolve_visual_screenshot", lambda e: b"png")
    res = vp.evaluate_observation(_obs())
    assert res.status == "not_evaluated"
    assert res.reason == "provider_timeout"
    assert res.findings == ()


def test_vlm_mode_wall_clock_timeout(monkeypatch):
    import asyncio

    class _SlowEngine:
        async def evaluate(self, **kwargs):
            await asyncio.sleep(5)
            return VisualJudgeReport.skipped("provider_error")

    monkeypatch.setattr(
        "app.lib.harness.visual_judge.critic_engine.build_critic_engine",
        lambda: _SlowEngine(),
    )
    monkeypatch.setenv("GIS_VISUAL_PROVIDER_MODE", "vlm")
    monkeypatch.setenv("GIS_VISUAL_PROVIDER_TIMEOUT_S", "0.5")
    monkeypatch.setattr(vp, "resolve_visual_screenshot", lambda e: b"png")
    res = vp.evaluate_observation(_obs())
    assert res.status == "not_evaluated"
    assert res.reason == "provider_timeout"


def test_timeout_bound_is_clamped(monkeypatch):
    monkeypatch.setenv("GIS_VISUAL_PROVIDER_TIMEOUT_S", "999")
    assert vp.provider_timeout_s() <= vp._MAX_TIMEOUT_S
    monkeypatch.setenv("GIS_VISUAL_PROVIDER_TIMEOUT_S", "nonsense")
    assert vp.provider_timeout_s() == vp.DEFAULT_TIMEOUT_S


# ── 4. hybrid 融合 + 反翻转 ───────────────────────────────────────────────

def test_hybrid_merges_same_entity_category(monkeypatch):
    from app.lib.harness.visual_judge.contracts import (
        VisualCritiqueItem,
        VisualDimension,
        VisualDimensionScore,
    )

    critiques = [
        VisualCritiqueItem(dimension=VisualDimension.COLOR_DISCRIMINABILITY,
                           confidence=0.9, suggestion="低对比 vlm 证据"),
    ]
    scores = [
        VisualDimensionScore(dimension=d, score=8.0, confidence=0.5)
        for d in VisualDimension
    ]
    _fake_engine(monkeypatch, VisualJudgeReport(
        status="evaluated", critiques=critiques, dimension_scores=scores))
    monkeypatch.setenv("GIS_VISUAL_PROVIDER_MODE", "hybrid")

    from PIL import Image

    import io

    buf = io.BytesIO()
    Image.new("RGB", (200, 200), (255, 255, 255)).save(buf, "PNG")
    monkeypatch.setattr(vp, "resolve_visual_screenshot", lambda e: buf.getvalue())

    res = vp.evaluate_observation(_obs())
    assert res.status == "evaluated"
    codes = [f.code for f in res.findings]
    # 空白画布 rules → empty_space；vlm → contrast；同 (entity=map) 类无重复。
    assert codes.count("visual_contrast") == 1
    assert codes.count("visual_empty_space") == 1
    assert res.provider == "hybrid"


def test_seam_sanitizes_malicious_evaluator_output():
    """seam 白名单二次消毒：非 visual domain / 改图意图在结构上进不来。"""
    def malicious(snapshot):
        return [
            {"code": "layer_missing", "severity": "error",
             "affected_entity": "L1"},                # 试图冒充确定性码
            {"code": "visual_ok", "severity": "warning",
             "mutation": {"opacity": 0.1}},           # 改图意图 → 整条判废
            {"code": "visual_ok", "severity": "error",
             "affected_entity": "map"},               # error 保留（封顶归管线）
        ]

    out = run_visual_evaluation(malicious, {"trigger": "finalization"})
    codes = [f.code for f in out]
    # 域强制 + 意图判废 + degradation-only（code 保真 —— 命名空间归管线披露面）。
    assert "layer_missing" in codes
    assert all(f.domain == "visual" for f in out)
    assert all(f.degradation_only for f in out)
    assert all(f.blocks_completion is False for f in out)
    assert all(not f.retryable for f in out)
    # 意图条目判废：两条 visual_ok 中只有无 mutation 的那条存活。
    assert codes.count("visual_ok") == 1


# ── finalization 级反翻转（真端到端：fake evaluator + run_map_finalization）
# 视觉 error 的两道终锁：不能掩盖 deterministic failure，不能升级非 READY。


def _geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
             "properties": {}},
        ],
    }


@pytest.fixture
async def flip_session():
    import shutil

    from app.services.mapspec.store import BASE_STORAGE_DIR
    from app.services.session_data import session_data_manager

    sid = f"f15flip-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


async def _seed_flip_session(sid: str, *, fatal: bool):
    """种子会话；``fatal=True`` 时章节计划引用不存在的结果层（layer_missing，
    repair=None → 不可修复 deterministic error，终验 failed）。"""
    from app.services.mapspec.lifecycle_engine import (
        InitProjectIntent,
        MapSpecLifecycleEngine,
        UpsertLayerIntent,
    )
    from app.services.session_plan import (
        ensure_session_plan_slot,
        save_session_plan,
    )

    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "base", "source": "s-base", "type": "circle",
                   "paint": {"circle-color": "#00f"}},
            source_data=_geojson(),
        ),
    )
    plan = await ensure_session_plan_slot(sid)
    plan.gis_chapter = {
        "plan_id": "p", "query": "q",
        "data_requirements": [], "analysis_steps": [],
        "map_layers": [{
            "role": "primary",
            "layer_id": "ghost-layer" if fatal else "base",
            "enabled": True,
        }],
        "components": [], "template_selection": {},
    }
    await save_session_plan(plan)


def _error_visual_evaluator():
    """返回 error 级视觉 finding 的 fake evaluator（攻击面输入）。"""
    def _evaluate(snapshot):
        return [{
            "code": "visual_contrast", "severity": "error",
            "affected_entity": "map", "evidence": "fake vlm says error",
        }]

    return _evaluate


@pytest.mark.asyncio
async def test_visual_error_cannot_mask_deterministic_failure(
        flip_session, monkeypatch):
    """deterministic 不可修复 error 在场 → status=failed；视觉 error 只以
    warning 披露，既不掩盖失败也不改写状态方向。"""
    from app.services.gis_harness.completion.contracts import STATUS_FAILED
    from app.services.gis_harness.completion.pipeline import (
        run_map_finalization as _run,
    )
    from app.services.gis_harness import visual_evaluator as _seam

    await _seed_flip_session(flip_session, fatal=True)
    monkeypatch.setattr(_seam, "get_visual_evaluator",
                        lambda: _error_visual_evaluator())
    result = await _run(flip_session)
    visual_rows = [f for f in result.findings
                   if f.code.startswith("visual_")]
    assert visual_rows, "visual findings must be disclosed"
    assert all(f.severity == "warning" for f in visual_rows), (
        "disclosure must stay capped at warning")
    assert result.status == STATUS_FAILED, (
        "deterministic unrepairable error must dominate")
    assert result.product_verdict not in ("READY", "READY_WITH_WARNINGS")


@pytest.mark.asyncio
async def test_visual_error_alone_cannot_upgrade_or_block(
        flip_session, monkeypatch):
    """只有视觉 error（无 deterministic error）→ 不得阻断/不得 produced
    error 披露；完成时 verdict 必须被降档（≠ READY）。"""
    from app.services.gis_harness.completion.contracts import STATUS_COMPLETE
    from app.services.gis_harness.completion.pipeline import (
        run_map_finalization as _run,
    )
    from app.services.gis_harness import visual_evaluator as _seam

    await _seed_flip_session(flip_session, fatal=False)
    monkeypatch.setattr(_seam, "get_visual_evaluator",
                        lambda: _error_visual_evaluator())
    result = await _run(flip_session)
    visual_rows = [f for f in result.findings
                   if f.code.startswith("visual_")]
    assert visual_rows
    assert all(f.severity in ("warning", "info") for f in visual_rows)
    if result.status == STATUS_COMPLETE:
        assert result.product_verdict == "READY_WITH_WARNINGS", (
            "visual error must downgrade READY, never upgrade non-READY")
    else:
        assert result.product_verdict != "READY"


def test_snapshot_includes_revision_and_screenshot_ref():
    snap = _assemble_visual_snapshot(
        {"layers": []}, None, [],
        session_id="s9", mapspec_revision=42, screenshot=_ref(),
    )
    assert snap["session_id"] == "s9"
    assert snap["mapspec_revision"] == 42
    assert snap["screenshot"]["ref"].startswith("vshot-")
    assert snap["screenshot"]["sha256"] == "b" * 64
    raw = str(snap)
    assert "data:image" not in raw and "base64" not in raw.lower()
    # 无截图 → 键存在、值 None（形状稳定）。
    snap2 = _assemble_visual_snapshot({"layers": []}, None, [])
    assert snap2["screenshot"] is None
    assert snap2["session_id"] == ""
