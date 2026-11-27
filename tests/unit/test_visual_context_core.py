"""G36 视觉上下文核心单测（ADR-0291）。

覆盖：decoder（真实解码/EXIF/炸弹/动画/预算）、evidence（身份不变量）、
store（登记/解析/撤销/共享/并发生命周期）、resolver（授权/预算/能力门）、
profile（能力投影）、dispatch 窄图像投影、V33 台账。

对应 V 表：V03/V04/V06/V09/V10/V12/V13/V14/V15/V16/V17/V18/V28（单元层）。
"""
from __future__ import annotations

import base64
import io
import uuid

import pytest
from PIL import Image

from app.services.visual_context import refs as vc_refs
from app.services.visual_context import store as vc_store
from app.services.visual_context.decoder import (
    decode_image,
    derive_model_image,
    make_thumbnail,
)
from app.services.visual_context.evidence import (
    EvidenceRejected,
    VisualEvidenceRecord,
    mint_evidence_id,
    viewport_identity_hash,
)
from app.services.visual_context.resolver import (
    MAX_ATTACHMENTS_PER_TURN,
    SentImageLedger,
    resolve_attachments,
)
from app.services.visual_context.profile import (
    VisionGate,
    model_catalog_entry,
    model_entry_declares_image,
    state_model_accepts_image,
)


# ── 图片工厂 ─────────────────────────────────────────────────────────────

def _png_bytes(w: int = 64, h: int = 32, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(w: int = 64, h: int = 32, color=(30, 30, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _exif_rotated_jpeg() -> bytes:
    """带 Orientation=6（旋转 90°）的 JPEG（V13）。"""
    img = Image.new("RGB", (40, 20), (10, 200, 10))
    exif = Image.Exif()
    exif[274] = 6  # Orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


# ── decoder ──────────────────────────────────────────────────────────────

def test_decode_png_and_derive_bounds():
    decoded = decode_image(_png_bytes(2000, 1000))
    assert decoded.format == "PNG"
    assert (decoded.width, decoded.height) == (2000, 1000)
    data, mime, w, h = derive_model_image(decoded)
    # 长边 ≤1568（与投影 inputLimits 一致）；JPEG 通道（无 alpha）。
    assert max(w, h) <= 1568
    assert mime == "image/jpeg"
    assert 0 < len(data) <= 1536 * 1024


def test_decode_rejects_non_raster_and_animated():
    # SVG（V15：不执行脚本/不解析）。
    with pytest.raises(EvidenceRejected) as ei:
        decode_image(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    assert ei.value.reason == "decode_failed"
    # 动画 GIF（多帧 → 明确拒绝，不取首帧）。
    frames = [Image.new("RGB", (8, 8), (i, 0, 0)) for i in range(3)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    with pytest.raises(EvidenceRejected) as ei:
        decode_image(buf.getvalue())
    assert ei.value.reason == "animated_not_supported"


def test_decode_exif_orientation_applied_and_metadata_stripped():
    decoded = decode_image(_exif_rotated_jpeg())
    # Orientation=6 → 转置后宽高互换。
    assert (decoded.width, decoded.height) == (20, 40)
    data, mime, w, h = derive_model_image(decoded)
    assert (w, h) == (20, 40)
    # 派生图不携带 EXIF（GPS 等）。
    out = Image.open(io.BytesIO(data))
    assert not (out.getexif() or {})


def test_decode_rejects_decompression_bomb():
    # 真实有效但超预算的 PNG（头部尺寸先行拒绝，不物化像素）。
    buf = io.BytesIO()
    Image.new("RGB", (9000, 9000), (10, 10, 10)).save(buf, format="PNG")
    with pytest.raises(EvidenceRejected) as ei:
        decode_image(buf.getvalue())
    assert ei.value.reason == "pixel_budget_exceeded"


def test_near_blank_observation_is_flagged_not_rejected():
    decoded = decode_image(_png_bytes(64, 64, color=(250, 250, 250)))
    assert "near_blank" in decoded.observations  # V14：诚实观察


def test_crop_chain_and_out_of_bounds():
    decoded = decode_image(_png_bytes(100, 80))
    data, mime, w, h = derive_model_image(
        decoded, crop={"x": 60, "y": 40, "w": 40, "h": 40})
    assert (w, h) == (40, 40)  # V03：crop 按原图像素执行
    with pytest.raises(EvidenceRejected) as ei:
        derive_model_image(decoded, crop={"x": 90, "y": 0, "w": 40, "h": 40})
    assert ei.value.reason == "crop_out_of_bounds"


def test_thumbnail_bounded():
    decoded = decode_image(_png_bytes(1200, 800))
    thumb = make_thumbnail(decoded)
    out = Image.open(io.BytesIO(thumb))
    assert max(out.size) <= 320


# ── evidence 身份 ────────────────────────────────────────────────────────

def test_viewport_hash_stable_and_sensitive():
    vp = {"center": [104.0, 30.6], "zoom": 10.5, "bearing": 0, "pitch": 0}
    assert viewport_identity_hash(vp) == viewport_identity_hash(dict(vp))
    # 平移 → 新身份（V04）。
    moved = dict(vp, center=[104.001, 30.6])
    assert viewport_identity_hash(vp) != viewport_identity_hash(moved)
    # 缩放 → 新身份。
    zoomed = dict(vp, zoom=11.0)
    assert viewport_identity_hash(vp) != viewport_identity_hash(zoomed)
    # 浮点噪声（<1e-6）不产生新身份。
    noisy = dict(vp, center=[104.0000000001, 30.6])
    assert viewport_identity_hash(vp) == viewport_identity_hash(noisy)


def _map_record(session_id: str, **kw) -> VisualEvidenceRecord:
    base = dict(
        evidence_id=mint_evidence_id(),
        session_id=session_id,
        source_type="map_viewport",
        mime="image/jpeg",
        origin_sha256="a" * 64,
        derived_sha256="b" * 64,
        origin_size=100,
        derived_size=90,
        width=100,
        height=80,
        mapspec_revision=7,
        mapspec_fingerprint="fp",
        viewport={"center": [1.0, 2.0], "zoom": 9},
        viewport_hash=viewport_identity_hash({"center": [1.0, 2.0], "zoom": 9}),
    )
    base.update(kw)
    return VisualEvidenceRecord(**base)


def test_reference_must_not_claim_map_identity():
    # V06：参考图伪造 revision → 拒绝。
    with pytest.raises(EvidenceRejected) as ei:
        _map_record("s1", source_type="reference_upload").validate_invariants()
    assert ei.value.reason == "reference_must_not_claim_map_identity"


def test_map_evidence_requires_revision_and_viewport():
    with pytest.raises(EvidenceRejected) as ei:
        _map_record("s1", mapspec_revision=None).validate_invariants()
    assert ei.value.reason == "map_evidence_requires_revision"
    with pytest.raises(EvidenceRejected) as ei:
        _map_record("s1", viewport_hash="").validate_invariants()
    assert ei.value.reason == "map_evidence_requires_viewport"


def test_region_requires_crop_rect():
    with pytest.raises(EvidenceRejected) as ei:
        _map_record("s1", source_type="map_region").validate_invariants()
    assert ei.value.reason == "region_requires_crop_rect"


def test_region_crop_non_numeric_is_typed_rejection():
    # 非数值 crop 值（intake 载荷 crop 含 null/字符串）→ typed 拒绝，
    # 不得外溢 TypeError/ValueError（登记期只抛 EvidenceRejected）。
    for bad in ({"x": None, "y": 0, "w": 10, "h": 10},
                {"x": 0, "y": None, "w": 10, "h": 10},
                {"x": 0, "y": 0, "w": "abc", "h": 10},
                {"x": 0, "y": 0, "w": 10, "h": None}):
        with pytest.raises(EvidenceRejected) as ei:
            _map_record("s1", source_type="map_region", crop=bad).validate_invariants()
        assert ei.value.reason in ("region_requires_crop_rect", "region_crop_empty")
    # 合法矩形仍照常通过（守卫不过度拒绝）。
    _map_record("s1", source_type="map_region",
                crop={"x": 0, "y": 0, "w": 10, "h": 10}).validate_invariants()


def test_record_roundtrip_drops_malformed():
    rec = _map_record("s1")
    parsed = VisualEvidenceRecord.from_dict(rec.to_dict())
    assert parsed is not None and parsed.evidence_id == rec.evidence_id
    assert VisualEvidenceRecord.from_dict({"evidence_id": "not-vev"}) is None
    assert VisualEvidenceRecord.from_dict("junk") is None


# ── store / refs（blob 生命周期）─────────────────────────────────────────

@pytest.fixture
async def vc_session(tmp_path, monkeypatch):
    sid = f"vc-{uuid.uuid4().hex[:8]}"
    from app.services.session_data import session_data_manager

    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


def _derived_pair(seed: int = 0):
    decoded = decode_image(_png_bytes(48 + seed, 32, color=(seed * 7 % 255, 99, 99)))
    return derive_model_image(decoded)


async def _register(session_id: str, record: VisualEvidenceRecord, derived: bytes):
    return await vc_store.register_evidence(session_id, derived, record)


async def test_register_resolve_roundtrip(vc_session):
    data, mime, w, h = _derived_pair()
    rec = _map_record(vc_session, derived_sha256=vc_refs.__dict__ and _sha(data),
                      derived_size=len(data), width=w, height=h)
    saved = await _register(vc_session, rec, data)
    got = await vc_store.resolve_evidence(
        vc_session, saved.evidence_id, max_bytes=8 * 1024 * 1024)
    assert got is not None
    rec2, blob = got
    assert rec2.evidence_id == saved.evidence_id
    assert blob == data  # sha 校验通过


def _sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


async def test_resolve_rejects_foreign_session(vc_session):
    """V16：B 会话猜 ref 读 A 的图 → None。"""
    data, mime, w, h = _derived_pair()
    rec = _map_record(vc_session, derived_sha256=_sha(data),
                      derived_size=len(data), width=w, height=h)
    saved = await _register(vc_session, rec, data)
    got = await vc_store.resolve_evidence(
        "vc-other-session", saved.evidence_id, max_bytes=8 * 1024 * 1024)
    assert got is None


async def test_shared_blob_revoke_keeps_other_session(vc_session, tmp_path):
    """V17：同字节两会话共享 blob；A 撤销 → B 仍可读。"""
    other = f"vc-{uuid.uuid4().hex[:8]}"
    from app.services.session_data import session_data_manager

    await session_data_manager.clear_session(other)
    try:
        data, mime, w, h = _derived_pair(seed=3)
        sha = _sha(data)
        rec_a = _map_record(vc_session, derived_sha256=sha,
                            derived_size=len(data), width=w, height=h)
        rec_b = _map_record(other, derived_sha256=sha,
                            derived_size=len(data), width=w, height=h)
        a = await _register(vc_session, rec_a, data)
        b = await _register(other, rec_b, data)
        # A 撤销自己的证据。
        await vc_store.revoke_evidence(vc_session, a.evidence_id)
        assert await vc_store.resolve_evidence(
            vc_session, a.evidence_id, max_bytes=8 * 1024 * 1024) is None
        got_b = await vc_store.resolve_evidence(
            other, b.evidence_id, max_bytes=8 * 1024 * 1024)
        assert got_b is not None and got_b[1] == data
    finally:
        await session_data_manager.clear_session(other)


async def test_same_bytes_two_map_versions_distinct_identity(vc_session):
    """V18：blob 去重（同 sha），证据身份（revision/viewport）不合并。"""
    data, mime, w, h = _derived_pair(seed=5)
    sha = _sha(data)
    r1 = _map_record(vc_session, derived_sha256=sha, derived_size=len(data),
                     width=w, height=h, mapspec_revision=1)
    r2 = _map_record(vc_session, derived_sha256=sha, derived_size=len(data),
                     width=w, height=h, mapspec_revision=2)
    e1 = await _register(vc_session, r1, data)
    e2 = await _register(vc_session, r2, data)
    assert e1.evidence_id != e2.evidence_id
    records = await vc_store.load_evidence_index(vc_session)
    assert len(records) == 2


async def test_draft_fifo_cap_and_revoked_not_evicted(vc_session):
    from app.services.visual_context.store import MAX_DRAFTS_PER_SESSION

    ids = []
    for i in range(MAX_DRAFTS_PER_SESSION + 3):
        data, _, w, h = _derived_pair(seed=10 + i)
        rec = _map_record(vc_session, derived_sha256=_sha(data),
                          derived_size=len(data), width=w, height=h,
                          mapspec_revision=i)
        ids.append((await _register(vc_session, rec, data)).evidence_id)
    records = await vc_store.load_evidence_index(vc_session)
    drafts = [r for r in records if r.state == "draft"]
    assert len(drafts) == MAX_DRAFTS_PER_SESSION
    # 最旧的 draft 被 FIFO 淘汰。
    assert ids[0] not in {r.evidence_id for r in drafts}
    # bound 不被淘汰：先绑定第一条。
    data, _, w, h = _derived_pair(seed=99)
    rec = _map_record(vc_session, derived_sha256=_sha(data),
                      derived_size=len(data), width=w, height=h)
    bound = await _register(vc_session, rec, data)
    await vc_store.bind_evidence_to_message(vc_session, [bound.evidence_id], 42)
    for i in range(MAX_DRAFTS_PER_SESSION + 2):
        d2, _, w2, h2 = _derived_pair(seed=200 + i)
        r2 = _map_record(vc_session, derived_sha256=_sha(d2),
                         derived_size=len(d2), width=w2, height=h2)
        await _register(vc_session, r2, d2)
    records = await vc_store.load_evidence_index(vc_session)
    assert any(r.evidence_id == bound.evidence_id and r.state == "bound"
               for r in records)  # V19：发送过的附件不被 draft 上限淘汰


async def test_revoke_tombstone_blocks_resolve(vc_session):
    """V28：撤销后解析拒绝；tombstone 记录保留（可审计）。"""
    data, _, w, h = _derived_pair(seed=7)
    rec = _map_record(vc_session, derived_sha256=_sha(data),
                      derived_size=len(data), width=w, height=h)
    saved = await _register(vc_session, rec, data)
    await vc_store.revoke_evidence(vc_session, saved.evidence_id)
    assert await vc_store.resolve_evidence(
        vc_session, saved.evidence_id, max_bytes=8 * 1024 * 1024) is None
    records = await vc_store.load_evidence_index(vc_session)
    assert any(r.evidence_id == saved.evidence_id and r.state == "revoked"
               for r in records)


async def test_registration_budget(vc_session, monkeypatch):
    from app.services.visual_context import store as store_mod

    orig = store_mod.MAX_REGISTRATIONS_PER_HOUR
    monkeypatch.setattr(store_mod, "MAX_REGISTRATIONS_PER_HOUR", 2, raising=False)
    try:
        for i in range(3):
            data, _, w, h = _derived_pair(seed=30 + i)
            rec = _map_record(vc_session, derived_sha256=_sha(data),
                              derived_size=len(data), width=w, height=h)
            if i < 2:
                await _register(vc_session, rec, data)
            else:
                with pytest.raises(EvidenceRejected) as ei:
                    await _register(vc_session, rec, data)
                assert ei.value.reason == "registration_budget_exceeded"
    finally:
        monkeypatch.setattr(store_mod, "MAX_REGISTRATIONS_PER_HOUR",
                            orig, raising=False)


# ── resolver（授权/预算/能力门）─────────────────────────────────────────

class _Ref:
    def __init__(self, evidence_id: str):
        self.evidence_id = evidence_id


async def _seed_attachment(vc_session: str, seed: int = 0):
    data, _, w, h = _derived_pair(seed=seed)
    rec = _map_record(vc_session, derived_sha256=_sha(data),
                      derived_size=len(data), width=w, height=h)
    saved = await _register(vc_session, rec, data)
    return saved, data


async def test_resolve_attachments_refuses_when_not_allowed(vc_session):
    """V10：模型未确证 vision → 全部拒绝，typed reason。"""
    saved, _ = await _seed_attachment(vc_session)
    outcome = await resolve_attachments(
        vc_session, [_Ref(saved.evidence_id)],
        images_allowed=False, allowed_reason="model_not_configured_for_vision")
    assert not outcome.any_sent
    assert outcome.refusal_summary[0]["reason"] == "model_not_configured_for_vision"


async def test_resolve_attachments_sent_and_refused(vc_session):
    saved, data = await _seed_attachment(vc_session, seed=1)
    # 形如 vev- 但不在本会话索引（跨会话猜 ref / 不存在 —— V16 口径）。
    fake = _Ref("vev-" + "0" * 16)
    outcome = await resolve_attachments(
        vc_session, [_Ref(saved.evidence_id), fake], images_allowed=True)
    assert len(outcome.images) == 1
    img = outcome.images[0]
    assert img["type"] == "image"
    assert base64.b64decode(img["data"]) == data
    assert img["mimeType"] in ("image/png", "image/jpeg")
    assert outcome.refusal_summary[0]["evidence_id"] == fake.evidence_id
    assert outcome.refusal_summary[0]["reason"] == "evidence_not_found_or_foreign_session"
    # 格式非法（非 vev 前缀）→ invalid_evidence_ref。
    outcome2 = await resolve_attachments(
        vc_session, [_Ref("vshot-abc")], images_allowed=True)
    assert outcome2.refusal_summary[0]["reason"] == "invalid_evidence_ref"


async def test_resolve_attachments_revoked_refused(vc_session):
    saved, _ = await _seed_attachment(vc_session, seed=2)
    await vc_store.revoke_evidence(vc_session, saved.evidence_id)
    outcome = await resolve_attachments(
        vc_session, [_Ref(saved.evidence_id)], images_allowed=True)
    assert not outcome.any_sent
    assert outcome.refusal_summary[0]["reason"] == "evidence_revoked"


async def test_resolve_attachments_caps_count(vc_session):
    refs = []
    for i in range(MAX_ATTACHMENTS_PER_TURN + 2):
        saved, _ = await _seed_attachment(vc_session, seed=40 + i)
        refs.append(_Ref(saved.evidence_id))
    outcome = await resolve_attachments(vc_session, refs, images_allowed=True)
    assert len(outcome.images) == MAX_ATTACHMENTS_PER_TURN


async def test_resolve_attachments_overflow_gets_receipts(vc_session):
    """V09：超每 turn 上限的附件不静默丢弃 —— 逐条 refused 回执。"""
    refs = []
    for i in range(MAX_ATTACHMENTS_PER_TURN + 2):
        saved, _ = await _seed_attachment(vc_session, seed=50 + i)
        refs.append(_Ref(saved.evidence_id))
    outcome = await resolve_attachments(vc_session, refs, images_allowed=True)
    assert len(outcome.images) == MAX_ATTACHMENTS_PER_TURN
    assert len(outcome.receipts) == len(refs)
    overflow = outcome.receipts[MAX_ATTACHMENTS_PER_TURN:]
    assert [r.evidence_id for r in overflow] == [
        r.evidence_id for r in refs[MAX_ATTACHMENTS_PER_TURN:]]
    assert all(r.status == "refused"
               and r.reason == "attachment_limit_exceeded" for r in overflow)


async def test_evidence_index_reads_past_256_records(vc_session):
    """P2：索引读取不截断 —— 第 257+ 条 bound 证据仍可解析且不被台账误判。"""
    from app.services.session_data import session_data_manager
    from app.services.visual_context.resolver import AttachmentReceipt

    saved, data = await _seed_attachment(vc_session, seed=70)
    fillers = [_map_record(vc_session, mapspec_revision=i) for i in range(256)]
    ok = await session_data_manager.set_map_state(
        vc_session, vc_store.INDEX_KEY,
        [r.to_dict() for r in fillers] + [saved.to_dict()])
    assert ok
    loaded = await vc_store.load_evidence_index_strict(vc_session)
    assert len(loaded) == len(fillers) + 1
    assert any(r.evidence_id == saved.evidence_id for r in loaded)
    # 解析仍可读（blob 引用仍在）；台账复验不得把它误判为已撤。
    got = await vc_store.resolve_evidence(
        vc_session, saved.evidence_id, max_bytes=8 * 1024 * 1024)
    assert got is not None and got[1] == data
    ledger = SentImageLedger()
    ledger.record(vc_session, [AttachmentReceipt(
        evidence_id=saved.evidence_id, status="sent",
        derived_sha256=saved.derived_sha256)])
    assert await ledger.find_revoked(vc_session) == []


# ── profile（能力投影 / 三重门）─────────────────────────────────────────

class _FakeRpc:
    def __init__(self, state=None):
        self._state = state
        self.calls = 0

    async def request(self, command, data=None):
        self.calls += 1
        if self._state is None:
            raise RuntimeError("down")
        return self._state


def test_model_catalog_entry_projects_only_confirmed_vision(monkeypatch):
    from app.services.visual_context import profile as profile_mod

    monkeypatch.setattr(profile_mod, "descriptor_vision_confirmed",
                        lambda model_id, provider_id="webgis": True)
    entry = model_catalog_entry("m1", "m1", True)
    assert model_entry_declares_image(entry) is True
    assert entry["input"] == ["text", "image"]
    assert entry["inputLimits"]["images"]["resize"]["maxWidth"] == 1568

    monkeypatch.setattr(profile_mod, "descriptor_vision_confirmed",
                        lambda model_id, provider_id="webgis": None)
    entry2 = model_catalog_entry("m1", "m1", True)
    assert model_entry_declares_image(entry2) is False
    assert "input" not in entry2  # 省略 input = Pi 默认 text-only


def test_state_model_accepts_image():
    assert state_model_accepts_image({"model": {"input": ["text", "image"]}}) is True
    assert state_model_accepts_image({"model": {"input": ["text"]}}) is False
    assert state_model_accepts_image({}) is None
    assert state_model_accepts_image(None) is None


async def test_vision_gate_three_doors(monkeypatch):
    from app.services.visual_context import profile as profile_mod

    gate = VisionGate()
    monkeypatch.setattr(profile_mod, "descriptor_vision_confirmed",
                        lambda model_id, provider_id="webgis": True)
    ok_state = {"model": {"input": ["text", "image"]}}
    rpc = _FakeRpc(ok_state)
    # 门 2 失败：spawn 未声明 image（V11：描述符换代早于 spawn）。
    allowed, reason = await gate.check(
        model_id="m", spawn_declared_image_input=False, rpc=rpc, spawn_epoch=1)
    assert (allowed, reason) == (False, "worker_spawned_without_image_input")
    # 门 3 失败：runtime 模型无 image input。
    allowed, reason = await gate.check(
        model_id="m", spawn_declared_image_input=True,
        rpc=_FakeRpc({"model": {"input": ["text"]}}), spawn_epoch=2)
    assert (allowed, reason) == (False, "runtime_model_lacks_image_input")
    # 门 3 失败：get_state 不可用。
    allowed, reason = await gate.check(
        model_id="m", spawn_declared_image_input=True,
        rpc=_FakeRpc(None), spawn_epoch=3)
    assert (allowed, reason) == (False, "runtime_state_unavailable")
    # 全部通过。
    allowed, reason = await gate.check(
        model_id="m", spawn_declared_image_input=True, rpc=rpc, spawn_epoch=4)
    assert (allowed, reason) == (True, "")
    # 缓存生效（同 epoch 不重复 get_state）。
    calls_before = rpc.calls
    await gate.check(model_id="m", spawn_declared_image_input=True,
                     rpc=rpc, spawn_epoch=4)
    assert rpc.calls == calls_before


# ── V33 台账 ────────────────────────────────────────────────────────────

async def test_sent_ledger_finds_revoked(vc_session):
    saved, _ = await _seed_attachment(vc_session, seed=60)
    from app.services.visual_context.resolver import AttachmentReceipt

    ledger = SentImageLedger()
    receipt = AttachmentReceipt(
        evidence_id=saved.evidence_id, source_type="map_viewport",
        status="sent", derived_sha256=saved.derived_sha256)
    ledger.record(vc_session, [receipt])
    assert await ledger.find_revoked(vc_session) == []
    # 撤销 → 下一 turn 复验发现失效 sha。
    await vc_store.revoke_evidence(vc_session, saved.evidence_id)
    revoked = await ledger.find_revoked(vc_session)
    assert revoked == [saved.derived_sha256]
    # 换代 → 台账清空（vendor transcript 已重建）。
    ledger.bump(vc_session)
    assert await ledger.find_revoked(vc_session) == []


# ── dispatch 窄投影 ──────────────────────────────────────────────────────

def test_extract_result_images_narrow():
    from app.services.tool_dispatch_service import _extract_result_images

    good = {"type": "image", "data": "x" * 64, "mimeType": "image/png"}
    assert _extract_result_images({"result_images": [good]}) == [good]
    # 任意 details 里的 image 字符串不自动升级（无 result_images 键 → 空）。
    assert _extract_result_images({"image": "data:image/png;base64,AAA"}) == []
    # 畸形条目丢弃。
    bad = [
        {"type": "text", "text": "hi"},                      # 非 image
        {"type": "image", "data": 123},                      # data 非 str
        {"type": "image", "data": "x", "mimeType": "image/webp"},  # mime 白名单外
        {"type": "image", "data": "x" * (2 * 1024 * 1024 + 1),
         "mimeType": "image/png"},                           # 超预算
        "junk",
    ]
    assert _extract_result_images({"result_images": bad}) == []
    # 单结果 ≤1 图。
    two = [good, dict(good)]
    assert len(_extract_result_images({"result_images": two})) == 1


# ── review 修复回归（P1#2/#3/#4、P2#10/#13）────────────────────────────

def test_slim_event_result_excludes_result_images():
    """P1#3：SSE step_result 绝不携带 base64。"""
    from app.services.llm_result_formatter import slim_event_result

    result = {
        "status": "ok",
        "summary": "证据已读",
        "result_images": [{"type": "image", "data": "x" * 100, "mimeType": "image/png"}],
    }
    slim = slim_event_result(result)
    assert "result_images" not in slim
    assert slim["summary"] == "证据已读"


async def test_cas_retry_does_not_double_release(vc_session, monkeypatch):
    """P1#4：WATCH 重试重跑 mutate 时释放清单不重复（共享 blob 计数不双减）。"""
    import app.services.session_state_atomic as atomic_mod
    from app.services.session_state_atomic import atomic_update_state as real_atomic

    # 同字节双证据（同会话共享 blob：refcount=2）。
    data, _, w, h = _derived_pair(seed=81)
    sha = _sha(data)
    r1 = _map_record(vc_session, derived_sha256=sha, derived_size=len(data),
                     width=w, height=h, mapspec_revision=1)
    r2 = _map_record(vc_session, derived_sha256=sha, derived_size=len(data),
                     width=w, height=h, mapspec_revision=2)
    e1 = await _register(vc_session, r1, data)
    e2 = await _register(vc_session, r2, data)

    async def failing_once(store, session_id, keys, update):
        # 跑一次 update 但不提交（模拟 WATCH 冲突的第一次尝试 —— 旧实现
        # 在闭包累积 released 会重复两次）→ 重跑 → 提交。
        snapshot = await store.get_map_state(session_id)
        update(snapshot)
        return await real_atomic(store, session_id, keys, update)

    monkeypatch.setattr(atomic_mod, "atomic_update_state", failing_once)
    await vc_store.revoke_evidence(vc_session, e1.evidence_id)
    # 双证据共享 blob：撤一个后另一个仍可解析（计数 2→1 而非 2→0）。
    got = await vc_store.resolve_evidence(
        vc_session, e2.evidence_id, max_bytes=8 * 1024 * 1024)
    assert got is not None and got[1] == data


async def test_precheck_runs_without_attachments(vc_session):
    """P1#2：无附件 turn 也执行 V33 预检（撤销后纯文本触发换代）。"""
    from app.services.visual_context.resolver import (
        AttachmentReceipt,
        get_sent_image_ledger,
    )

    saved, _ = await _seed_attachment(vc_session, seed=91)
    ledger = get_sent_image_ledger()  # bridge 使用进程级单例
    ledger.bump(vc_session)  # 隔离：清掉其他测试可能留下的代际状态
    ledger.record(vc_session, [AttachmentReceipt(
        evidence_id=saved.evidence_id, status="sent",
        derived_sha256=saved.derived_sha256)])
    await vc_store.revoke_evidence(vc_session, saved.evidence_id)

    class _Rpc:
        new_sessions = 0

        async def request(self, command, data=None):
            if command == "new_session":
                self.new_sessions += 1
                return {"cancelled": False}
            return {}

    from app.agent_pi_bridge import PiBridge

    bridge = PiBridge.__new__(PiBridge)

    async def _drain():
        return None

    bridge._drain_stale_events = _drain
    rpc = _Rpc()
    bridge._rpc = rpc
    bridge._vision_gate = None
    outcome = await bridge._resolve_turn_images(vc_session, None)
    assert outcome["images"] == []
    assert outcome["receipts"] == []
    assert rpc.new_sessions == 1  # 纯文本 turn 同样换代


async def test_find_revoked_unavailable_index_returns_none(vc_session, monkeypatch):
    """P2#10：索引瞬态不可用 → None（阻断图片），不是全部已撤（不换代）。"""
    from app.services.visual_context import store as store_mod
    from app.services.visual_context.resolver import SentImageLedger

    async def boom(_sid):
        raise RuntimeError("storage down")

    from app.services.visual_context.resolver import AttachmentReceipt

    saved, _ = await _seed_attachment(vc_session, seed=93)
    ledger = SentImageLedger()
    ledger.record(vc_session, [AttachmentReceipt(
        evidence_id=saved.evidence_id, status="sent",
        derived_sha256=saved.derived_sha256)])
    monkeypatch.setattr(store_mod, "load_evidence_index_strict", boom)
    assert await ledger.find_revoked(vc_session) is None


async def test_bound_records_never_truncated_by_cap(vc_session, monkeypatch):
    """P2#13：总量帽只作用于 draft；bound/revoked 永不驱逐。"""
    from app.services.visual_context.store import MAX_DRAFTS_PER_SESSION

    data, _, w, h = _derived_pair(seed=92)
    rec = _map_record(vc_session, derived_sha256=_sha(data),
                      derived_size=len(data), width=w, height=h)
    bound = await _register(vc_session, rec, data)
    await vc_store.bind_evidence_to_message(vc_session, [bound.evidence_id], 7)
    # 大量 draft 登记（超 12 上限若干倍；均在 512 内）。
    for i in range(24):
        d2, _, w2, h2 = _derived_pair(seed=120 + i)
        r2 = _map_record(vc_session, derived_sha256=_sha(d2),
                         derived_size=len(d2), width=w2, height=h2)
        await _register(vc_session, r2, d2)
    records = await vc_store.load_evidence_index(vc_session)
    assert any(r.evidence_id == bound.evidence_id and r.state == "bound"
               for r in records)
    drafts = [r for r in records if r.state == "draft"]
    assert len(drafts) <= MAX_DRAFTS_PER_SESSION
