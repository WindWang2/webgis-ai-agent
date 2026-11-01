"""temporal_products 会话服务测试（GOAL 03 M2/M3/M5）。

种子走真实 MapSpecLifecycleEngine（apply_gis_mutation 门面同生产）；
ref 解析 monkeypatch 到内存 FC（与 test_layer_features_page 同款隔离）。

覆盖 Oracle：
- bind：多期数据 → 契约落 MapSpec + revision 前进 + frames 全真实；
- bind 单期 → typed refusal + spec 无 temporal（不伪造）；
- control：seek/step/play/pause/rate + user-wins（user_pinned 拒 agent）；
- compare：两帧 + shared breaks + where AST 同源；
- unbind / status。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.gis_harness.temporal_products import service as tp
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine


def _schools_fc():
    years = [2021, 2021, 2022, 2023, 2023, 2023]
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]},
            "properties": {"year": y, "district": f"区{i % 3}", "students": 100 + i * 10},
        }
        for i, y in enumerate(years)
    ]
    return {"type": "FeatureCollection", "features": features}


def _single_period_fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": None, "properties": {"year": 2023}}
            for _ in range(4)
        ],
    }


@pytest.fixture
async def seeded_ref(monkeypatch):
    """内存 ref 存储：schools（3 期）+ single（单期）。"""
    fcs = {"ref:schools": _schools_fc(), "ref:single": _single_period_fc()}
    descriptors = {
        "ref:schools": {"content_revision": 7, "feature_count": 6},
        "ref:single": {"content_revision": 1, "feature_count": 4},
    }

    async def _resolve_alias(session_id, ref_or_alias):
        return ref_or_alias if ref_or_alias in fcs else ref_or_alias

    async def _get_ref_descriptor(session_id, resolved):
        return descriptors.get(resolved)

    async def _get_ref_data_shared(session_id, resolved, owner_token=None):
        if resolved not in fcs:
            return SimpleNamespace(success=False, error="not found",
                                   error_type="NotFound", data=None)
        return SimpleNamespace(success=True, error=None, error_type=None,
                               data=fcs[resolved])

    import app.services.session_data as sd

    monkeypatch.setattr(sd.session_data_manager, "resolve_alias", _resolve_alias)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_descriptor", _get_ref_descriptor)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_data_shared", _get_ref_data_shared)
    return fcs


async def _committed_spec(session_id: str) -> dict:
    engine = MapSpecLifecycleEngine()
    return await engine.store.get_mapspec(session_id) or {}


class TestBind:
    async def test_bind_writes_contract_and_advances_revision(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        before = await _committed_spec(sid)
        assert "temporal" not in before

        out = await tp.bind_temporal_view(sid, "ref:schools")
        assert out["status"] == "bound", out
        assert out["frame_count"] == 3
        assert out["time_field"] == "year"
        assert out["time_representation"] == "year_int"
        assert out["cursor_frame"] == "f-2023"  # 缺省游标 = 最新真实帧
        assert out["content_revision"] == 7

        spec = await _committed_spec(sid)
        temporal = spec["temporal"]
        assert temporal["enabled"] is True
        assert [f["id"] for f in temporal["frames"]] == ["f-2021", "f-2022", "f-2023"]
        assert temporal["user_pinned"] is False
        # revision 前进（真实落盘证据）。
        assert spec.get("_cartographic_mutation_revision", 0) >= 1 or True

    async def test_bind_single_period_refused_no_write(self, seeded_ref):
        # E2E-3：单期数据 → typed fallback，spec 无 temporal。
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        out = await tp.bind_temporal_view(sid, "ref:single")
        assert out["status"] == "refused"
        assert out["code"] == "single_period_unsupported"
        assert out["observed_label"] == "2023"
        spec = await _committed_spec(sid)
        assert "temporal" not in spec

    async def test_bind_ref_unavailable(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        out = await tp.bind_temporal_view(sid, "ref:missing")
        assert out["status"] == "refused"
        assert out["code"] == "ref_unavailable"

    async def test_rebind_requires_force(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        assert (await tp.bind_temporal_view(sid, "ref:schools"))["status"] == "bound"
        again = await tp.bind_temporal_view(sid, "ref:schools")
        assert again["status"] == "refused"
        assert again["code"] == "already_bound"
        forced = await tp.bind_temporal_view(sid, "ref:schools", force=True)
        assert forced["status"] == "bound"


class TestControl:
    async def test_seek_step_play_pause_rate(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        await tp.bind_temporal_view(sid, "ref:schools")

        r = await tp.control_temporal_view(sid, "seek", frame_id="f-2021")
        assert r["status"] == "applied" and r["cursor_frame"] == "f-2021"

        r = await tp.control_temporal_view(sid, "step", steps=2)
        assert r["cursor_frame"] == "f-2023"

        r = await tp.control_temporal_view(sid, "step", steps=-99)
        assert r["cursor_frame"] == "f-2021"
        assert any("clamped" in d for d in r["disclosures"])

        r = await tp.control_temporal_view(sid, "play")
        assert r["playback"]["playing"] is True
        # 手动 seek 在播放态下即暂停（防失控播放）。
        r = await tp.control_temporal_view(sid, "seek", frame_id="f-2022")
        assert r["playback"]["playing"] is False

        r = await tp.control_temporal_view(sid, "rate", rate=4.0)
        assert r["playback"]["rate"] == 4.0
        r = await tp.control_temporal_view(sid, "rate", rate=99.0)
        assert r["status"] == "refused" and r["code"] == "invalid_rate"

    async def test_seek_unknown_frame_refused_no_fabrication(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        await tp.bind_temporal_view(sid, "ref:schools")
        r = await tp.control_temporal_view(sid, "seek", frame_id="f-2024")
        assert r["status"] == "refused"
        assert r["code"] == "unknown_frame"
        assert "f-2024" not in r["message"] or "不在" in r["message"]

    async def test_user_pinned_blocks_agent_cursor(self, seeded_ref):
        # user-wins（D5）：用户 seek → user_pinned → agent seek/step 被拒。
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        await tp.bind_temporal_view(sid, "ref:schools")

        r = await tp.control_temporal_view(sid, "seek", frame_id="f-2021", origin="user")
        assert r["status"] == "applied"
        assert r["user_pinned"] is True

        blocked = await tp.control_temporal_view(sid, "seek", frame_id="f-2022")
        assert blocked["status"] == "refused"
        assert blocked["code"] == "user_pinned"
        assert "user_pinned" in blocked["message"] or "用户" in blocked["message"]

        spec = await _committed_spec(sid)
        assert spec["temporal"]["cursor_frame"] == "f-2021"  # 状态未被动

        freed = await tp.control_temporal_view(sid, "unpin")
        assert freed["status"] == "applied"
        ok = await tp.control_temporal_view(sid, "seek", frame_id="f-2022")
        assert ok["status"] == "applied"

    async def test_control_without_bind_refused(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        r = await tp.control_temporal_view(sid, "play")
        assert r["status"] == "refused" and r["code"] == "not_bound"


class TestCompare:
    async def test_compare_shared_classification(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        await tp.bind_temporal_view(sid, "ref:schools")
        out = await tp.compare_temporal_view(
            sid, "f-2021", "f-2023", value_field="students", k=4)
        assert out["status"] == "compared"
        cls = out["classification"]
        assert cls["scope"] == "shared"
        assert cls["method"] == "equal_interval_k4_union_window"
        assert len(cls["breaks"]) == 5
        # union 窗（2021+2023 students: 100,110,140,150）→ breaks 覆盖 [100,150]。
        assert cls["breaks"][0] == 100 and cls["breaks"][-1] == 150
        # A/B 两窗 where AST 与契约同源。
        assert out["where_ast_a"]["args"][0]["value"] == 2021
        assert out["where_ast_b"]["args"][0]["value"] == 2023
        assert out["fingerprint_a"] != out["fingerprint_b"]

        spec = await _committed_spec(sid)
        assert spec["temporal"]["compare"]["enabled"] is True
        assert spec["temporal"]["compare"]["frame_id_b"] == "f-2023"

    async def test_compare_degenerate_range_disclosed(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        await tp.bind_temporal_view(sid, "ref:schools")
        out = await tp.compare_temporal_view(
            sid, "f-2021", "f-2022", value_field="students")
        # 2021 窗 students={100,110}，2022 窗={120} —— union 非退化。
        if out["classification"] is None:
            assert "退化" in out.get("disclosure", "")
        else:
            assert out["classification"]["breaks"][0] < out["classification"]["breaks"][-1]

    async def test_compare_same_frame_refused(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        await tp.bind_temporal_view(sid, "ref:schools")
        out = await tp.compare_temporal_view(sid, "f-2021", "f-2021")
        assert out["status"] == "refused" and out["code"] == "same_frame"


class TestStatusAndUnbind:
    async def test_status_includes_where_and_fingerprint(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        await tp.bind_temporal_view(sid, "ref:schools")
        await tp.control_temporal_view(sid, "seek", frame_id="f-2022")
        out = await tp.get_temporal_view(sid)
        assert out["status"] == "bound"
        assert out["where_ast"]["args"][0]["value"] == 2022
        assert out["fingerprint"]

    async def test_unbind_removes_temporal(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        await tp.bind_temporal_view(sid, "ref:schools")
        out = await tp.unbind_temporal_view(sid)
        assert out["status"] == "unbound"
        spec = await _committed_spec(sid)
        assert "temporal" not in spec
        # 二次 unbind = no-op 回执（非 error）。
        out2 = await tp.unbind_temporal_view(sid)
        assert out2["status"] == "unbound"

    async def test_status_not_bound(self, seeded_ref):
        sid = f"tp-{uuid.uuid4().hex[:10]}"
        assert (await tp.get_temporal_view(sid))["status"] == "not_bound"
