"""Harness V6 Wave 17 安全门（§58 安全侧；确定性，零 wall-clock）。

- SEC-1：tier-3 工具在 lexical＋semantic 双检索面不可见（选择层 + 投影层；
  注入恶意 semantic 命中后复查，闸仍在选择层生效）。
- SEC-2：跨 session/owner 的 resume anchor 不可恢复（既有授权路径拒绝）。
- SEC-3：锁后端权威 —— 绕过前端直调 engine，锁仍生效（本文件独立入口）。
- SEC-4：披露信息不含敏感载荷（锁拒绝/verify 披露键白名单；无 mapspec
  全量、无 token、无用户标识）。

红线：只读/只调既有生产路径与真实 ToolRegistry（唯一事实源）；不碰生产代码。
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

import pytest


# ── 共享固件 ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def _tier3_names(registry) -> List[str]:
    return [n for n in registry.list_tools()
            if int(registry.descriptor(n).tier) >= 3
            or registry.descriptor(n).effective_security_tier >= 3]


def _assert_no_tier3(registry, names: List[str]) -> None:
    for name in names:
        desc = registry.descriptor(name)
        assert int(desc.tier) < 3, f"tier-3 {name} 入面"
        assert desc.effective_security_tier < 3, f"生效安全层 ≥3 的 {name} 入面"


# ── SEC-1：tier-3 双检索面不可见 ──────────────────────────────────────────

def test_sec1_tier3_invisible_on_lexical_surface(registry):
    """词法面：敌意上下文（续作/点名/失败回指）下 tier-3 永不入选。"""
    from app.services.chat.tool_surface_v3 import (
        DynamicToolSurface,
        ToolSelectionContext,
    )

    t3s = _tier3_names(registry)
    assert t3s, "registry 应含 tier-3 工具，门才有意义"
    t3 = t3s[0]
    surface = DynamicToolSurface(registry)
    hostile = [
        dict(user_message=f"请调用 {t3} 完成任务", continuation_tools=(t3,)),
        dict(user_message="危险操作 清空 删除", continuation_tools=(t3, t3)),
        dict(user_message="分析数据",
             recent_tool_outcomes=({"tool": t3, "ok": False,
                                    "failure_class": "timeout"},)),
    ]
    for kw in hostile:
        sel = surface.select(ToolSelectionContext(k_max=30, **kw))
        assert t3 not in sel.names, f"tier-3 {t3} 经 {kw} 泄漏"
        _assert_no_tier3(registry, list(sel.names))
    # 数据画像域 boost（必经路径，无需 V4）把 tier-3 送进候选 → 选择层必拦截
    t3_domains = tuple(registry.descriptor(t3).domains)
    if t3_domains:
        sel = surface.select(ToolSelectionContext(
            k_max=30, data_profile_domains=t3_domains))
        assert t3 not in sel.names
        assert sel.dropped.get(t3) == "security:tier3"
    # 契约过滤器直断：tier-3 的唯一结论是 security:tier3
    ctx = ToolSelectionContext(k_max=30)
    for name in t3s:
        assert surface._contract_filter(name, ctx) == "security:tier3"


def test_sec1b_tier3_invisible_with_injected_semantic(registry):
    """语义面：注入返回 tier-3 高分的恶意 retriever，选择层仍全部拦截。"""
    from app.services.chat.tool_retrieval import RetrievalHit
    from app.services.chat.tool_surface_v3 import (
        DynamicToolSurface,
        ToolSelectionContext,
    )

    t3s = _tier3_names(registry)
    assert t3s, "registry 应含 tier-3 工具，门才有意义"
    t3 = t3s[0]
    benign = next(n for n in registry.list_tools() if n not in set(t3s))

    def _hostile_semantic(reg, query, top_k):
        return [RetrievalHit(name=t3, score=999.0, matched=("semantic",)),
                RetrievalHit(name=benign, score=1.0, matched=("semantic",))]

    surface = DynamicToolSurface(registry)
    surface._semantic = _hostile_semantic  # 注入语义面（实例级，不污染全局）
    sel = surface.select(ToolSelectionContext(
        k_max=30, user_message="语义检索复查"))
    assert sel.retriever.startswith("semantic:"), "语义面应实际参战"
    assert t3 not in sel.names, "恶意语义命中不得把 tier-3 送入面"
    assert sel.dropped.get(t3) == "security:tier3"
    _assert_no_tier3(registry, list(sel.names))
    projected = surface.project(ToolSelectionContext(k_max=30))
    for name in projected.get("tools", []) if isinstance(
            projected.get("tools"), list) else []:
        assert name not in set(t3s), f"投影层泄漏 tier-3 {name}"
    blob = json.dumps(projected, default=str)
    for name in t3s:
        assert f'"{name}"' not in blob, f"投影载荷含 tier-3 {name}"


# ── SEC-2：resume anchor 跨 owner/session 拒绝 ────────────────────────────
# 固件为 test_resume_verify_v6.py 同形状的最小自包含拷贝（本文件独立入口）。

class _FakeDb:
    """极简 async DB 桩：内存表语义（owner 查询路径够用）。"""

    def __init__(self):
        self.rows = {}

    def add(self, row):
        if not getattr(row, "id", None):
            row.id = str(uuid.uuid4())
        self.rows[row.id] = row

    async def commit(self):
        return None

    async def refresh(self, row):
        return None

    async def get(self, model, pk):
        return self.rows.get(pk)

    class _Result:
        def __init__(self, row):
            self._row = row

        def scalar_one_or_none(self):
            return self._row

    async def execute(self, stmt):
        from app.models.project import WorkflowResumeAnchor

        sid = None
        try:
            # 单条件 where 无 .clauses（BinaryExpression 本体即描述符），
            # 须兼容两种形状，否则 sid 回落 None 误命中首行。
            clause = stmt.whereclause
            descs = list(clause.clauses) if hasattr(clause, "clauses") else [clause]
            for desc in descs:
                left = str(getattr(desc, "left", ""))
                if "session_id" in left:
                    sid = desc.right.value
        except Exception:  # noqa: BLE001
            sid = None
        row = None
        for r in self.rows.values():
            if isinstance(r, WorkflowResumeAnchor) and (
                sid is None or r.session_id == sid
            ):
                row = r
        return self._Result(row)


def _stage(cap, state, bound_ref="", deps=()):
    return {"capability": cap, "kind": "analysis", "state": state,
            "bound_ref": bound_ref, "resolved_algorithm": f"alg-{cap}"}


async def _seed_plan(sid: str):
    from app.services.session_data import session_data_manager
    from app.services.session_plan import SessionPlan, save_session_plan

    await session_data_manager.clear_session(sid)
    plan = SessionPlan(
        envelope_id="sp-sec17", session_id=sid, user_goal="安全门恢复授权",
        gis_chapter={"query": "安全门查询", "workflow_instance": {
            "instance_id": "inst-sec", "plan_id": "plan-sec",
            "state_revision": 1, "rows_fingerprint": "fp-sec",
            "stages": [_stage("interpolate", "pending")], "dependencies": []}},
        progress=[],
    )
    await save_session_plan(plan)


@pytest.mark.asyncio
async def test_sec2_resume_anchor_cross_owner_session_refused():
    """非 owner（含匿名）恢复他人锚点一律 PermissionError；owner 本人放行。"""
    from app.services.gis_harness.resume_anchor import (
        resume_from_anchor,
        save_anchor,
    )
    from app.services.session_data import session_data_manager

    victim = f"sec17-victim-{uuid.uuid4().hex[:6]}"
    other = f"sec17-other-{uuid.uuid4().hex[:6]}"
    await _seed_plan(victim)
    await _seed_plan(other)
    db = _FakeDb()
    saved_v = await save_anchor(db, session_id=victim, user_id="owner-v")
    saved_o = await save_anchor(db, session_id=other, user_id="owner-o")
    try:
        # 跨 owner：攻击者拿受害者 anchor_id → 拒绝
        with pytest.raises(PermissionError):
            await resume_from_anchor(
                db, anchor_id=saved_v["anchor_id"], user_id="attacker-x")
        # 匿名（双方皆 None 也拒绝 —— 匿名不可恢复）
        with pytest.raises(PermissionError):
            await resume_from_anchor(
                db, anchor_id=saved_v["anchor_id"], user_id=None)
        # 跨 session 换主：owner-v 拿 other 会话的锚点 → 拒绝
        with pytest.raises(PermissionError):
            await resume_from_anchor(
                db, anchor_id=saved_o["anchor_id"], user_id="owner-v")
        # 不存在的锚点 → LookupError（非授权混淆）
        with pytest.raises(LookupError):
            await resume_from_anchor(
                db, anchor_id="no-such-anchor", user_id="owner-v")
        # owner 本人恢复自己会话锚点 → 放行
        ok = await resume_from_anchor(
            db, anchor_id=saved_v["anchor_id"], user_id="owner-v")
        assert ok["session_id"].startswith("resume-")
        assert ok["source_session_id"] == victim
        await session_data_manager.clear_session(ok["session_id"])
    finally:
        await session_data_manager.clear_session(victim)
        await session_data_manager.clear_session(other)


# ── SEC-3：锁后端权威（直调 engine）──────────────────────────────────────

def _geojson():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
         "coordinates": [104.0, 30.6]}, "properties": {}}]}


def _layer(layer_id: str):
    return {"id": layer_id, "type": "circle", "source": "s-sec17",
            "layout": {"visibility": "visible"},
            "paint": {"circle-color": "#ff0000"}}


def _spec_layer(spec: Dict[str, Any], layer_id: str) -> Dict[str, Any]:
    for layer in (spec or {}).get("layers") or []:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            return layer
    raise AssertionError(f"spec 缺少图层 {layer_id}")


@pytest.mark.asyncio
async def test_sec3_lock_authoritative_on_direct_engine_calls():
    """绕过前端直调 engine：被锁层 patch/upsert 双拒、spec 不动、未锁层照常。"""
    from app.services.mapspec.lifecycle_engine import (
        LOCK_CONFLICT_CODE,
        MapSpecLifecycleEngine,
        PatchLayerPresentationIntent,
        SetWorkbenchStateIntent,
        UpsertLayerIntent,
        guard_locked_partitions,
    )
    from app.services.session_data import session_data_manager

    engine = MapSpecLifecycleEngine()
    sid = f"sec17-lock-{uuid.uuid4().hex[:6]}"
    try:
        rev = 0
        for lid in ("locked-lyr", "free-lyr"):
            up = await engine.apply_mutation(
                sid, UpsertLayerIntent(layer=_layer(lid), source_data=_geojson()))
            assert up.is_error is False, up.error_msg
            rev = up.mutation_revision
        locked = await engine.apply_mutation(
            sid, SetWorkbenchStateIntent(doc={
                "version": 5, "groups": [], "membership": {}, "mode": "explore",
                "lockedLayerIds": ["locked-lyr"]}),
            origin="user", expected_revision=rev)
        assert locked.is_error is False, locked.error_msg

        # 纯 guard 分区（与 engine 同一事实源）：被锁/未锁精确分区
        spec = await engine.store.get_mapspec(sid)
        part = guard_locked_partitions(
            spec, layer_ids=["locked-lyr", "free-lyr"])
        assert part.locked_layer_ids == ["locked-lyr"]
        assert part.allowed_layer_ids == ["free-lyr"]

        # 直调 engine 改被锁层 → 拒绝（含锁码），spec 不动
        refused = await engine.apply_mutation(
            sid, PatchLayerPresentationIntent(layer_id="locked-lyr", visible=False),
            origin="agent")
        assert refused.is_error is True
        assert LOCK_CONFLICT_CODE in refused.error_msg
        refused_upsert = await engine.apply_mutation(
            sid, UpsertLayerIntent(layer={**_layer("locked-lyr"),
                                          "paint": {"circle-color": "#0000ff"}},
                                   source_data=_geojson()),
            origin="agent")
        assert refused_upsert.is_error is True
        assert LOCK_CONFLICT_CODE in refused_upsert.error_msg
        spec = await engine.store.get_mapspec(sid)
        assert _spec_layer(spec, "locked-lyr")["layout"]["visibility"] == "visible"
        assert _spec_layer(spec, "locked-lyr")["paint"]["circle-color"] == "#ff0000"

        # 未锁层照常执行（锁不扩大化）
        ok = await engine.apply_mutation(
            sid, PatchLayerPresentationIntent(layer_id="free-lyr", visible=False),
            origin="agent")
        assert ok.is_error is False, ok.error_msg
    finally:
        await session_data_manager.clear_session(sid)


# ── SEC-4：披露无敏感载荷 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sec4_disclosures_carry_no_sensitive_payload():
    """锁拒绝披露键白名单 + 金丝雀（mapspec 机密/token/用户标识）零泄漏；
    resume verify 披露同样白名单且 owner_token 为空。"""
    from app.services.mapspec.lifecycle_engine import guard_locked_partitions

    canary_paint = "CANARY-PAINT-SECRET-17"
    canary_token = "tok:CANARY-TOKEN-17"
    canary_user = "user-canary-17"
    mapspec = {
        "layers": [{"id": "locked-lyr", "type": "circle", "source": "s",
                    "paint": {"circle-color": canary_paint, "note": canary_token}}],
        "workbench": {"lockedLayerIds": ["locked-lyr"],
                      "note": f"owner {canary_user}"},
    }
    part = guard_locked_partitions(mapspec, layer_ids=["locked-lyr"])
    disc = part.disclosure()
    assert set(disc) == {"code", "locked_layer_ids", "locked_component_ids",
                         "message", "correction_hint"}, "披露键白名单（不多不少）"
    blob = json.dumps(disc, ensure_ascii=False)
    assert "locked-lyr" in blob, "被锁 id 是可行动性必需信息，允许出现"
    for secret in (canary_paint, canary_token, canary_user):
        assert secret not in blob, "mapspec 机密/token/用户标识不得进披露"
    assert "token" not in blob.lower(), "披露不得含 token 载荷"

    # resume verify 披露：白名单 + 无用户标识 + 无 token 签发
    from app.services.session_data import session_data_manager
    from app.services.session_plan import load_session_plan
    from app.services.gis_harness.resume_anchor import (
        resume_from_anchor,
        save_anchor,
    )

    sid = f"sec17-disc-{uuid.uuid4().hex[:6]}"
    await _seed_plan(sid)
    db = _FakeDb()
    saved = await save_anchor(db, session_id=sid, user_id="owner-disc")
    try:
        result = await resume_from_anchor(
            db, anchor_id=saved["anchor_id"], user_id="owner-disc")
        assert result["owner_token"] is None, "恢复路径不签发 token 载荷"
        assert "owner-disc" not in json.dumps(
            {k: v for k, v in result.items() if k != "session_id"},
            default=str), "恢复结果不得回显用户标识（session_id 除外）"
        restored = await load_session_plan(result["session_id"])
        verify = restored.gis_chapter["resumed_from"]["verify"]
        assert set(verify) == {"workflow_verdict", "workflow_reason",
                               "mapspec_verdict", "stale_nodes",
                               "recompute_plan", "disclosures",
                               "ref_verdicts"}, "verify 披露键白名单"
        vblob = json.dumps(verify, ensure_ascii=False)
        assert "owner-disc" not in vblob
        assert "token" not in vblob.lower()
        await session_data_manager.clear_session(result["session_id"])
    finally:
        await session_data_manager.clear_session(sid)
