"""IntentAcceptance + DisplayConfirmation + finalizer V7 出口（ADR-0130 D6）。

不变式：
1. 意图验收独立三面核对（verdict / desired / observed）—— 打破
   intent_verified=(status==complete) 的循环论证；
2. observation 缺席 → intent_verified=False（诚实未知，不再自证晋级
   semantically_correct）；accepted 只看 verdict+desired；
3. display confirmation：默认 auto（True，零行为变化）；required 模式
   依赖显式 ack（render_seq 代次比对，陈旧 ack 不算确认）；
4. finalizer continuation：needs_repair/failed → decide_continuation；
   repair 预算尽 → request_replan 驱动（预算记账持久）；READY → commit
   标记 + 九域 checkpoint 落账。
"""
from __future__ import annotations

import shutil
import uuid
from typing import Any, Dict

import pytest

from app.services.gis_harness.display_confirmation import (
    display_mode,
    is_display_confirmed,
    record_display_ack,
)
from app.services.gis_harness.intent_acceptance import (
    assess_intent_acceptance,
    derive_intent_requirements,
)


def _chapter() -> Dict[str, Any]:
    return {
        "plan_id": "planF",
        "query": "成都学校分布专业地图",
        "map_layers": [
            {"layer_id": "layer-schools", "role": "primary"},
            {"layer_id": "layer-admin", "role": "secondary"},
        ],
    }


def _mapspec() -> Dict[str, Any]:
    return {
        "layers": [
            {"id": "layer-schools", "visible": True},
            {"id": "layer-admin", "visible": True},
        ],
    }


def _observation() -> Dict[str, Any]:
    return {
        "layers": {
            "layer-schools": {"mounted": True, "visible": True,
                              "feature_count": 5, "render_complete": True},
            "layer-admin": {"mounted": True, "visible": True,
                            "feature_count": 2, "render_complete": True},
        },
    }


# ── 需求派生 ─────────────────────────────────────────────────────────────


def test_requirements_derived_bounded():
    req = derive_intent_requirements(_chapter())
    assert req["layers"] == ["layer-schools", "layer-admin"]
    assert req["roles"] == ["primary", "secondary"]
    empty = derive_intent_requirements({})
    assert empty["layers"] == []


# ── 验收判定 ─────────────────────────────────────────────────────────────


def test_acceptance_full_path_verified():
    r = assess_intent_acceptance(
        _chapter(), _mapspec(), _observation(), product_verdict="READY")
    assert r["accepted"] is True
    assert r["intent_verified"] is True
    assert r["observed_confirmed"] is True
    assert r["unmet"] == []


def test_acceptance_honest_without_observation():
    """V7 诚实收紧：observation 缺席 → intent_verified=False（V6 会自证 True）。"""
    r = assess_intent_acceptance(
        _chapter(), _mapspec(), None, product_verdict="READY")
    assert r["accepted"] is True          # desired 面满足
    assert r["intent_verified"] is False  # 但渲染证据未证实
    assert not any(u.startswith("layer_not") for u in r["unmet"])  # 层无缺口
    assert r["observed_confirmed"] is False


def test_acceptance_rejects_verdict_and_spec_gaps():
    # verdict 不 READY
    r = assess_intent_acceptance(
        _chapter(), _mapspec(), _observation(), product_verdict="NEEDS_REPAIR")
    assert r["accepted"] is False
    assert any(u.startswith("verdict:") for u in r["unmet"])
    # 层不在 spec
    spec = _mapspec()
    spec["layers"] = spec["layers"][:1]
    r2 = assess_intent_acceptance(
        _chapter(), spec, None, product_verdict="READY")
    assert r2["accepted"] is False
    assert any("layer_not_in_spec" in u for u in r2["unmet"])
    # spec 层隐藏 → user-wins：不阻断验收，仅披露（评审 F4）
    spec2 = _mapspec()
    spec2["layers"][1]["visible"] = False
    r3 = assess_intent_acceptance(
        _chapter(), spec2, None, product_verdict="READY")
    assert r3["accepted"] is True
    assert r3["unmet"] == []
    assert any("layer_hidden_by_user" in d for d in r3["disclosures"])
    # 用户隐藏层不做 observed 核对（观察同样不可见也不降级）
    obs_hidden = _observation()
    obs_hidden["layers"]["layer-admin"]["visible"] = False
    r4 = assess_intent_acceptance(
        _chapter(), spec2, obs_hidden, product_verdict="READY")
    assert r4["intent_verified"] is True


def test_acceptance_observed_gaps():
    obs = _observation()
    obs["layers"]["layer-admin"] = {"mounted": True, "visible": False}
    r = assess_intent_acceptance(
        _chapter(), _mapspec(), obs, product_verdict="READY")
    assert r["intent_verified"] is False
    assert any("layer_not_visible_observed" in u for u in r["unmet"])
    # 观察缺层
    obs2 = _observation()
    del obs2["layers"]["layer-admin"]
    r2 = assess_intent_acceptance(
        _chapter(), _mapspec(), obs2, product_verdict="READY")
    assert r2["intent_verified"] is False
    # 观察层集合与需求完全不相交
    obs3 = {"layers": {"other": {"mounted": True}}}
    r3 = assess_intent_acceptance(
        _chapter(), _mapspec(), obs3, product_verdict="READY")
    assert r3["intent_verified"] is False
    # 空章节
    assert assess_intent_acceptance(None)["accepted"] is False


# ── display confirmation ─────────────────────────────────────────────────


def test_display_mode_default_and_required(monkeypatch):
    monkeypatch.delenv("GIS_FINAL_DISPLAY_CONFIRM", raising=False)
    assert display_mode() == "auto"
    monkeypatch.setenv("GIS_FINAL_DISPLAY_CONFIRM", "required")
    assert display_mode() == "required"
    monkeypatch.setenv("GIS_FINAL_DISPLAY_CONFIRM", "nonsense")
    assert display_mode() == "auto"  # 未知值按 auto


async def test_display_confirmation_auto_and_required(monkeypatch, tmp_path):
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    monkeypatch.delenv("GIS_FINAL_DISPLAY_CONFIRM", raising=False)
    sid = f"dc-{uuid.uuid4().hex[:8]}"
    try:
        assert await is_display_confirmed(sid, render_seq=3) is True  # auto
        monkeypatch.setenv("GIS_FINAL_DISPLAY_CONFIRM", "required")
        assert await is_display_confirmed(sid, render_seq=3) is False  # 未 ack
        await record_display_ack(sid, render_seq=5)
        assert await is_display_confirmed(sid, render_seq=3) is True
        assert await is_display_confirmed(sid, render_seq=5) is True
        # 陈旧 ack（ack 代次 < 当前观察代次）→ 不算确认
        assert await is_display_confirmed(sid, render_seq=6) is False
    finally:
        from app.services.session_data import session_data_manager

        await session_data_manager.clear_session(sid)


# ── finalizer 集成（真实 session store）─────────────────────────────────


@pytest.fixture()
async def clean_session():
    sid = f"fin7-{uuid.uuid4().hex[:8]}"
    from app.services.session_data import session_data_manager

    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    try:
        from app.lib.data.large_data import BASE_STORAGE_DIR

        d = BASE_STORAGE_DIR / sid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _finalizable_chapter() -> Dict[str, Any]:
    rows = [
        {"capability": "cap_a", "purpose": "a", "status": "complete",
         "bound_ref": "ref:a", "resolved_algorithm": "alg.a",
         "resolved_tool": "t_a", "depends_on": [], "params": {}},
        {"capability": "cap_b", "purpose": "b", "status": "complete",
         "bound_ref": "ref:b", "resolved_algorithm": "alg.b",
         "resolved_tool": "t_b", "depends_on": ["cap_a"], "optional": False},
    ]
    return {
        "plan_id": "planF2",
        "recipe_id": "",
        "query": "成都学校分布",
        "status": "finalized",
        "data_requirements": rows,
        "analysis_steps": [],
        "map_layers": [],
        "components": [],
    }


async def _save_plan(sid: str, chapter: Dict[str, Any]) -> None:
    from app.services.session_plan import SessionPlan, save_session_plan

    await save_session_plan(SessionPlan(
        envelope_id=f"env-{chapter.get('plan_id')}",
        session_id=sid,
        user_goal=str(chapter.get("query") or "goal"),
        gis_chapter=chapter,
    ))


@pytest.mark.asyncio
async def test_finalize_persists_acceptance_and_commit(clean_session):
    """终验落块带 intent_acceptance；complete → commit 标记 + 阶段推进。"""
    from app.services.gis_harness.map_completion import maybe_finalize_map_product
    from app.services.session_plan import load_session_plan

    ch = _finalizable_chapter()
    await _save_plan(clean_session, ch)
    result = await maybe_finalize_map_product(clean_session, reason="test")
    assert result is not None
    plan = await load_session_plan(clean_session)
    block = plan.gis_chapter.get("map_product") or {}
    assert "intent_acceptance" in block  # V7 验收面持久化
    # F1 修复钉死：生产块必须携带 task_complete（否则 committed/commit
    # 全链死代码 —— 独立评审 CRITICAL 1 的回归锁）
    assert "task_complete" in block
    assert block["task_complete"] in (True, False)
    # 最小章节（无 spec/观察）终验 failed → continuation 裁决面持久化
    assert "continuation" in block
    assert block["continuation"]["verdict"] == "remediate_and_retry"
    # committed 链由 test_committed_reachable_via_real_map_product_block
    # 与 test_abort_gate_excludes_replan_budget 钉死（生产触发面）。


@pytest.mark.asyncio
async def test_finalizer_continuation_verdicts(clean_session, monkeypatch, tmp_path):
    """终验出口 continuation：预算内 → repair 裁决；预算尽 → abort +
    replan 驱动点置 pending（V6 follow-up 的生产闭环）。"""
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    from app.services.gis_harness.completion.pipeline import _finalizer_continuation
    from app.services.gis_harness.completion.contracts import MapCompletionResult
    from app.services.gis_harness.durable_context import update_recovery_state

    await _save_plan(clean_session, _finalizable_chapter())
    result = MapCompletionResult()
    result.status = "needs_repair"
    result.render_status = "stale"
    # 新鲜 recovery → repair 预算有余 → remediate_and_retry
    payload = await _finalizer_continuation(
        clean_session, result, _finalizable_chapter())
    assert payload["verdict"] == "remediate_and_retry"
    # 耗尽 deepen/requalify/repair（replan 不参与 abort 门槛 —— 评审 F2）
    # → 确定性 abort + replan 驱动点置 pending（逃生舱语义回归锁）
    for loop in ("deepen", "requalify", "repair", "deepen", "requalify", "repair"):
        await update_recovery_state(clean_session, loop=loop, detail="x")
    payload2 = await _finalizer_continuation(
        clean_session, result, _finalizable_chapter())
    assert payload2["verdict"] == "abort_with_disclosure"
    # replan 路由结果挂子对象（不覆盖 abort 裁决），pending 置位
    assert payload2["replan"]["replan_pending"] is True
