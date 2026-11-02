"""scale_profile.v1 服务测试（M7）—— 真实引擎种子，无 mock。

链路：UpsertLayer(inlineData) 播种（自动成 ref）→ inspect（证据）→
preview（零突变）→ apply commit（新 ref 工件 + MapSpec 落笔 + 回执幂等）。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.gis_harness.scale_profile import (
    apply_scale_representation,
    inspect_map_scale_profile,
    preview_scale_representation,
)
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine
from app.services.mapspec.store import mapspec_store_instance
from app.services.session_data import session_data_manager


def _roads_fc() -> dict:
    feats = []
    for cls, n in [("高速公路", 6), ("国道", 20), ("省道", 40), ("县道", 80), ("乡道", 120)]:
        for i in range(n):
            feats.append({
                "type": "Feature", "id": f"{cls}-{i}",
                "properties": {"道路等级": cls, "name": f"{cls}{i}"},
                "geometry": {"type": "LineString", "coordinates": [
                    [104.0 + i * 0.004 + k * 0.0002, 30.6 + (1e-5 if k % 2 else -1e-5)]
                    for k in range(40)]},
            })
    return {"type": "FeatureCollection", "features": feats}


async def _seed(session_id: str) -> str:
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        session_id,
        __import__("app.services.mapspec.lifecycle_engine", fromlist=[
            "UpsertLayerIntent"]).UpsertLayerIntent(
            layer={"id": "roads", "type": "line", "source": "src-roads",
                   "name": "省域道路网", "paint": {"line-color": "#c33"}},
            source_data={"type": "geojson", "inlineData": _roads_fc()},
        ),
    )
    assert not res.is_error, res.error_msg
    spec = await mapspec_store_instance.get_mapspec(session_id)
    src = spec["sources"]["src-roads"]
    ref = src.get("ref_id") or src.get("ref")
    # inline 播种路径：载体是 inlineData（服务层双载体支持）
    return ref if isinstance(ref, str) else None


@pytest.fixture
async def session():
    sid = f"msp-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    yield sid
    session_data_manager._store.pop(sid, None)
    session_data_manager._map_state.pop(sid, None)
    await mapspec_store_instance.discard_mapspec(sid)


async def test_inspect_reports_importance_evidence(session):
    report = await inspect_map_scale_profile(session, "roads")
    assert report["status"] == "ok"
    sel = report["importance"]["selected"]
    assert sel is not None, report["importance"]["reason_codes"]
    assert sel["field"] == "道路等级"
    assert sel["value_order"][0] == "高速公路"
    assert report["band_selection_preview"]["world"] == ["高速公路", "国道", "省道"]
    assert report["source"]["feature_count"] == 266


async def test_inspect_bad_target_lists_candidates(session):
    from app.services.gis_harness.scale_profile import ScaleProfileError

    with pytest.raises(ScaleProfileError) as exc:
        await inspect_map_scale_profile(session, "nope")
    assert "roads" in str(exc.value)


async def test_preview_is_zero_mutation(session):
    before = await mapspec_store_instance.get_mapspec(session)
    rev_before = (await session_data_manager.get_map_state(session) or {}).get(
        "_cartographic_mutation_revision")
    preview = await preview_scale_representation(session, "roads")
    assert preview["status"] == "preview"
    tiers = {b["tier"]: b for b in preview["bands"]}
    assert tiers["world"]["representation_mode"] == "generalized"
    assert tiers["world"]["displayed"] == 6 + 20 + 40
    assert tiers["world"]["original"] == 266
    assert tiers["street"]["displayed"] == 266
    assert preview["windows"][0]["mode"] == "generalized"
    # 零突变：spec / revision 不动
    after = await mapspec_store_instance.get_mapspec(session)
    rev_after = (await session_data_manager.get_map_state(session) or {}).get(
        "_cartographic_mutation_revision")
    assert after == before and rev_after == rev_before


async def test_apply_commit_writes_profile_and_artifacts(session):
    import json as _json

    spec0 = await mapspec_store_instance.get_mapspec(session)
    src_snapshot = _json.dumps(spec0["sources"]["src-roads"], sort_keys=True)
    rev_before = (await session_data_manager.get_map_state(session) or {}).get(
        "_cartographic_mutation_revision", 0)

    receipt = await apply_scale_representation(
        session, "roads", mode="commit", operation_id="op-msp-1",
        note="省域道路多尺度")
    assert receipt["status"] == "committed", receipt
    # 播种载体是 inline → 锚点 inline:<source_id>（ref 载荷则 ref: 前缀）
    assert receipt["source_untouched"].startswith(("ref:", "inline:"))
    assert receipt["artifact_refs"], receipt
    assert all(r.startswith("ref:") for r in receipt["artifact_refs"].values())
    assert receipt["mapspec_revision"] > rev_before

    spec = await mapspec_store_instance.get_mapspec(session)
    layer = next(lyr for lyr in spec["layers"] if lyr["id"] == "roads")
    # representation 窗口 + source 覆盖（v1.9）
    windows = layer["representation"]["windows"]
    gen = [w for w in windows if w["mode"] == "generalized"]
    assert gen and all("source" in w for w in gen)
    # scale_profile 声明 + 统计口径 + 分类指纹
    block = layer["scale_profile"]
    assert block["schema_version"] == 1
    assert block["classification_basis"] == "original_source"
    assert block["classification_breaks_fingerprint"] is None  # 无 legend_spec
    # 工件源真实在场且为 ref 载荷
    for w in gen:
        src = spec["sources"].get(w["source"])
        assert isinstance(src, dict) and src.get("ref_id", "").startswith("ref:")
    # 原始数据非破坏：主源条目逐字节不变（inline 载荷快照对比；工件源
    # 是新增 key，不动 src-roads 本体）
    spec_now = await mapspec_store_instance.get_mapspec(session)
    assert _json.dumps(spec_now["sources"]["src-roads"], sort_keys=True) == (
        src_snapshot)


async def test_apply_commit_idempotent_replay(session):
    r1 = await apply_scale_representation(
        session, "roads", mode="commit", operation_id="op-msp-replay")
    assert r1["status"] == "committed"
    r2 = await apply_scale_representation(
        session, "roads", mode="commit", operation_id="op-msp-replay")
    assert r2.get("replayed") is True
    assert r2["profile_id"] == r1["profile_id"]


async def test_apply_pin_raw_override_honored(session):
    receipt = await apply_scale_representation(
        session, "roads", mode="commit", operation_id="op-msp-pin",
        overrides=[{"kind": "pin_mode", "value": "raw"}])
    assert receipt["status"] == "committed"
    assert all(b["representation_mode"] == "raw" for b in receipt["bands"])
    assert receipt["artifact_refs"] == {}
    spec = await mapspec_store_instance.get_mapspec(session)
    layer = next(lyr for lyr in spec["layers"] if lyr["id"] == "roads")
    assert "representation" not in layer or all(
        w["mode"] == "raw" for w in layer.get("representation", {}).get("windows", []))


async def test_apply_preview_mode_does_not_commit(session):
    receipt = await apply_scale_representation(
        session, "roads", mode="preview", operation_id="op-msp-pv")
    assert receipt["status"] == "preview"
    assert "commit_hint" in receipt


async def test_partial_chain_resume_after_supersede(session):
    """评审 R1-P2-3：链中断持久化 partial 回执；同 op 重放断点续链。"""
    from app.services.session_data import session_data_manager

    # 用陈旧 CAS 基线强制首笔 superseded → partial（committed_slots=0）
    rev = (await session_data_manager.get_map_state(session) or {}).get(
        "_cartographic_mutation_revision", 0)
    r1 = await apply_scale_representation(
        session, "roads", mode="commit", operation_id="op-msp-resume",
        expected_revision=max(0, rev - 1))
    assert r1["status"] == "partial", r1
    assert r1["committed_slots"] == 0
    state = await session_data_manager.get_map_state(session) or {}
    ring = state.get("_scale_profile_receipts") or []
    assert any(row.get("operation_id") == "op-msp-resume"
               and row.get("status") == "partial" for row in ring)

    # 同 op 重放（不带陈旧基线）→ 从断点续链直至 committed
    r2 = await apply_scale_representation(
        session, "roads", mode="commit", operation_id="op-msp-resume")
    assert r2["status"] == "committed", r2
    assert r2.get("replayed") is None  # 是真实续链提交，非 committed 回放

    # 回执环：committed 覆盖同 op 的 partial（每 op 单条）
    state2 = await session_data_manager.get_map_state(session) or {}
    rows = [row for row in (state2.get("_scale_profile_receipts") or [])
            if row.get("operation_id") == "op-msp-resume"]
    assert len(rows) == 1 and rows[0]["status"] == "committed"


async def test_artifact_content_fingerprint_stable(session):
    """评审 R1-P2-2：工件 data_fingerprint 是内容指纹（同内容同指纹）。"""
    r1 = await apply_scale_representation(
        session, "roads", mode="commit", operation_id="op-msp-fp1")
    assert r1["status"] == "committed"
    spec1 = await mapspec_store_instance.get_mapspec(session)
    fps1 = {
        sid: entry.get("data_fingerprint")
        for sid, entry in spec1["sources"].items()
        if sid.startswith("roads__gen@")}

    # 同参数第二个 op（不同 operation_id）：同内容 → 同指纹
    r2 = await apply_scale_representation(
        session, "roads", mode="commit", operation_id="op-msp-fp2")
    assert r2["status"] == "committed"
    spec2 = await mapspec_store_instance.get_mapspec(session)
    fps2 = {
        sid: entry.get("data_fingerprint")
        for sid, entry in spec2["sources"].items()
        if sid.startswith("roads__gen@")}
    assert fps1 and fps1 == fps2, (fps1, fps2)
    assert all(fp and fp.startswith("gen-sha256:") for fp in fps1.values())


async def test_resume_ignores_stale_stored_baseline(session):
    """R2-新发现 1 回归锁：partial 存量 chain_revision 过期不得永久压制。"""
    from app.services.gis_harness.scale_profile.service import _persist_receipt
    from app.services.session_data import session_data_manager

    # 手工塞入 chain_revision=999 的 partial（模拟远期存量）
    await _persist_receipt(session, {
        "status": "partial", "schema_version": "scale_profile.v1",
        "operation_id": "op-msp-stale", "layer_id": "roads",
        "committed_slots": 0, "chain_revision": 999,
        "artifact_refs": {}, "artifact_source_ids": {},
        "error": "synthetic", "at": 0.0,
    })
    # 修复前：读存量 999 → 永远 superseded；修复后：重读当前 → 续链提交
    receipt = await apply_scale_representation(
        session, "roads", mode="commit", operation_id="op-msp-stale")
    assert receipt["status"] == "committed", receipt
    state = await session_data_manager.get_map_state(session) or {}
    rows = [r for r in (state.get("_scale_profile_receipts") or [])
            if r.get("operation_id") == "op-msp-stale"]
    assert len(rows) == 1 and rows[0]["status"] == "committed"


async def test_resume_explicit_baseline_wins(session):
    """R2-新发现 1 回归锁：显式 expected_revision 恒优先于 partial 存量。"""
    from app.services.gis_harness.scale_profile.service import _persist_receipt

    rev = (await session_data_manager.get_map_state(session) or {}).get(
        "_cartographic_mutation_revision", 0)
    await _persist_receipt(session, {
        "status": "partial", "schema_version": "scale_profile.v1",
        "operation_id": "op-msp-explicit", "layer_id": "roads",
        "committed_slots": 0, "chain_revision": max(0, rev - 5),
        "artifact_refs": {}, "artifact_source_ids": {},
        "error": "synthetic", "at": 0.0,
    })
    # 带新鲜基线：直接以入参为 CAS 基线续链
    receipt = await apply_scale_representation(
        session, "roads", mode="commit", operation_id="op-msp-explicit",
        expected_revision=rev)
    assert receipt["status"] == "committed", receipt
    assert receipt["mapspec_revision"] > rev


async def test_resume_shrunk_intents_reports_error(session):
    """R2-新发现 3 回归锁：重放参数使 intents 变少 → 诚实 error 非伪 committed。"""
    from app.services.gis_harness.scale_profile.service import _persist_receipt

    await _persist_receipt(session, {
        "status": "partial", "schema_version": "scale_profile.v1",
        "operation_id": "op-msp-shrunk", "layer_id": "roads",
        "committed_slots": 99,  # 远超实际 intents 数 → 续链切片为空
        "chain_revision": 0,
        "artifact_refs": {}, "artifact_source_ids": {},
        "error": "synthetic", "at": 0.0,
    })
    receipt = await apply_scale_representation(
        session, "roads", mode="commit", operation_id="op-msp-shrunk")
    assert receipt["status"] == "error", receipt
    assert "no runnable intents" in receipt["error"]
