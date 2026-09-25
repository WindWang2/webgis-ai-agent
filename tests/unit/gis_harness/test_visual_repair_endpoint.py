"""F15 user-approved visual repair 端点回归（ADR-0214 决策四）。

不变式：
1. plan 零突变（revision 不动）；闭包 ⊆ healer 四类呈现面微变异；
   不可自愈类诚实 skip（unmapped_category）；
2. apply 批准是结构门槛（缺省/False → 400 approval_required）；CAS 漂移
   → 409；收敛耗尽 → 200 hard_stop 诚实回执（非 5xx）；
3. user-wins：user-locked 图层上的 heal 被 guard 拒绝（layer_locked 原样
   浮出，提案/应用层不放大权限）；
4. 截图通道：ref-only 回执 + 确定性初筛（非 PNG / 超限 → 400）。
"""

from __future__ import annotations

import io
import shutil
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.core.auth import require_owned_session
from app.models.db_model import Conversation
from app.services.gis_harness.visual_observation.store import (
    MAX_SCREENSHOT_BYTES,
    SCREENSHOT_INDEX_KEY,
)
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    SetWorkbenchStateIntent,
    UpsertLayerIntent,
)
from app.services.session_data import session_data_manager
from app.services.session_plan import (
    ensure_session_plan_slot,
    save_session_plan,
)
from app.api.routes import visual_repairs as _mod


def _geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
             "properties": {}},
        ],
    }


def _visual_finding(entity="L1", code="visual_label_collision"):
    return {
        "domain": "visual",
        "code": code,
        "severity": "error",
        "source": "visual_observation_provider",
        "scope": "map",
        "affected_entity": entity,
        "evidence": "注记与 L1 overlap 重叠",
        "repair_class": "",
        "retryable": False,
        "blocks_completion": False,
        "degradation_only": True,
        "finding_class": "visual",
        "user_owned": False,
        "finding_id": f"visual:{code}:fp01",
        "recurrence_fingerprint": "fp-label-01",
    }


@pytest.fixture
def session_id():
    return f"vrepair-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def app(session_id):
    application = FastAPI()
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exceptions import RequestValidationError
    from app.core.exception import (
        unified_http_exception_handler,
        unified_validation_exception_handler,
    )

    application.add_exception_handler(
        StarletteHTTPException, unified_http_exception_handler)
    application.add_exception_handler(
        RequestValidationError, unified_validation_exception_handler)
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
async def _clean_session(session_id):
    await session_data_manager.clear_session(session_id)
    # 路由模块级 engine 的进程内 heal 收敛账本跨测试残留（同内容缺陷
    # 共享 content-fingerprint 预算 —— ADR-0186 已披露语义）；测试间清零
    # 保证隔离。
    _mod._engine._visual_heal_ledger.clear()
    yield
    _mod._engine._visual_heal_ledger.clear()
    await session_data_manager.clear_session(session_id)
    from app.services.mapspec.store import BASE_STORAGE_DIR

    d = BASE_STORAGE_DIR / session_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


async def _seed_session(session_id: str, *, lock_layer: bool = False) -> int:
    """种子：engine 初始化 + L1 图层 + map_product.visual_findings。"""
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(session_id, InitProjectIntent())
    # symbol + text-field：LabelCollisionResolver 的支持面（circle 等类型
    # 会被诚实 skip —— unsupported_layer_type 是 healer 的正确行为）。
    await engine.apply_mutation(
        session_id,
        UpsertLayerIntent(
            layer={"id": "L1", "source": "s1", "type": "symbol",
                   "layout": {"text-field": "{name}", "text-size": 14},
                   "paint": {}},
            source_data=_geojson(),
        ),
    )
    if lock_layer:
        await engine.apply_mutation(
            session_id,
            SetWorkbenchStateIntent(
                doc={"version": 5, "mode": "explore", "groups": [],
                     "lockedLayerIds": ["L1"]}),
        )
    plan = await ensure_session_plan_slot(session_id)
    plan.gis_chapter = {
        "plan_id": "p1",
        "query": "q",
        "map_product": {"visual_findings": [_visual_finding()]},
    }
    await save_session_plan(plan)
    state = await session_data_manager.get_map_state(session_id)
    return int(state.get("_cartographic_mutation_revision") or 0)


async def _plan(client, session_id: str, **payload):
    return await client.post(
        f"/api/v1/chat/sessions/{session_id}/visual-repairs/plan",
        json=payload or {},
    )


def _err(resp) -> str:
    """统一异常信封（dict detail → data 键，ADR-0138）。"""
    return str((resp.json().get("data") or {}).get("error") or "")


async def _apply(client, session_id: str, proposal_id: str, revision: int,
                 approved=True):
    return await client.post(
        f"/api/v1/chat/sessions/{session_id}/visual-repairs/apply",
        json={"proposal_id": proposal_id, "approved": approved,
              "expected_revision": revision},
    )


# ── plan ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_plan_without_visual_findings_is_honest(client, session_id):
    resp = await _plan(client, session_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposable"] is False
    assert body["reason"] == "no_visual_findings"


@pytest.mark.asyncio
async def test_plan_unhealable_category_is_skipped_honestly(
        client, session_id):
    plan = await ensure_session_plan_slot(session_id)
    plan.gis_chapter = {
        "plan_id": "p1", "query": "q",
        "map_product": {"visual_findings": [
            _visual_finding(code="visual_crop")]},
    }
    await save_session_plan(plan)
    resp = await _plan(client, session_id)
    body = resp.json()
    assert body["proposable"] is False
    assert body["reason"] == "no_healable_defects"
    assert body["skipped"][0]["reason"] == "unmapped_category"


@pytest.mark.asyncio
async def test_plan_is_mutation_free_and_bounded(client, session_id):
    revision = await _seed_session(session_id)
    resp = await _plan(client, session_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposable"] is True
    assert body["proposal_id"]
    assert body["base_revision"] == revision
    for op in body["ops"]:
        assert op["op"] in {"label_layout", "contrast_palette",
                            "layer_order", "opacity"}
    # plan 零突变：revision 不动。
    state = await session_data_manager.get_map_state(session_id)
    assert int(state.get("_cartographic_mutation_revision") or 0) == revision


@pytest.mark.asyncio
async def test_plan_targeted_filter_and_proposal_replay(client, session_id):
    await _seed_session(session_id)
    first = (await _plan(client, session_id)).json()
    second = (await _plan(client, session_id,
                          finding_ids=["visual:visual_label_collision:fp01"])
              ).json()
    assert first["proposable"] and second["proposable"]
    # 同缺陷集 + 同 revision → 同 proposal_id（幂等重放面）。
    assert first["proposal_id"] == second["proposal_id"]
    miss = (await _plan(
        client, session_id, finding_ids=["visual:nope:fpXX"])).json()
    assert miss["proposable"] is False
    assert miss["reason"] == "no_healable_defects"


# ── apply ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_requires_explicit_approval(client, session_id):
    revision = await _seed_session(session_id)
    planned = (await _plan(client, session_id)).json()
    resp = await _apply(client, session_id, planned["proposal_id"],
                        revision, approved=False)
    assert resp.status_code == 400
    assert _err(resp) == "approval_required"
    # 语义化批准门：approved 缺省也拒绝（FastAPI 默认 False → 同路径）。
    resp2 = await client.post(
        f"/api/v1/chat/sessions/{session_id}/visual-repairs/apply",
        json={"proposal_id": planned["proposal_id"],
              "expected_revision": revision},
    )
    assert resp2.status_code == 400


@pytest.mark.asyncio
async def test_apply_unknown_proposal_is_404(client, session_id):
    await _seed_session(session_id)
    resp = await _apply(client, session_id, "vrepair-ghost-000001", 1)
    assert resp.status_code == 404
    assert _err(resp) == "proposal_not_found"


@pytest.mark.asyncio
async def test_apply_stale_proposal_is_409(client, session_id):
    revision = await _seed_session(session_id)
    planned = (await _plan(client, session_id)).json()
    # plan 之后 spec 又前进了一代 → 提案陈旧。
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(
        session_id,
        UpsertLayerIntent(
            layer={"id": "L2", "source": "s1", "type": "circle",
                   "paint": {"circle-color": "#0f0"}},
            source_data=_geojson(),
        ),
    )
    resp = await _apply(client, session_id, planned["proposal_id"], revision)
    assert resp.status_code == 409
    assert _err(resp) == "proposal_stale"


@pytest.mark.asyncio
async def test_apply_stale_client_revision_is_409_revision_conflict(
        client, session_id):
    revision = await _seed_session(session_id)
    planned = (await _plan(client, session_id)).json()
    resp = await _apply(client, session_id, planned["proposal_id"],
                        revision + 99)
    assert resp.status_code == 409
    assert _err(resp) == "revision_conflict"


@pytest.mark.asyncio
async def test_apply_applies_closed_class_and_advances_revision(
        client, session_id):
    revision = await _seed_session(session_id)
    planned = (await _plan(client, session_id)).json()
    resp = await _apply(client, session_id, planned["proposal_id"], revision)
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is True
    assert body["reverify"] == "visual_repair"
    assert body["mutation_revision"] > revision
    # spec 已被 heal 推进（label layout patch 落在 L1）。
    engine = MapSpecLifecycleEngine()
    loaded = await engine.store.get_mapspec(session_id)
    l1 = next(ly for ly in loaded["layers"] if ly.get("id") == "L1")
    layout = l1.get("layout") or {}
    assert any(k.startswith("text-") for k in layout)


@pytest.mark.asyncio
async def test_user_locked_layer_is_disclosed_and_user_overrides(
        client, session_id):
    """user-wins 仓库语义：锁防 agent/system，不防用户本人（user origin
    是锁的唯一 override）；但批准必须发生在披露之后 —— plan 预览如实标注
    ``touches_locked``。"""
    revision = await _seed_session(session_id, lock_layer=True)
    planned = (await _plan(client, session_id)).json()
    assert planned["proposable"] is True
    assert all(op["touches_locked"] for op in planned["ops"])
    # 用户显式批准 → user origin heal 通过锁执行（知情 override）。
    resp = await _apply(client, session_id, planned["proposal_id"], revision)
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is True
    engine = MapSpecLifecycleEngine()
    loaded = await engine.store.get_mapspec(session_id)
    l1 = next(ly for ly in loaded["layers"] if ly.get("id") == "L1")
    layout = l1.get("layout") or {}
    assert layout.get("text-allow-overlap") is False


@pytest.mark.asyncio
async def test_agent_origin_stays_guarded_by_locks(client, session_id):
    """锁的本来语义：agent/system origin 的同一 heal 被 guard 拒绝
    （user locks 对自动路径绝对有效 —— F15 视觉修复不得绕过）。"""
    from app.services.gis_harness.visual_observation.repair_bridge import (
        visual_findings_to_defects,
    )
    from app.services.mapspec.visual_healer import VisualHealStrategyPlanner

    await _seed_session(session_id, lock_layer=True)
    engine = MapSpecLifecycleEngine()
    spec = await engine.store.get_mapspec(session_id)
    translation = visual_findings_to_defects(
        [_visual_finding()], known_layer_ids=["L1"])
    plan = VisualHealStrategyPlanner().plan(spec, translation["defects"])
    assert plan.ops, "closed-class plan must be plannable"
    result = await engine.apply_visual_heal_patch(
        session_id, translation["defects"], origin="system")
    assert result.is_error is True
    assert result.error_code == "layer_locked"


@pytest.mark.asyncio
async def test_apply_convergence_exhausted_is_hard_stop_not_5xx(
        client, session_id, monkeypatch):
    from app.services.mapspec.lifecycle_engine import MapSpecResult

    revision = await _seed_session(session_id)
    planned = (await _plan(client, session_id)).json()

    async def _exhausted(*args, **kwargs):
        return MapSpecResult(
            is_error=True,
            origin="user",
            error_code="HEAL_CONVERGENCE_EXHAUSTED",
            error_msg="exhausted",
            correction_hint="请人工研判。",
            mutation_revision=revision,
        )

    monkeypatch.setattr(_mod._engine, "apply_visual_heal_patch", _exhausted)
    resp = await _apply(client, session_id, planned["proposal_id"], revision)
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is False
    assert body["hard_stop"] is True
    assert body["reason"] == "convergence_exhausted"


# ── 截图通道（ref-only 纪律）───────────────────────────────────────────────

def _png_bytes(color=(30, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_screenshot_upload_registers_ref_only(client, session_id):
    await _seed_session(session_id)
    resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/visual-snapshots",
        params={"mapspec_revision": 3, "width": 64, "height": 48},
        files={"screenshot": ("map.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ref"].startswith("vshot-")
    assert len(body["sha256"]) == 64
    assert "base64" not in resp.text.lower()
    state = await session_data_manager.get_map_state(session_id)
    index = state.get(SCREENSHOT_INDEX_KEY)
    assert isinstance(index, list) and index[0]["ref"] == body["ref"]


@pytest.mark.asyncio
async def test_screenshot_upload_rejects_non_png_and_oversize(
        client, session_id):
    await _seed_session(session_id)
    resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/visual-snapshots",
        files={"screenshot": ("map.png", b"not a png at all", "image/png")},
    )
    assert resp.status_code == 400
    assert _err(resp) == "not_png"

    oversized = b"\x89PNG\r\n\x1a\n" + b"0" * (MAX_SCREENSHOT_BYTES + 2)
    resp2 = await client.post(
        f"/api/v1/chat/sessions/{session_id}/visual-snapshots",
        files={"screenshot": ("map.png", oversized, "image/png")},
    )
    assert resp2.status_code == 400
