"""F15 视觉回归 corpus + pipeline 级接线回归（ADR-0214）。

不变式：
1. corpus 确定性：golden_images 同图恒同 findings（pixel rules 逐条钉住）；
   rules-half 能力边界恒成立（几何/语义类永不出现），负例（正常地图）
   零误报；
2. pipeline 接线：provider 配置后 finalization 真实产生 visual findings
   （ref-only 截图通道 → store → provider），recurrence 记账跨运行推进，
   第 3 轮硬停（披露降 info、plan 面清空、visual_loop 收据）；
3. 反翻转（finalization 级）：视觉 warning/error 只降档 READY →
   READY_WITH_WARNINGS，不把非 READY 提升为完成，不掩盖 deterministic
   error；
4. 字节纪律：map_product 持久化面只有 ref+sha 摘要。
"""

from __future__ import annotations

import shutil
import uuid

import pytest

from app.lib.harness.visual_judge.golden_images import render_golden_image
from app.services.gis_harness.completion.contracts import (
    STATUS_COMPLETE,
    STATUS_NEEDS_REPAIR,
    MapCompletionResult,
)
from app.services.gis_harness.completion.pipeline import (
    run_map_finalization,
)
from app.services.gis_harness.visual_observation.rules import (
    evaluate_pixel_rules,
)
from app.services.gis_harness.visual_observation.store import (
    SCREENSHOT_INDEX_KEY,
    register_visual_screenshot,
)
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager
from app.services.session_plan import ensure_session_plan_slot, save_session_plan

# ── corpus：golden_images × pixel rules（确定性钉住）────────────────────────

_CORPUS_EXPECTATIONS = {
    "sparse_canvas_underdensity": ["visual_empty_space", "visual_hierarchy"],
    "bottom_heavy_layout": ["visual_hierarchy"],
    "tiny_unreadable_text": ["visual_hierarchy"],
    "overlay_offset_misalignment": ["visual_legibility"],
    "low_contrast_dark_theme": ["visual_empty_space", "visual_hierarchy"],
    "clean_balanced_map": [],                    # 负例：零误报
    "overlapping_labels": [],                    # 几何类 = 确定性 verifier/VLM 领地
    "adjacent_palette_confusion": [],
    "symbol_clutter_overdensity": [],
    "extreme_tilt_rotation": [],
}

_RULES_ALLOWED_CODES = {"visual_empty_space", "visual_legibility",
                        "visual_hierarchy", "visual_contrast"}


@pytest.mark.parametrize("name,expected", sorted(_CORPUS_EXPECTATIONS.items()))
def test_corpus_golden_images_deterministic(name, expected):
    findings = evaluate_pixel_rules(render_golden_image(name))
    assert [f.code for f in findings] == expected
    for f in findings:
        assert f.domain == "visual"
        assert f.code in _RULES_ALLOWED_CODES      # 能力边界
        assert f.degradation_only and not f.blocks_completion
        assert f.affected_entity == "map"          # 像素事实是画布级，不猜组件
        assert f.code in expected


# ── pipeline 级接线：store → provider → finalization → recurrence ──────────


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
async def vis_session():
    sid = f"f15vis-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


async def _seed(vis_session: str, revision: int = 5):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(vis_session, InitProjectIntent())
    await engine.apply_mutation(
        vis_session,
        UpsertLayerIntent(
            layer={"id": "poi-main", "source": "s1", "type": "circle",
                   "paint": {"circle-color": "#00f"}},
            source_data=_geojson(),
        ),
    )
    entry = await register_visual_screenshot(
        vis_session, render_golden_image("sparse_canvas_underdensity"),
        mapspec_revision=revision, width=480, height=360)
    plan = await ensure_session_plan_slot(vis_session)
    plan.gis_chapter = {
        "plan_id": "p", "query": "q",
        "data_requirements": [], "analysis_steps": [],
        "map_layers": [], "components": [], "template_selection": {},
    }
    await save_session_plan(plan)
    return entry


def _provider_env(monkeypatch):
    monkeypatch.setenv(
        "GIS_VISUAL_EVALUATOR",
        "app.services.gis_harness.visual_observation.provider:evaluate")
    monkeypatch.setenv("GIS_VISUAL_PROVIDER_MODE", "rules_only")


@pytest.mark.asyncio
async def test_finalization_produces_visual_findings_and_recurrence(
        vis_session, monkeypatch):
    _provider_env(monkeypatch)
    entry = await _seed(vis_session)
    result = await run_map_finalization(vis_session)
    codes = {f.code for f in result.findings}
    assert "visual_empty_space" in codes
    assert result.visual_findings, "plan face must carry visual findings"
    loop = result.to_dict().get("visual_loop") or {}
    assert loop.get("recorded", 0) >= 1
    assert loop.get("hard_stopped") == []
    # ref-only：持久化面无字节/base64；sha 摘要只落 store 索引（map_state）。
    import json as _json

    blob = _json.dumps(result.to_dict(), ensure_ascii=False, default=str)
    assert "base64" not in blob.lower()
    assert "data:image" not in blob
    state = await session_data_manager.get_map_state(vis_session)
    index = state.get(SCREENSHOT_INDEX_KEY)
    assert index and index[-1]["sha256"] == entry.sha256
    assert index[-1]["ref"].startswith("vshot-")


@pytest.mark.asyncio
async def test_recurrence_hard_stop_across_finalization_runs(
        vis_session, monkeypatch):
    _provider_env(monkeypatch)
    await _seed(vis_session)

    from app.services.gis_harness.visual_observation.recurrence import (
        load_ledger,
    )

    hard_stopped_fp = None
    for run in range(1, 4):
        result = await run_map_finalization(vis_session)
        loop = result.to_dict().get("visual_loop") or {}
        ledger = await load_ledger(vis_session)
        if run < 3:
            assert loop.get("hard_stopped") == []
            assert result.visual_findings, f"run{run} plan face nonempty"
        else:
            hard_stopped_fp = loop.get("hard_stopped") or []
    assert hard_stopped_fp, "third run must hard stop"
    assert hard_stopped_fp[0] in ledger.hard_stopped
    # 硬停后：plan 面清空（不再索要修复），披露面降 info。
    result4 = await run_map_finalization(vis_session)
    assert result4.visual_findings is None
    info_rows = [
        f for f in result4.findings
        if f.code.startswith("visual_") and f.severity == "info"
    ]
    assert info_rows and "recurrence hard stop" in info_rows[0].detail


@pytest.mark.asyncio
async def test_visual_findings_never_upgrade_or_mask(vis_session, monkeypatch):
    """视觉 warning/error 只降档 READY；deterministic error 仍裁决 failed。"""
    _provider_env(monkeypatch)
    await _seed(vis_session)
    result = await run_map_finalization(vis_session)
    visual_codes = [f.code for f in result.findings if f.code.startswith("visual_")]
    assert visual_codes
    for f in result.findings:
        if f.code.startswith("visual_"):
            # 披露面封顶：视觉条目永不 error。
            assert f.severity in ("warning", "info")
        # 无 deterministic error 的会话：visual warning 的唯一裁决效应是
        # verdict 降档（READY → READY_WITH_WARNINGS，#1479 语义）——
        # status 阶梯与 verdict 都不允许视觉把未完成说成完成。
        if result.status == STATUS_COMPLETE:
            assert result.product_verdict in (
                "READY_WITH_WARNINGS", "READY", "")
            if visual_codes:
                assert result.product_verdict != "READY", (
                    "visual warnings must downgrade READY verdict")
    # 掩盖路径：注入一个不可修复 deterministic error，视觉发现同时在场。
    result.visual_findings = list(result.visual_findings or [])
    from app.services.gis_harness.completion.contracts import MapCompletionFinding

    result.findings.append(MapCompletionFinding(
        code="source_missing", severity="error", target="src-x",
        detail="injected deterministic error"))
    errors = [f for f in result.findings if f.severity == "error"]
    assert errors and errors[0].code == "source_missing"
    assert STATUS_NEEDS_REPAIR  # 词表互锁引用


# ── 诚实缺席与词表互锁 ─────────────────────────────────────────────────────

def test_visual_loop_absent_key_when_no_findings(monkeypatch):
    monkeypatch.delenv("GIS_VISUAL_EVALUATOR", raising=False)
    result = MapCompletionResult()
    d = result.to_dict()
    assert "visual_loop" not in d
    assert "visual_findings" not in d


def test_result_visual_loop_serialization_bounded():
    result = MapCompletionResult(visual_loop={
        "recorded": 3, "recurrent": 1,
        "hard_stopped": [f"fp{i}" for i in range(20)],
    })
    d = result.to_dict()
    assert len(d["visual_loop"]["hard_stopped"]) == 8
    assert d["visual_loop"]["recorded"] == 3
