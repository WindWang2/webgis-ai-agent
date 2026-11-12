"""G25 逐画幅观察接线测试（P6）。

覆盖：
- DTO `frames` 增维归一（封闭词表/≤16/非法→None、export_verified 拒绝）；
- `frame_product_status` 从存储的 `_cartographic_observation.frames` 构造
  observed → verify_frame_product 出真实 live_observed/snapshot/not_observed；
- revision 不一致 → stale_observation（旧 revision 不 verified，A11/A24）；
- frameA 完成不得补 frameB（逐帧行独立）。
"""
from __future__ import annotations

from typing import Any, Dict

import pytest




def _districts_fc(year_values: Dict[int, Dict[str, float]]) -> Dict[str, Any]:
    import json

    feats = []
    for y, values in year_values.items():
        for d, v in values.items():
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[104.0, 30.5], [104.1, 30.5], [104.1, 30.7], [104.0, 30.7], [104.0, 30.5]]]},
                "properties": {"year": y, "district": d, "per_10k": v},
            })
    return {"type": "FeatureCollection", "features": feats}


THREE_YEARS = _districts_fc({
    2020: {"锦江": 21.0, "武侯": 35.0, "高新": 48.0},
    2022: {"锦江": 25.0, "武侯": 33.0, "高新": 52.0},
    2024: {"锦江": 31.0, "武侯": 30.0, "高新": 44.0},
})


@pytest.fixture
async def ref_store(monkeypatch):
    async def _resolve_alias(session_id, ref_or_alias):
        return ref_or_alias

    async def _get_ref_descriptor(session_id, resolved):
        return {"content_revision": 7, "feature_count": 9}

    async def _get_ref_data_shared(session_id, resolved, owner_token=None):
        from types import SimpleNamespace

        return SimpleNamespace(success=True, error=None, error_type=None, data=THREE_YEARS)

    import app.services.session_data as sd

    monkeypatch.setattr(sd.session_data_manager, "resolve_alias", _resolve_alias)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_descriptor", _get_ref_descriptor)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_data_shared", _get_ref_data_shared)


async def _seed_and_bind(sid: str) -> Dict[str, Any]:
    from app.services.mapspec.intents import UpsertLayerIntent, UpsertSourceIntent
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.gis_harness.frame_products import service as fps

    await apply_gis_mutation(
        sid, UpsertSourceIntent(
            source_id="src-d", source={"type": "geojson", "inlineData": THREE_YEARS}),
        origin="agent", actor="test")
    await apply_gis_mutation(
        sid, UpsertLayerIntent(
            layer={"id": "lyr-districts", "type": "fill", "source": "src-d"}),
        origin="agent", actor="test")
    return await fps.bind_frame_product(
        sid, "ref:three", product_kind="time_series",
        field="per_10k", title="G25 观察接线",
    )


async def _store_observation(
    monkeypatch, sid: str, frames: list, mutation_revision: int,
    stamped_revision: int | None = None,
) -> None:
    """落一个假 _cartographic_observation + 当前 mutation revision。

    stamped_revision = 观察被服务端盖章时的 revision（默认同当前 —— 新鲜
    观察）；与当前不一致即 stale 场景（A11/A24）。
    """
    import app.services.session_data as sd

    state: Dict[str, Any] = {
        "_cartographic_observation": {
            "session_id": sid,
            "mapspec_revision": stamped_revision if stamped_revision is not None else mutation_revision,
            "frames": frames,
        },
        "_cartographic_mutation_revision": mutation_revision,
    }

    async def _get_map_state(session_id, *a, **k):
        return dict(state)

    monkeypatch.setattr(sd.session_data_manager, "get_map_state", _get_map_state)


class TestDtoFramesNormalization:
    def test_valid_rows_kept_invalid_dropped(self):
        from app.schemas.chat_schema import CartographicRuntimeObservationRequest

        payload: Dict[str, Any] = {
            "client_generation": 3,
            "mapspec_fingerprint": "f" * 32,
            "style_loaded": True,
            "frames": [
                {"frame_id": "fp-t-2020", "mode": "live_observed", "mapspec_revision": 9},
                {"frame_id": "fp-t-2022", "mode": "snapshot", "mapspec_revision": 9,
                 "fingerprint": "ab12cd34", "content_revision": 7},
                # 浏览器绝不冒充导出验证（export_verified 拒绝）。
                {"frame_id": "fp-bad", "mode": "export_verified"},
                {"frame_id": "fp-bad2", "mode": "made_up"},
                "not-a-dict",
            ],
        }
        req = CartographicRuntimeObservationRequest(**payload)
        assert req.frames is not None
        modes = {r["frame_id"]: r["mode"] for r in req.frames}
        assert modes == {"fp-t-2020": "live_observed", "fp-t-2022": "snapshot"}
        snap = next(r for r in req.frames if r["frame_id"] == "fp-t-2022")
        assert snap["fingerprint"] == "ab12cd34"
        assert snap["content_revision"] == 7

    def test_absent_or_garbage_is_none(self):
        from app.schemas.chat_schema import CartographicRuntimeObservationRequest

        base = {"client_generation": 1, "mapspec_fingerprint": "f" * 32, "style_loaded": True}
        assert CartographicRuntimeObservationRequest(**base).frames is None
        assert CartographicRuntimeObservationRequest(**base, frames="nope").frames is None
        assert CartographicRuntimeObservationRequest(**base, frames=[]).frames is None

    def test_bounded_to_16_latest(self):
        from app.schemas.chat_schema import CartographicRuntimeObservationRequest

        rows = [{"frame_id": f"f{i}", "mode": "snapshot"} for i in range(30)]
        req = CartographicRuntimeObservationRequest(
            client_generation=1, mapspec_fingerprint="f" * 32, style_loaded=True, frames=rows,
        )
        assert req.frames is not None
        assert len(req.frames) == 16
        assert req.frames[0]["frame_id"] == "f14"  # 最新 16 条


class TestStatusObservationWiring:
    async def test_live_snapshot_not_observed_independently(self, ref_store, monkeypatch):
        import uuid

        from app.services.gis_harness.frame_products import service as fps

        sid = f"g25-{uuid.uuid4().hex[:10]}"
        bound = await _seed_and_bind(sid)
        assert bound["status"] == "ok"
        # frameA live、frameB snapshot、frameC 未上报。
        await _store_observation(monkeypatch, sid, [
            {"frame_id": "fp-t-2020", "mode": "live_observed", "mapspec_revision": None},
            {"frame_id": "fp-t-2022", "mode": "snapshot", "mapspec_revision": None},
        ], mutation_revision=5)
        out = await fps.frame_product_status(sid)
        ver = out["verification"]
        by_id = {r["frame_id"]: r for r in ver["frames"]}
        assert by_id["fp-t-2020"]["observation"] == "live_observed"
        assert by_id["fp-t-2022"]["observation"] == "snapshot"
        assert by_id["fp-t-2024"]["observation"] == "not_observed"  # frameA 不补 frameB/C
        assert ver["summary"]["not_observed"] == 1

    async def test_stale_revision_not_verified(self, ref_store, monkeypatch):
        import uuid

        from app.services.gis_harness.frame_products import service as fps

        sid = f"g25-{uuid.uuid4().hex[:10]}"
        bound = await _seed_and_bind(sid)
        assert bound["status"] == "ok"
        # 观察盖章在旧 mutation revision（4）而当前是 6 → stale。
        await _store_observation(monkeypatch, sid, [
            {"frame_id": "fp-t-2020", "mode": "live_observed", "mapspec_revision": 4},
        ], mutation_revision=6, stamped_revision=4)
        out = await fps.frame_product_status(sid)
        by_id = {r["frame_id"]: r for r in out["verification"]["frames"]}
        assert by_id["fp-t-2020"]["observation"] == "stale_observation"
        assert "stale_observation:fp-t-2020" in out["verification"]["problems"]

    async def test_no_observation_all_not_observed(self, ref_store, monkeypatch):
        import uuid

        from app.services.gis_harness.frame_products import service as fps

        sid = f"g25-{uuid.uuid4().hex[:10]}"
        bound = await _seed_and_bind(sid)
        assert bound["status"] == "ok"
        await _store_observation(monkeypatch, sid, [], mutation_revision=5)
        out = await fps.frame_product_status(sid)
        ver = out["verification"]
        assert all(r["observation"] == "not_observed" for r in ver["frames"])
        assert ver["status"] == "incomplete"
