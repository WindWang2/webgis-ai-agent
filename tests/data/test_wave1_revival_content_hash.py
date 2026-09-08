"""Wave 1：register_artifact 复活守卫 + geocompute ARTIFACT_REGISTER
不可复活死载荷 + RefDescriptor.content_hash 环境开关测试。

audit §6.2.1：register_artifact 此前把任何记录无条件拉回 valid —— 从不
探测载荷的调用方（geocompute ARTIFACT_REGISTER 裸 ref_id）能把死载荷
标记成 valid。修复后：终态（expired/failed/superseded）记录先 probe，
miss 保持诚实 expired；valid/stale 快路径保持无探测（热路径零成本）。
"""
import asyncio
import uuid

from app.services.artifact_registry import (
    get_artifact,
    mark_status,
    register_artifact,
)
from app.services.session_data import session_data_manager


def _fc():
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
                          "properties": {"k": "v"}}]}


async def _mint_and_register(session_id, payload):
    ref = await session_data_manager.store(session_id, payload, prefix="poi")
    rec = await register_artifact(session_id, artifact_id=ref, producer_tool="t")
    assert rec is not None and rec.status == "valid"
    return ref


async def test_register_over_expired_dead_payload_stays_expired():
    sid = f"rv-{uuid.uuid4().hex[:8]}"
    try:
        ref = await _mint_and_register(sid, _fc())
        await session_data_manager.delete_ref(sid, ref)  # 载荷死亡
        await mark_status(sid, ref, "expired")  # 账本诚实记录
        # 重注册（= geocompute ARTIFACT_REGISTER 的裸 ref_id 路径）：
        # 死载荷不得被拉回 valid
        rec = await register_artifact(sid, artifact_id=ref, producer_tool="retry")
        assert rec is not None
        assert rec.status == "expired", "dead payload must not be revived to valid"
        assert rec.metadata.get("revival_probe") == "miss"
        stored = await get_artifact(sid, ref)
        assert stored.status == "expired"
    finally:
        await session_data_manager.clear_session(sid)


async def test_register_over_expired_live_payload_becomes_valid():
    sid = f"rv-{uuid.uuid4().hex[:8]}"
    try:
        ref = await _mint_and_register(sid, _fc())
        await mark_status(sid, ref, "expired")  # 载荷仍活着，只是账本态过期
        rec = await register_artifact(sid, artifact_id=ref, producer_tool="retry")
        assert rec is not None
        assert rec.status == "valid", "live payload SHOULD be revived (probe hit)"
        assert "revival_probe" not in (rec.metadata or {})
    finally:
        await session_data_manager.clear_session(sid)


async def test_valid_and_new_record_fast_path_never_probes(monkeypatch):
    """valid→valid 重注册与新记录保持无探测（probe 计数恒 0）。"""
    import app.services.artifact_registry as ar

    sid = f"rv-{uuid.uuid4().hex[:8]}"
    calls = []

    async def _spy(session_id, ref, **kw):
        calls.append(ref)
        return {"exists": True}

    monkeypatch.setattr(ar, "probe_ref", _spy)
    try:
        ref = await _mint_and_register(sid, _fc())
        assert calls == [], "fresh registration is a fast path (no probe)"
        rec = await register_artifact(sid, artifact_id=ref, producer_tool="t2")
        assert rec.status == "valid"
        assert calls == [], "valid→valid re-register is a fast path (no probe)"
    finally:
        await session_data_manager.clear_session(sid)


# ── geocompute ARTIFACT_REGISTER op 端到端（execute_node 同一入口）────────


def test_geocompute_artifact_register_op_cannot_revive_dead_payload():
    """audit §6.2.1：裸 ref_id 的 ARTIFACT_REGISTER op 绝不能把死载荷
    标记为 valid（此前经 _op_artifact_register → register_artifact 无条件
    复活）。同步测试体：execute_node 内部的 run_coro_sync 需要非运行循环。"""
    from app.services.geocompute.ops import OperatorContext, execute_node
    from app.services.geocompute.plan import ExecutionNode, NodeCategory

    sid = f"rv-{uuid.uuid4().hex[:8]}"

    async def _setup():
        ref = await session_data_manager.store(sid, _fc(), prefix="poi")
        rec = await register_artifact(sid, artifact_id=ref, producer_tool="t")
        assert rec.status == "valid"
        await session_data_manager.delete_ref(sid, ref)  # 载荷死亡
        await mark_status(sid, ref, "expired")
        return ref

    dead_ref = asyncio.run(_setup())
    try:
        ctx = OperatorContext(run_id="run_rv", node_id="node_rv", session_id=sid)
        node = ExecutionNode(
            node_id="node_rv",
            category=NodeCategory.ARTIFACT_REGISTER,
            operation="ARTIFACT_REGISTER",
            inputs=["r"],
            parameters={"artifact_type": "geocompute"},
        )
        out = execute_node(ctx, node, {"r": {"ref_id": dead_ref}})
        assert out["metadata"]["registered"] is True  # 注册本身仍是增值记录
        assert out["metadata"]["artifact_state"] in (None, "expired")
        record = asyncio.run(get_artifact(sid, dead_ref))
        assert record is not None
        assert record.status == "expired", (
            "geocompute ARTIFACT_REGISTER must not mark a dead payload valid"
        )
    finally:
        asyncio.run(session_data_manager.clear_session(sid))


# ── RefDescriptor.content_hash opt-in（WEBGIS_REF_CONTENT_HASH）──────────


def _canonical_hash(payload):
    from app.lib.data.fingerprints import canonical_fingerprint

    return canonical_fingerprint(payload)


def test_content_hash_default_off_is_none():
    """默认关：行为与历史逐字节一致（content_hash 恒 None）。"""
    from app.schemas.ref_descriptor import compute_descriptor

    d = compute_descriptor("ref:x", _fc())
    assert d.content_hash is None
    assert d.to_dict()["content_hash"] is None


def test_content_hash_enabled_populates_small_payload(monkeypatch):
    from app.schemas.ref_descriptor import compute_descriptor

    monkeypatch.setenv("WEBGIS_REF_CONTENT_HASH", "1")
    payload = _fc()
    d = compute_descriptor("ref:x", payload)
    assert d.content_hash == _canonical_hash(payload)


def test_content_hash_enabled_oversized_payload_stays_none(monkeypatch):
    """>1MB 成本闸：即使开关打开也保持 None（诚实缺省）。"""
    from app.schemas.ref_descriptor import compute_descriptor

    monkeypatch.setenv("WEBGIS_REF_CONTENT_HASH", "true")
    big = {"type": "FeatureCollection",
           "features": [{"type": "Feature", "geometry": None,
                         "properties": {"blob": "x" * (1200 * 1024)}}]}
    d = compute_descriptor("ref:big", big)
    assert d.content_hash is None


def test_content_hash_unserializable_stays_none(monkeypatch):
    from app.schemas.ref_descriptor import compute_descriptor

    monkeypatch.setenv("WEBGIS_REF_CONTENT_HASH", "1")
    d = compute_descriptor("ref:nan", {"v": float("nan")})  # NaN 非 fingerprintable
    assert d.content_hash is None


async def test_content_hash_flows_through_session_store_descriptor(monkeypatch):
    """端到端：store() 描述符盖章（off-loop to_thread 路径）携带 content_hash。"""
    monkeypatch.setenv("WEBGIS_REF_CONTENT_HASH", "1")
    sid = f"ch-{uuid.uuid4().hex[:8]}"
    payload = _fc()
    try:
        ref = await session_data_manager.store(sid, payload, prefix="ch")
        desc = await session_data_manager.get_ref_descriptor(sid, ref)
        assert desc is not None
        assert desc["content_hash"] == _canonical_hash(payload)
    finally:
        await session_data_manager.clear_session(sid)
