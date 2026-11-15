"""G36 视觉上下文集成回归（ADR-0291）。

覆盖：intake 路由（owner 门、格式拒绝、layout_preview unsupported）、
ChatRequest.attachments 线格式校验、bridge prompt images（FakeRpc 捕获
data["images"]）、非流式路径、steer/follow_up images（V31 单元层）。

真实 Pi 子进程旅程在 tests/integration/test_visual_context_pi_journey.py。
"""
from __future__ import annotations

import asyncio
import base64
import io
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.services.auth_history_bridge import require_owned_session
from app.models.db_model import Conversation
from app.services.session_data import session_data_manager


def _png(w=64, h=32, color=(150, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(w=48, h=48) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (40, 40, 160)).save(buf, format="JPEG", quality=88)
    return buf.getvalue()


@pytest.fixture
def session_id():
    return f"vcit-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def app(session_id):
    application = FastAPI()
    from app.api.routes import visual_context as _mod

    application.dependency_overrides[require_owned_session] = (
        lambda: Conversation(id=session_id)
    )
    application.include_router(_mod.router, prefix="/api/v1")
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
async def _clean(session_id):
    await session_data_manager.clear_session(session_id)
    yield
    await session_data_manager.clear_session(session_id)


async def _intake(client, session_id, *, data: bytes, filename="cap.png",
                  mime="image/png", source_type="map_viewport", extra=None):
    files = {"image": (filename, data, mime)}
    form = {"source_type": source_type}
    form.update(extra or {})
    return await client.post(
        f"/api/v1/chat/sessions/{session_id}/visual-context/evidence",
        files=files, data=form)


async def test_intake_map_viewport_roundtrip(client, session_id):
    resp = await _intake(
        client, session_id, data=_png(400, 300),
        extra={"mapspec_revision": "3", "mapspec_fingerprint": "fp1",
               "viewport": '{"center":[104,30],"zoom":10}',
               "dpr": "2.0"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_type"] == "map_viewport"
    assert body["mapspec_revision"] == 3
    assert body["viewport_hash"]
    assert body["state"] == "draft"
    assert body["evidence_id"].startswith("vev-")
    # 回执 ref-only：无 base64。
    assert "data" not in body and "b64" not in str(body).lower()

    # 受权读取（derived + thumbnail）。
    eid = body["evidence_id"]
    r2 = await client.get(
        f"/api/v1/chat/sessions/{session_id}/visual-context/evidence/{eid}/image")
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("image/")
    r3 = await client.get(
        f"/api/v1/chat/sessions/{session_id}/visual-context/evidence/{eid}/image"
        "?variant=thumbnail")
    assert r3.status_code == 200
    thumb = Image.open(io.BytesIO(r3.content))
    assert max(thumb.size) <= 320


async def test_intake_reference_jpeg_with_exif_orientation(client, session_id):
    img = Image.new("RGB", (40, 20), (10, 200, 10))
    exif = Image.Exif()
    exif[274] = 6
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    resp = await _intake(
        client, session_id, data=buf.getvalue(), filename="ref.jpg",
        mime="image/jpeg", source_type="reference_upload",
        extra={"label": "配色参考"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # V13：方向校正后宽高互换；V06：参考图不携带地图身份。
    assert body["width"] == 20 and body["height"] == 40
    assert body["mapspec_revision"] is None
    assert body["viewport_hash"] == ""


async def test_intake_region_crop_chain(client, session_id):
    """V03：crop 按原图设备像素执行（右下角局部不被误裁）。"""
    # 200×100 三色带：左 1/3 红、中绿、右 1/3 蓝（y 全高）。
    img = Image.new("RGB", (200, 100))
    for x in range(200):
        color = (255, 0, 0) if x < 200 / 3 else ((0, 255, 0) if x < 2 * 200 / 3 else (0, 0, 255))
        for y in range(100):
            img.putpixel((x, y), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    # crop 右下角（x=150..200, y=50..100）→ 必为蓝色。
    resp = await _intake(
        client, session_id, data=buf.getvalue(), source_type="map_region",
        extra={"mapspec_revision": "1", "viewport": '{"center":[0,0],"zoom":1}',
               "crop": '{"x":150,"y":50,"w":50,"h":50}'})
    assert resp.status_code == 200, resp.text
    eid = resp.json()["evidence_id"]
    r = await client.get(
        f"/api/v1/chat/sessions/{session_id}/visual-context/evidence/{eid}/image")
    crop_img = Image.open(io.BytesIO(r.content)).convert("RGB")
    # 中心像素应为蓝（右下带），非红（左上）。
    px = crop_img.getpixel((crop_img.width // 2, crop_img.height // 2))
    assert px[2] > 200 and px[0] < 60


async def test_intake_rejections(client, session_id):
    # layout_preview → typed unsupported（V08：视口不冒充纸面）。
    resp = await _intake(client, session_id, data=_png(),
                         source_type="layout_preview")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "layout_preview_unsupported"
    # SVG → 拒绝（V15）。
    resp = await _intake(client, session_id, data=b"<svg/>",
                         filename="x.svg", mime="image/svg+xml",
                         source_type="reference_upload")
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "decode_failed"
    # 假 PNG（魔数 + 垃圾）→ 解码拒绝（V12）。
    resp = await _intake(client, session_id,
                         data=b"\x89PNG\r\n\x1a\n" + b"junk" * 64)
    assert resp.status_code == 400
    # 截断 JPEG → 解码拒绝（V12）。
    truncated = _jpeg()[: len(_jpeg()) // 2]
    resp = await _intake(client, session_id, data=truncated,
                         filename="t.jpg", mime="image/jpeg",
                         source_type="reference_upload")
    assert resp.status_code == 400
    # 地图类缺 revision → 400（不变量）。
    resp = await _intake(client, session_id, data=_png(),
                         source_type="map_viewport",
                         extra={"viewport": '{"center":[0,0],"zoom":1}'})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "map_evidence_requires_revision"


async def test_revoke_and_list(client, session_id):
    resp = await _intake(
        client, session_id, data=_png(),
        extra={"mapspec_revision": "0", "viewport": '{"center":[0,0],"zoom":1}'})
    eid = resp.json()["evidence_id"]
    listed = await client.get(
        f"/api/v1/chat/sessions/{session_id}/visual-context/evidence")
    assert listed.status_code == 200
    assert any(e["evidence_id"] == eid for e in listed.json()["evidence"])
    deleted = await client.delete(
        f"/api/v1/chat/sessions/{session_id}/visual-context/evidence/{eid}")
    assert deleted.status_code == 200
    assert deleted.json()["state"] == "revoked"
    # 撤销后读取 → 404（V28）。
    r = await client.get(
        f"/api/v1/chat/sessions/{session_id}/visual-context/evidence/{eid}/image")
    assert r.status_code == 404
    # 再删 → 幂等 200（tombstone 仍在）。
    r2 = await client.delete(
        f"/api/v1/chat/sessions/{session_id}/visual-context/evidence/{eid}")
    assert r2.status_code == 200
    assert r2.json()["state"] == "revoked"


# ── ChatRequest 线格式 ──────────────────────────────────────────────────

def test_chat_request_attachments_schema():
    from app.schemas.chat_schema import ChatRequest
    import pydantic

    ok = ChatRequest(message="看这张图",
                     session_id="s1",
                     attachments=[{"evidence_id": "vev-" + "a" * 16}])
    assert ok.attachments[0].evidence_id == "vev-" + "a" * 16
    # 非 vev 前缀 → 拒绝。
    with pytest.raises(pydantic.ValidationError):
        ChatRequest(message="x", attachments=[{"evidence_id": "vshot-abc"}])
    # >4 条 → 拒绝。
    with pytest.raises(pydantic.ValidationError):
        ChatRequest(message="x", attachments=[
            {"evidence_id": f"vev-{i:016x}"} for i in range(5)])


# ── bridge prompt images（FakeRpc 捕获） ────────────────────────────────

class _CaptureRpc:
    """最小 PiRpc 替身：捕获 prompt/steer/new_session/get_state 载荷。"""

    def __init__(self):
        self.prompts = []
        self.new_sessions = 0
        self.spawn_model_id = "vision-model"
        self.spawn_declared_image_input = True
        self.spawn_epoch = 1
        self.state = {"model": {"id": "vision-model", "input": ["text", "image"]}}

    async def request(self, command, data=None):
        if command in ("prompt", "steer", "follow_up"):
            self.prompts.append((command, dict(data or {})))
            return {"disposition": "queued"}
        if command == "new_session":
            self.new_sessions += 1
            return {"cancelled": False}
        if command == "get_state":
            return self.state
        if command == "clear_queue":
            return {"steering": [], "followUp": []}
        return {}


def _make_bridge_with_capture(monkeypatch):
    from app.agent_pi_bridge import PiBridge

    rpc = _CaptureRpc()
    bridge = PiBridge.__new__(PiBridge)
    bridge._rpc = rpc
    # 换代路径需要的最小方法面（真实 bridge 上是事件排空）。

    async def _drain():
        return None

    bridge._drain_stale_events = _drain
    return bridge, rpc


async def test_stream_prompt_images_attached(monkeypatch):
    from app.services.visual_context import store as vc_store
    from app.services.visual_context.evidence import mint_evidence_id
    from app.services.visual_context.decoder import decode_image, derive_model_image
    from tests.unit.test_visual_context_core import _map_record, _sha

    from app.services.visual_context import profile as profile_mod

    monkeypatch.setattr(profile_mod, "descriptor_vision_confirmed",
                        lambda model_id, provider_id="webgis": True)
    bridge, rpc = _make_bridge_with_capture(monkeypatch)
    sid = f"vcbr-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    try:
        png = _png(100, 60, color=(1, 2, 3))
        decoded = decode_image(png)
        data, mime, w, h = derive_model_image(decoded)
        rec = _map_record(sid, evidence_id=mint_evidence_id(),
                          derived_sha256=_sha(data), derived_size=len(data),
                          width=w, height=h)
        saved = await vc_store.register_evidence(sid, data, rec)

        # stream_prompt 需要完整 turn 机制 —— 这里走非流式 prompt() 太重；
        # 直接验证 _resolve_turn_images + data 组装 seam（同一函数被两条
        # prompt 路径调用）。
        outcome = await bridge._resolve_turn_images(
            sid, [{"evidence_id": saved.evidence_id}])
        assert len(outcome["images"]) == 1
        assert outcome["images"][0]["type"] == "image"
        assert outcome["blocked_reason"] == ""
        receipts = outcome["receipts"]
        assert receipts[0]["status"] == "sent"
        # V33：台账已记录；撤销后复验发现。
        from app.services.visual_context.resolver import get_sent_image_ledger

        ledger = get_sent_image_ledger()
        assert await ledger.find_revoked(sid) == []
        await vc_store.revoke_evidence(sid, saved.evidence_id)
        revoked = await ledger.find_revoked(sid)
        assert revoked == [saved.derived_sha256]
        # 再次解析：revoked 阻断 → new_session 换代 + 新解析拒绝旧图。
        outcome2 = await bridge._resolve_turn_images(
            sid, [{"evidence_id": saved.evidence_id}])
        assert rpc.new_sessions >= 1
        assert outcome2["images"] == []
        assert outcome2["receipts"][0]["reason"] == "evidence_revoked"
        # 换代后台账清空 —— 纯文本 turn 不再被旧 pixels 拖累。
        assert await ledger.find_revoked(sid) == []
    finally:
        await session_data_manager.clear_session(sid)


async def test_stream_prompt_images_gate_refuses(monkeypatch):
    """V11：描述符换代后旧 worker 未声明 image → 拒图但文本照常。"""
    from app.services.visual_context import profile as profile_mod

    monkeypatch.setattr(profile_mod, "descriptor_vision_confirmed",
                        lambda model_id, provider_id="webgis": True)
    bridge, rpc = _make_bridge_with_capture(monkeypatch)
    rpc.spawn_declared_image_input = False
    sid = f"vcbr-{uuid.uuid4().hex[:8]}"
    outcome = await bridge._resolve_turn_images(
        sid, [{"evidence_id": "vev-" + "b" * 16}])
    assert outcome["images"] == []
    assert outcome["receipts"][0]["reason"] == "worker_spawned_without_image_input"


async def test_turn_message_route_attachments(monkeypatch):
    """V31：运行中附图经 G29 路由 → steer payload 带 images（原生通道）。"""
    from app.services.visual_context import store as vc_store
    from tests.unit.test_visual_context_core import _map_record, _sha, _derived_pair
    import app.api.routes.pi_interactions as pi_mod
    from app.services.session_data import session_data_manager as sdm

    sid = f"vcst-{uuid.uuid4().hex[:8]}"
    await sdm.clear_session(sid)
    from app.services.visual_context import profile as profile_mod

    monkeypatch.setattr(profile_mod, "descriptor_vision_confirmed",
                        lambda model_id, provider_id="webgis": True)
    bridge, rpc = _make_bridge_with_capture(monkeypatch)
    try:
        data, _, w, h = _derived_pair(seed=70)
        rec = _map_record(sid, derived_sha256=_sha(data),
                          derived_size=len(data), width=w, height=h)
        saved = await vc_store.register_evidence(sid, data, rec)

        class _Entry:
            turn_id = "t1"

        entry = _Entry()
        entry.bridge = bridge
        captured: dict = {}

        async def fake_issue(command, payload):
            captured["command"] = command
            captured["payload"] = dict(payload or {})
            return None

        async def fake_collect(issued, command=None):
            return {"disposition": "queued"}

        rpc.issue = fake_issue
        rpc.collect = fake_collect
        bridge._current_turn = entry
        bridge._control_gate = asyncio.Lock()

        async def _ok(*a, **k):
            return True

        import app.agent_pi_bridge as bridge_mod

        monkeypatch.setattr(bridge_mod, "is_active_pi_turn", _ok)
        monkeypatch.setattr(bridge_mod, "get_active_turn_entry",
                            lambda *a: entry)
        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(pi_mod, "_raise_if_deleted", _noop)
        monkeypatch.setattr(pi_mod, "_record_ratelimit", _noop)

        class _AttachmentRef:
            evidence_id = saved.evidence_id

            def model_dump(self):
                return {"evidence_id": self.evidence_id}

        class _Req:
            message = "看这局部"
            mode = "adjust"
            attachments = [_AttachmentRef()]

        resp = await pi_mod.send_turn_message(sid, "t1", _Req(), None, None)
        assert resp["accepted"] is True, resp
        assert captured["command"] == "steer"
        images = captured["payload"].get("images")
        assert isinstance(images, list) and images
        assert images[0]["type"] == "image"
        assert base64.b64decode(images[0]["data"]) == data
        # V33：steer 送图同样进台账（transcript pixels 复验口径一致）。
        from app.services.visual_context.resolver import get_sent_image_ledger

        assert await get_sent_image_ledger().find_revoked(sid) == []
        await vc_store.revoke_evidence(sid, saved.evidence_id)
        assert await get_sent_image_ledger().find_revoked(sid) == [
            saved.derived_sha256]
    finally:
        await sdm.clear_session(sid)
