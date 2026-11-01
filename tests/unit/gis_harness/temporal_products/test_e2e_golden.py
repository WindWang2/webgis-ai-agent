"""GOAL 03 E2E 黄金情境（M8）—— 可重复两遍的确定性端到端。

三情境（对应任务书用户场景）：
- E2E-1 五年学校增量与回放：bind→5 真实帧→seek→地图/统计同窗→reload 恢复；
- E2E-2 两期区级固定分级：compare 2023/2026（缺 2024-2025 披露）→
  breaks 固定→paint 跨帧不变（颜色范围变化 ≠ 真实变化的构造性保证）；
- E2E-3 单期拒动：bind 拒绝→无 temporal→atlas time 拒绝→无时间轴。

每情境连续执行两遍断言逐位一致（确定性/无 hidden state 泄漏）。
"""
from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.lib.cartography.atlas_job_spec import AtlasJobSpec
from app.lib.cartography.publication_ir import (
    AtlasPolicy,
    MapSpecSchemaError,
    plan_publication_pages,
)
from app.services.atlas_batch.planner import plan_atlas_binding
from app.services.feature_aggregate import aggregate_features
from app.services.gis_harness.temporal_products import service as tp
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine


def _fc_iso() -> Dict[str, Any]:
    """ISO 字符串跨年边界数据（[start,end) 语义锚点）。"""
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": None,
             "properties": {"ts": "2023-12-31T23:59:59Z"}},
            {"type": "Feature", "geometry": None,
             "properties": {"ts": "2024-01-01T00:00:00Z"}},
            {"type": "Feature", "geometry": None,
             "properties": {"ts": "2024-06-15T08:00:00Z"}},
        ],
    }


def _schools_fc(years: List[int]) -> Dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [104.0 + (i % 7) * 0.02, 30.6 + (i % 3) * 0.02]},
                "properties": {
                    "year": y,
                    "district": f"区{i % 3}",
                    "students": 100 + i * 10,
                },
            }
            for i, y in enumerate(years)
        ],
    }


def _districts_fc(years_with_counts: Dict[int, int]) -> Dict[str, Any]:
    """区县面 × 期（year 属性 + primaries 数量）——固定分级场景数据。"""
    feats = []
    for y, per_district in years_with_counts.items():
        for d in range(3):
            feats.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[104.0 + d * 0.1, 30.6], [104.1 + d * 0.1, 30.6],
                                     [104.1 + d * 0.1, 30.7], [104.0 + d * 0.1, 30.7],
                                     [104.0 + d * 0.1, 30.6]]],
                },
                "properties": {"year": y, "district": f"区{d}",
                               "primaries": per_district + d},
            })
    return {"type": "FeatureCollection", "features": feats}


@pytest.fixture
async def ref_store(monkeypatch):
    """内存 ref 存储（ schools 5 年 / districts 2 期 / single 单期 / missing 不可读）。"""
    fcs: Dict[str, Dict[str, Any]] = {
        "ref:schools": _schools_fc([2021, 2021, 2022, 2023, 2023, 2023, 2024, 2024, 2025, 2025]),
        "ref:districts": _districts_fc({2023: 10, 2026: 13}),
        "ref:single": _schools_fc([2023] * 4),
    }

    async def _resolve_alias(session_id, ref_or_alias):
        return ref_or_alias

    async def _get_ref_descriptor(session_id, resolved):
        if resolved not in fcs:
            return None
        return {"content_revision": 5,
                "feature_count": len(fcs[resolved]["features"])}

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


async def _spec(session_id: str) -> Dict[str, Any]:
    engine = MapSpecLifecycleEngine()
    return await engine.store.get_mapspec(session_id) or {}


# ── E2E-1：五年学校增量与回放 ────────────────────────────────────────────

async def _run_e2e1() -> Dict[str, Any]:
    sid = f"e2e1-{uuid.uuid4().hex[:10]}"
    # 1) bind：5 真实年帧（2021-2025），无虚构期。
    bind = await tp.bind_temporal_view(sid, "ref:schools")
    assert bind["status"] == "bound", bind
    frame_ids = [f["id"] for f in bind["frames"]]
    assert frame_ids == ["f-2021", "f-2022", "f-2023", "f-2024", "f-2025"]
    counts = {f["id"]: f["feature_count"] for f in bind["frames"]}
    assert counts == {"f-2021": 2, "f-2022": 1, "f-2023": 3, "f-2024": 2, "f-2025": 2}

    # 2) seek 2024：spec cursor + where AST。
    seek = await tp.control_temporal_view(sid, "seek", frame_id="f-2024")
    assert seek["status"] == "applied" and seek["cursor_frame"] == "f-2024"

    spec = await _spec(sid)
    assert spec["temporal"]["cursor_frame"] == "f-2024"

    # 3) 地图 ↔ 统计同窗：status 的 where AST 与 aggregate 通道同 AST
    #    → 计数 = 帧真值（2），全量（10）不冒充帧值。
    status = await tp.get_temporal_view(sid)
    where = status["where_ast"]
    assert where["terms"][0]["value"] == 2024 and where["terms"][1]["value"] == 2025
    fc = _schools_fc([2021, 2021, 2022, 2023, 2023, 2023, 2024, 2024, 2025, 2025])
    agg = aggregate_features(fc, group_by="district", agg="count",
                             agg_field=None, bbox=None, where=where, max_groups=16)
    assert agg["total_matching"] == 2
    assert agg["scanned"] == 10  # 全扫口径披露（诚实：不是只扫了 2 条）

    # 4) 播放推进到末帧（快帧取消/乱序由前端 controller 单测覆盖）。
    play = await tp.control_temporal_view(sid, "play")
    assert play["playback"]["playing"] is True
    step = await tp.control_temporal_view(sid, "step", steps=99)
    assert step["cursor_frame"] == "f-2025"
    assert any("clamped" in d for d in step["disclosures"])
    # 手动 seek 在播放态 → 停播（防失控）。
    seek2 = await tp.control_temporal_view(sid, "seek", frame_id="f-2021")
    assert seek2["playback"]["playing"] is False

    # 5) reload 恢复：新会话视角直接读 committed spec —— cursor 仍是落定值。
    spec_after = await _spec(sid)
    assert spec_after["temporal"]["cursor_frame"] == "f-2021"
    frames_persisted = [f["id"] for f in spec_after["temporal"]["frames"]]
    assert frames_persisted == frame_ids

    # 6) 指纹随 revision/帧敏感（图表/表格同窗锚点）。
    fp_a = status["fingerprint"]
    status_b = await tp.get_temporal_view(sid)  # cursor 现为 f-2021
    assert fp_a != status_b["fingerprint"]

    return {"cursor": spec_after["temporal"]["cursor_frame"],
            "frames": frames_persisted,
            "fingerprint_len": len(fp_a)}


@pytest.mark.asyncio
async def test_e2e1_five_year_playback_repeatable(ref_store):
    first = await _run_e2e1()
    second = await _run_e2e1()
    # 两遍逐位一致（确定性；指纹确定性长度/游标/帧序）。
    assert first["cursor"] == second["cursor"] == "f-2021"
    assert first["frames"] == second["frames"]
    assert first["fingerprint_len"] == second["fingerprint_len"] == 16


# ── E2E-2：两期区级固定分级 ─────────────────────────────────────────────

async def _run_e2e2() -> Dict[str, Any]:
    sid = f"e2e2-{uuid.uuid4().hex[:10]}"
    # 自然 agent 流程：先挂 choropleth 层（paint 快照 = 固定分级证据），
    # 再 temporal bind —— bind 从 spec 解析绑定图层 id（渲染面按图层 id
    # 注入时间过滤）。
    from app.services.mapspec.intents import UpsertLayerIntent
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, UpsertLayerIntent(layer={
        "id": "districts", "type": "fill", "source": "s1",
        "paint": {"fill-color": ["step", ["get", "primaries"],
                                 "#eff6ff", 11, "#bfdbfe", 14, "#60a5fa"]},
    }, source_data=_districts_fc({2023: 10, 2026: 13})))

    bind = await tp.bind_temporal_view(sid, "ref:districts", layer_id="districts")
    assert bind["status"] == "bound"
    # 缺 2024/2025 → 诚实披露（不内插帧）。
    assert "missing_period_gap" in bind["quality"]
    assert [f["label"] for f in bind["frames"]] == ["2023", "2026"]
    # 绑定层解析到 spec 图层 id（不是 ref 名）。
    assert bind["layers"][0]["layer_id"] == "districts"
    spec_before = await _spec(sid)
    paint_before = copy.deepcopy(
        next(lyr for lyr in spec_before["layers"] if lyr["id"] == "districts")["paint"])

    # compare：固定分级（union 窗 = primaries ∈ [10, 12]∪[13, 15]）。
    cmp_out = await tp.compare_temporal_view(
        sid, "f-2023", "f-2026", value_field="primaries", k=4)
    assert cmp_out["status"] == "compared"
    breaks = cmp_out["classification"]["breaks"]
    assert cmp_out["classification"]["scope"] == "shared"
    # union 域 [10, 15]（2023: 10,11,12；2026: 13,14,15）→ 等距 breaks 覆盖。
    assert breaks[0] == 10 and breaks[-1] == 15

    # 两窗 where AST + 指纹互异；计数与帧真值一致。
    fc = _districts_fc({2023: 10, 2026: 13})
    agg_a = aggregate_features(fc, group_by="district", agg="sum", agg_field="primaries",
                               bbox=None, where=cmp_out["where_ast_a"], max_groups=16)
    agg_b = aggregate_features(fc, group_by="district", agg="sum", agg_field="primaries",
                               bbox=None, where=cmp_out["where_ast_b"], max_groups=16)
    assert agg_a["total_matching"] == 3 and agg_b["total_matching"] == 3
    # 数据真实不同（分级不变 ≠ 无变化）：2023 各区 10/11/12，2026 各区 13/14/15。
    sums_a = {r["key"]: r.get("sum") for r in agg_a["rows"]}
    sums_b = {r["key"]: r.get("sum") for r in agg_b["rows"]}
    assert sums_a != sums_b
    assert sum(v for v in sums_a.values() if v) == 33
    assert sum(v for v in sums_b.values() if v) == 42

    # 颜色范围变化 ≠ 真实变化：seek 前后 paint 逐位不变（分级不随帧漂移）。
    await tp.control_temporal_view(sid, "seek", frame_id="f-2026")
    spec_after = await _spec(sid)
    paint_after = next(lyr for lyr in spec_after["layers"] if lyr["id"] == "districts")["paint"]
    assert paint_after == paint_before

    # atlas time driver：两页（每期一页）、跨页 classification shared 可比。
    plan = plan_atlas_binding(
        AtlasJobSpec(session_id=sid, atlas={"driver": "time",
                                            "atlas_title": "两期对照"}),
        spec_after)
    content_pages = [b for b in plan.bindings if not b.cover]
    assert [b.page_id for b in content_pages] == ["t-f-2023", "t-f-2026"]
    assert [b.feature_count for b in content_pages] == [3, 3]

    return {"breaks": breaks, "cursor": spec_after["temporal"]["cursor_frame"],
            "compare_b": spec_after["temporal"]["compare"]["frame_id_b"]}


@pytest.mark.asyncio
async def test_e2e2_two_period_fixed_classification_repeatable(ref_store):
    first = await _run_e2e2()
    second = await _run_e2e2()
    assert first == second  # 两遍逐位一致（breaks/游标/对照帧确定性）


# ── E2E-3：单期数据拒绝假动态 ───────────────────────────────────────────

async def _run_e2e3() -> Dict[str, Any]:
    sid = f"e2e3-{uuid.uuid4().hex[:10]}"
    bind = await tp.bind_temporal_view(sid, "ref:single")
    assert bind["status"] == "refused"
    assert bind["code"] == "single_period_unsupported"
    assert bind["observed_label"] == "2023"
    # spec 无 temporal（前端无时间轴可渲染）。
    spec = await _spec(sid)
    assert "temporal" not in spec
    # 控制类动作 typed 拒绝。
    control = await tp.control_temporal_view(sid, "play")
    assert control["code"] == "not_bound"
    # atlas time driver 同语义拒绝。
    doc = dict(spec)
    doc.setdefault("layers", [])
    with pytest.raises(MapSpecSchemaError) as ei:
        plan_publication_pages(doc, atlas=AtlasPolicy(driver="time"))
    assert ei.value.code == "atlas_time_no_temporal"
    # 被撤销 ref：bind typed 拒绝（不伪造）。
    missing = await tp.bind_temporal_view(sid, "ref:revoked")
    assert missing["status"] == "refused" and missing["code"] == "ref_unavailable"
    return {"code": bind["code"], "spec_has_temporal": "temporal" in spec}


@pytest.mark.asyncio
async def test_e2e3_single_period_refuses_fake_motion_repeatable(ref_store):
    first = await _run_e2e3()
    second = await _run_e2e3()
    assert first == second
    assert first["code"] == "single_period_unsupported"
    assert first["spec_has_temporal"] is False


# ── 日期边界（[start,end) 语义跨年） ────────────────────────────────────

@pytest.mark.asyncio
async def test_date_boundary_half_open(monkeypatch):
    """iso_string 数据 2023-12-31T23:59 与 2024-01-01T00:00 分属两帧。"""
    sid = f"e2eb-{uuid.uuid4().hex[:10]}"
    fc = _fc_iso()
    import app.services.session_data as sd

    async def _resolve(session_id, ref_or_alias):
        return ref_or_alias

    async def _desc(session_id, resolved):
        return {"content_revision": 1, "feature_count": len(fc["features"])}

    async def _data(session_id, resolved, owner_token=None):
        return SimpleNamespace(success=True, error=None, error_type=None, data=fc)

    monkeypatch.setattr(sd.session_data_manager, "resolve_alias", _resolve)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_descriptor", _desc)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_data_shared", _data)

    bind = await tp.bind_temporal_view(sid, "ref:iso", granularity="year")
    assert bind["status"] == "bound", bind
    assert bind["time_representation"] == "iso_string"
    counts = {f["id"]: f["feature_count"] for f in bind["frames"]}
    # 2023 帧含 12-31T23:59（<2024 边界）；2024 帧含 01-01T00:00（≥2024）。
    assert counts == {"f-2023": 1, "f-2024": 2}

