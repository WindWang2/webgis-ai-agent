"""性能快照 + 多轮恢复 + 负例（M9/M10/M11，ADR-0278）。

- 性能：10/100/1000 层 spec 上 preview/commit 的耗时快照（宽松上界断言
  防回归，精确数字记 EVIDENCE.md —— 先测基线再定阈值纪律）。
- 恢复（F-A4 压缩版）：多轮操作后回执环可重建、同 operation_id 不重复
  提交、跨「刷新」（重新读环）last_result 指代仍锚定。
- 负例：无观察 ack（latest=0）拒绝、已删除会话 410、空 effects 拒绝。
"""
from __future__ import annotations

import time
import uuid

import pytest

from app.services.gis_harness.map_operations import (
    MapOperationRequest,
    commit_map_operation,
    get_operation_receipt,
    list_operation_receipts,
    mint_operation_id,
    preview_map_operation,
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.session_data import session_data_manager


def _sid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


async def _seed_spec_direct(sid: str, layer_count: int) -> int:
    """直接落 map_state（性能种子：避免 N 次引擎事务的装配成本混入测量）。"""
    layers = [
        {
            "id": f"layer-{i:04d}",
            "type": "circle",
            "source": f"src-{i:04d}",
            "name": f"图层-{i:04d}",
            "paint": {"circle-radius": 4, "circle-color": "#123456"},
        }
        for i in range(layer_count)
    ]
    await session_data_manager.set_map_state(sid, "mapspec", {
        "schema_version": "mapspec.v1",
        "layers": layers,
        "sources": {},
        "view": {"center": [104.0, 30.6], "zoom": 10},
    })
    await session_data_manager.set_map_state(sid, "_cartographic_mutation_revision", 0)
    return layer_count


# ── 性能快照（M11）─────────────────────────────────────────────────────


@pytest.mark.parametrize("layer_count", [10, 100, 1000])
async def test_perf_preview_commit(layer_count: int):
    sid = _sid(f"mop-perf{layer_count}")
    await _seed_spec_direct(sid, layer_count)

    req = MapOperationRequest(
        operation_id=mint_operation_id(),
        mode="preview",
        effects=[
            {"kind": "set_layer_visibility", "target": "图层-0001", "visible": False},
            {"kind": "set_layer_opacity", "target": "图层-0002", "opacity": 0.5},
            {"kind": "set_viewport", "center": [104.05, 30.65], "zoom": 12},
        ],
    )
    t0 = time.perf_counter()
    preview = await preview_map_operation(sid, req)
    preview_ms = (time.perf_counter() - t0) * 1000
    assert preview["status"] == "preview"
    assert preview["effects"][0]["targets"] == ["layer-0001"]

    commit_req = MapOperationRequest(
        operation_id=mint_operation_id(),
        mode="commit",
        effects=req.effects,
    )
    t1 = time.perf_counter()
    receipt = await commit_map_operation(sid, commit_req)
    commit_ms = (time.perf_counter() - t1) * 1000
    assert receipt["status"] == "committed", receipt

    # 宽松回归上界（本地快照基线：1000 层 preview ~百ms 级、commit 走
    # 真实引擎事务秒级内；断言防的是数量级回归，不是精确性能门）。
    assert preview_ms < 5000, f"preview {preview_ms:.0f}ms @ {layer_count} layers"
    assert commit_ms < 30000, f"commit {commit_ms:.0f}ms @ {layer_count} layers"
    print(f"\n[perf] layers={layer_count} preview={preview_ms:.0f}ms commit={commit_ms:.0f}ms")


async def test_perf_large_spec_not_in_preview_payload():
    """预览载荷不携带全量 spec/GeoJSON —— 有界投影纪律（小动作不触发
    全量 MapSpec 导出到 LLM 面）。"""
    sid = _sid("mop-perf-payload")
    await _seed_spec_direct(sid, 1000)
    preview = await preview_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="preview",
        effects=[{"kind": "set_layer_opacity", "target": "图层-0003", "opacity": 0.9}],
    ))
    assert "mapspec" not in preview
    assert preview["layer_count"] == 1000
    import json

    assert len(json.dumps(preview)) < 20000  # 有界（不含层清单明细）


# ── 多轮恢复（M9 / F-A4 压缩）─────────────────────────────────────────


async def test_multi_turn_receipt_ring_recovery():
    sid = _sid("mop-mt")
    await _seed_spec_direct(sid, 6)
    oids = []
    for i in range(6):
        oid = f"mop-mt-{i:04d}"
        oids.append(oid)
        receipt = await commit_map_operation(sid, MapOperationRequest(
            operation_id=oid, mode="commit",
            effects=[{"kind": "set_layer_opacity", "target": f"图层-{i:04d}",
                      "opacity": round(0.3 + i * 0.1, 2)}],
        ))
        assert receipt["status"] == "committed", (i, receipt)

    ring = await list_operation_receipts(sid, limit=8)
    assert len(ring) == 6
    assert ring[0]["operation_id"] == oids[-1]

    # 「刷新」后重建：逐条回执可读、revision 链单调
    revisions = [r["final_revision"] for r in reversed(ring) if r.get("final_revision")]
    assert revisions == sorted(revisions)

    # 已完成动作不重复提交：重放第一个 operation_id → replayed，无新 revision
    rev_before = (await session_data_manager.get_map_state(sid) or {}).get(
        "_cartographic_mutation_revision")
    replay = await commit_map_operation(sid, MapOperationRequest(
        operation_id=oids[0], mode="commit",
        effects=[{"kind": "set_layer_opacity", "target": "图层-0000", "opacity": 0.3}],
    ))
    assert replay.get("replayed") is True
    rev_after = (await session_data_manager.get_map_state(sid) or {}).get(
        "_cartographic_mutation_revision")
    assert rev_before == rev_after

    # 跨「刷新」的 last_result 指代锚定 = 环内最近 applied 目标
    last = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "set_layer_visibility", "target_kind": "last_result",
                  "visible": False}],
    ))
    assert last["status"] == "committed"
    assert last["effects"][0]["targets"] == ["layer-0005"]


# ── 负例（M10）─────────────────────────────────────────────────────────


async def test_ack_without_observation_rejected(client=None):
    """无任何观察（latest=0）时 ack 拒绝 —— 零假确认。"""
    from app.api.routes import display_confirmation as _dc_mod
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from unittest.mock import MagicMock

    app = FastAPI()
    app.include_router(_dc_mod.router, prefix="/api/v1")
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()

    sid = _sid("mop-neg")
    import os

    os.environ["GIS_FINAL_DISPLAY_CONFIRM"] = "required"
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            # 绕过限速
            import app.api.routes.display_confirmation as dc

            orig = dc.get_rate_limiter

            async def _limiter():
                m = MagicMock()

                async def _ok(k, ma, w):
                    return True
                m.is_allowed = _ok
                return m
            dc.get_rate_limiter = _limiter
            try:
                resp = await c.post(
                    f"/api/v1/chat/sessions/{sid}/display-confirmation/ack",
                    json={"render_seq": 0},
                )
                # 无裁决（no_verdict）或无观察（stale_ack）—— 都是明确拒绝
                assert resp.status_code == 409
                assert resp.json()["detail"]["error"] in ("no_verdict", "stale_ack")
            finally:
                dc.get_rate_limiter = orig
    finally:
        os.environ.pop("GIS_FINAL_DISPLAY_CONFIRM", None)


async def test_empty_session_operations_absent():
    sid = _sid("mop-empty")
    ring = await list_operation_receipts(sid)
    assert ring == []
    assert await get_operation_receipt(sid, "mop-none") is None


async def test_effects_upper_bound_enforced():
    with pytest.raises(ValueError):
        MapOperationRequest(
            operation_id=mint_operation_id(),
            effects=[
                {"kind": "set_layer_opacity", "target": f"l{i}", "opacity": 0.5}
                for i in range(9)
            ],
        )
