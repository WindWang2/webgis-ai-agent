"""G17 导入工作台 —— 纵向切片真实验收。

两个真实 GeoJSON 一次批次导入（任务书场景 2）：创建 staging → 成员枚举 →
解释计划 → 有界预览 → 提交到真实摄入管线 → ref 可读 → 幂等复用。
不 mock IngestPipeline/SessionStore（生产链路），DATA_DIR 隔离到 tmp。
"""
from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.services.data_import.service import DataImportError, DataImportService

pytestmark = pytest.mark.heavy

GEOJSON_A = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "alpha", "code": "00123"},
            "geometry": {"type": "Point", "coordinates": [116.397, 39.909]},
        },
        {
            "type": "Feature",
            "properties": {"name": "beta", "code": "00456"},
            "geometry": {"type": "Point", "coordinates": [116.407, 39.919]},
        },
    ],
}

GEOJSON_B = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"city": "shanghai"},
            "geometry": {"type": "LineString", "coordinates": [[121.4, 31.2], [121.5, 31.3]]},
        }
    ],
}

SESSION = "sess-g17-vertical-slice-0001"


@pytest.fixture()
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def service(isolated_data_dir):
    return DataImportService()


async def test_two_geojson_vertical_slice(service, isolated_data_dir):
    # 1. 创建批次（多文件，第二文件不再被忽略）
    state = await service.create_batch(
        SESSION,
        "user:test",
        [("a.geojson", json.dumps(GEOJSON_A).encode("utf-8")),
         ("b.geojson", json.dumps(GEOJSON_B).encode("utf-8"))],
    )
    assert state.status.value == "inspected"
    keys = {m.display_name: m.member_key for m in state.members}
    assert set(keys) == {"a.geojson", "b.geojson"}
    for m in state.members:
        assert m.feature_count == len(
            GEOJSON_A["features"] if m.display_name == "a.geojson" else GEOJSON_B["features"]
        )
        assert m.crs_source == "rfc7946_default"

    # 2. 计划：两个成员默认解释 + 勾选
    saved = await service.save_plan(
        state.batch_id,
        SESSION,
        member_configs={k: {} for k in keys.values()},
        selected=list(keys.values()),
    )
    assert saved.plan_version == 1
    assert set(saved.plan_hashes) == set(keys.values())
    plan_hashes_v1 = dict(saved.plan_hashes)

    # 3. 预览（有界样本；不建正式数据）
    pv = await service.preview_members(state.batch_id, SESSION, row_limit=10)
    assert len(pv["previews"]) == 2
    by_key = {p["member_key"]: p for p in pv["previews"]}
    pv_a = by_key[keys["a.geojson"]]
    assert pv_a["accepted_rows"] == 2 and pv_a["rejected_rows"] == 0
    assert pv_a["crs"]["confirmed"] is True
    assert pv_a["location_preview"] is not None
    # 预览前导零保留（默认 text 透传，不经数值通道）
    assert pv_a["sample_rows"][0]["code"] == "00123"

    # 4. 提交（同步路径）
    out = await service.start_commit(state.batch_id, SESSION, background=False)
    receipts = out["receipts"]
    assert out["status"] == "completed", receipts
    refs = {}
    for key, r in receipts.items():
        assert r["status"] == "completed", r
        assert r["output_ref"].startswith("ref:upload-")
        assert r["output_rows"] == 2 if key == keys["a.geojson"] else True
        refs[key] = r["output_ref"]
    assert refs[keys["a.geojson"]] != refs[keys["b.geojson"]]

    # 5. 成功可验证：ref 真实可读
    from app.services.session_data import session_data_manager

    data = await session_data_manager.get(SESSION, refs[keys["a.geojson"]])
    assert data is not None and "geojson" in data
    assert len(data["geojson"]["features"]) == 2
    # 前导零未经数值截断
    assert data["geojson"]["features"][0]["properties"]["code"] == "00123"

    # 6. 幂等：同计划重试复用既有产物（不重复登记）
    out2 = await service.start_commit(state.batch_id, SESSION, background=False)
    r2 = out2["receipts"][keys["a.geojson"]]
    assert r2["output_ref"] == refs[keys["a.geojson"]]
    assert r2["duplicate"] is True

    # 7. 变更解释（keep_fields）→ 新 plan_hash → 不同产物（场景 13 的机制）
    saved2 = await service.save_plan(
        state.batch_id, SESSION,
        member_configs={keys["a.geojson"]: {"keep_fields": ["name"]}},
        expected_plan_version=1,
    )
    assert saved2.plan_hashes[keys["a.geojson"]] != plan_hashes_v1[keys["a.geojson"]]
    out3 = await service.start_commit(state.batch_id, SESSION, member_keys=[keys["a.geojson"]], background=False)
    r3 = out3["receipts"][keys["a.geojson"]]
    assert r3["output_ref"] != refs[keys["a.geojson"]]
    data3 = await session_data_manager.get(SESSION, r3["output_ref"])
    assert set(data3["geojson"]["features"][0]["properties"]) == {"name"}


async def test_batch_lifecycle_and_expiry(service, isolated_data_dir):
    state = await service.create_batch(
        SESSION, "user:test",
        [("one.geojson", json.dumps(GEOJSON_A).encode("utf-8"))],
    )
    got = await service.get_batch(state.batch_id, SESSION)
    assert got.batch_id == state.batch_id
    listed = await service.list_batches(SESSION)
    assert any(b["batch_id"] == state.batch_id for b in listed)

    # 他人会话不可见（NOT_FOUND；不泄露存在性）
    with pytest.raises(DataImportError) as ei:
        await service.get_batch(state.batch_id, "sess-someone-else")
    assert ei.value.code == "NOT_FOUND"

    deleted = await service.delete_batch(state.batch_id, SESSION)
    assert deleted
    with pytest.raises(DataImportError):
        await service.get_batch(state.batch_id, SESSION)


async def test_unsupported_format_honest_disclosure(service, isolated_data_dir):
    state = await service.create_batch(
        SESSION, "user:test", [("notes.txt", b"plain text, not gis")]
    )
    assert len(state.members) == 1
    m = state.members[0]
    assert m.unsupported_reason == "unsupported_format"
    assert any("不支持" in n for n in state.notes)
    # 不支持成员不可配置计划
    with pytest.raises(DataImportError) as ei:
        await service.save_plan(state.batch_id, SESSION, {m.member_key: {}})
    assert ei.value.code == "MEMBER_UNSUPPORTED"
