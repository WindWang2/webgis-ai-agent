"""DA-P1-1: build_layer_schema descriptor-first regression tests.

每轮 ambient context 的图层 inventory 行此前对新 ref 付全 payload 物化
（Redis GET + json.loads + O(features) 扫描）。descriptor 在 store 时已
一次性算好全部所需字段（ref 不可变 → 权威），命中即零物化。
"""
import pytest

from app.services.chat.context import layer_schema as ls
from app.services.session_data import session_data_manager


def _fc(n: int = 3) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.1, 30.6]},
                "properties": {"name": f"p{i}", "score": float(i), "ok": i % 2 == 0},
            }
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_descriptor_first_avoids_payload_materialization(monkeypatch):
    sid = "test-scheme-desc-first"
    ref_id = await session_data_manager.store(sid, _fc(), prefix="geojson")
    ls.clear_layer_schema_cache(sid)

    # 物化路径若被走到即失败 —— descriptor 命中时绝不允许全量 get()
    async def _fail_get(session_id, ref):
        raise AssertionError("payload materialized although descriptor was available")
        return None  # pragma: no cover

    monkeypatch.setattr(ls.session_data_manager, "get", _fail_get)

    schema = await ls.build_layer_schema(sid, ref_id)
    assert schema is not None
    assert schema["geom"] == "Point"
    assert schema["count"] == 3
    assert schema["fields"]["name"] == "string"
    assert schema["fields"]["score"] == "number"
    assert schema["fields"]["ok"] == "bool"
    assert isinstance(schema["bbox"], list) and len(schema["bbox"]) == 4


@pytest.mark.asyncio
async def test_fallback_to_payload_when_descriptor_has_no_fields(monkeypatch):
    """老 ref（descriptor 无 field_schema）回落物化路径并进缓存。"""
    sid = "test-scheme-fallback"
    ref_id = await session_data_manager.store(sid, _fc(), prefix="geojson")
    ls.clear_layer_schema_cache(sid)

    # 模拟老 descriptor：抹掉 field_schema
    store = session_data_manager._descriptors.get(sid, {})
    if ref_id in store:
        store[ref_id] = {**store[ref_id], "field_schema": None}

    schema = await ls.build_layer_schema(sid, ref_id)
    assert schema is not None
    assert schema["count"] == 3
    assert schema["fields"]["score"] == "number"

    # 第二次命中缓存（物化只允许发生一次）
    calls = {"n": 0}
    orig_get = session_data_manager.get

    async def _count_get(session_id, ref):
        calls["n"] += 1
        return await orig_get(session_id, ref)

    monkeypatch.setattr(ls.session_data_manager, "get", _count_get)
    again = await ls.build_layer_schema(sid, ref_id)
    assert again == schema
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_non_fc_ref_returns_none():
    sid = "test-scheme-nonfc"
    ref_id = await session_data_manager.store(sid, {"kind": "plan", "steps": []}, prefix="plan")
    ls.clear_layer_schema_cache(sid)
    assert await ls.build_layer_schema(sid, ref_id) is None


@pytest.mark.asyncio
async def test_style_injected_keys_are_not_fields():
    sid = "test-scheme-stylekeys"
    fc = _fc(2)
    for feat in fc["features"]:
        feat["properties"]["fill_color"] = "#ff0000"
        feat["properties"]["__style__"] = "x"
    ref_id = await session_data_manager.store(sid, fc, prefix="geojson")
    ls.clear_layer_schema_cache(sid)

    schema = await ls.build_layer_schema(sid, ref_id)
    assert schema is not None
    assert "fill_color" not in schema["fields"]
    assert "__style__" not in schema["fields"]
    assert "name" in schema["fields"]
