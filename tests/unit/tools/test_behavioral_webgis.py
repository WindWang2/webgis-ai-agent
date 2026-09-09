"""行为化 dispatch 测试 —— webgis_* 制图/世界状态 6 工具。

真实依赖：MapSpec 生命周期引擎（lifecycle_engine）+ 磁盘存储
（BASE_STORAGE_DIR → tmp，与 tests/data/test_workspace_v4.py 的 session_base
fixture 同款）。会话 MapSpec 经 UpsertLayerIntent 真实提交（与
tests/unit/test_gis_world_state.py 同款种子手法），供 validate / checkpoint /
rollback / world_state 消费。

webgis_compile_maplibre 的编译路径要拉起 Node 子进程（jiti/npx）——测试
环境不依赖它，只钉 MapSpec 缺失时的快速失败语义。

注：webgis_* 的 args model 不声明 ``session_id``（合法参数表为空或仅
checkpoint_id）—— 会话 id 由 harness 经 ``dispatch(name, args, session_id)``
第三参注入（与生产路径一致）。
"""
import pytest

from app.tools.cartography_tools import register_mapspec_cartography_tools
from app.tools.registry import ToolRegistry
from app.tools.templates import register_template_tools
from app.services.gis_harness.tools import register_gis_harness_tools


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_mapspec_cartography_tools(reg)
    register_template_tools(reg)
    register_gis_harness_tools(reg)
    return reg


@pytest.fixture()
def session_base(tmp_path, monkeypatch):
    """MapSpec 存储根 → tmp（会话 spec/checkpoint/revision 落盘隔离）。"""
    from app.services.mapspec import store as mapspec_store_module

    base = tmp_path / "webgis-agent"
    base.mkdir()
    monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", base)
    return base


async def _commit_layer(sid: str, layer_id: str = "pop_layer") -> None:
    """给会话真实提交一个带内联数据的图层（非被测工具，是测试种子）。"""
    from app.services.mapspec.lifecycle_engine import (
        MapSpecLifecycleEngine,
        UpsertLayerIntent,
    )

    res = await MapSpecLifecycleEngine().apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={
                "id": layer_id,
                "type": "circle",
                "source": f"src-{layer_id}",
                "paint": {"circle-color": "#ff0000", "circle-radius": 6},
            },
            source_data={
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {"type": "Feature",
                         "geometry": {"type": "Point",
                                      "coordinates": [116.0 + i * 0.01, 39.9]},
                         "properties": {"name": f"p{i}", "pop": float(10 * (i + 1))}}
                        for i in range(5)
                    ],
                },
            },
        ),
    )
    assert not res.is_error, res


@pytest.mark.asyncio
async def test_webgis_map_combine_behavioral(registry, session_base):
    # validation：geojson 不在声明参数模型里 → 显式拒绝（unknown param 闸）
    bad = await registry.dispatch("webgis_map_combine", {
        "session_id": "bdw-combine", "geojson": {"type": "FeatureCollection"}})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"
    assert "geojson" in (bad.get("message") or "")

    # happy path：5 槽位规范组合 → MapSpec 组装（未提交会话）
    assembled = await registry.dispatch("webgis_map_combine", {
        "preset": "academic_research", "layer_id": "l1"})
    assert assembled.get("status") == "composite_map_assembled", assembled
    assert assembled.get("committed") is False
    mapspec = assembled.get("mapspec") or {}
    assert mapspec.get("version") == "1.0"
    assert mapspec.get("basemap", {}).get("providerId")
    assert (mapspec.get("layers") or [{}])[0].get("id") == "l1"
    assert assembled.get("summary")


@pytest.mark.asyncio
async def test_webgis_validate_behavioral(registry, session_base):
    # validation：缺 session_id → 失败应答
    bad = await registry.dispatch("webgis_validate", {})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert "session" in (bad.get("message") or "").lower()

    # error path：会话无 MapSpec → 未初始化错误
    missing = await registry.dispatch(
        "webgis_validate", {}, session_id="bdw-validate-fresh-xyz")
    assert isinstance(missing, dict) and missing.get("success") is False
    assert "MapSpec not found" in (missing.get("message") or "")

    # happy path：已提交 MapSpec → 规范性校验 + 指纹 + 制图评审
    sid = "bdw-validate"
    await _commit_layer(sid)
    out = await registry.dispatch("webgis_validate", {}, session_id=sid)
    assert isinstance(out, dict), out
    assert isinstance(out.get("errors"), list)
    assert isinstance(out.get("warnings"), list)
    assert out.get("mapspec_fingerprint")
    assert "cartographic_review" in out


@pytest.mark.asyncio
async def test_webgis_checkpoint_behavioral(registry, session_base):
    # validation：缺 session_id → 失败应答
    bad = await registry.dispatch("webgis_checkpoint", {})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert "session" in (bad.get("message") or "").lower()

    # happy path：对已提交 MapSpec 打快照 → checkpoint id + ref 计数
    sid = "bdw-checkpoint"
    await _commit_layer(sid)
    out = await registry.dispatch(
        "webgis_checkpoint", {"checkpoint_id": "cp-behavioral"}, session_id=sid)
    assert out.get("success") is True, out
    assert out.get("checkpoint_id") == "cp-behavioral"
    assert "ref_count" in out

    # 边界语义：对无任何图层的空白会话打快照 → 引擎自动建档成功
    # （ref_count=0，不报错 —— checkpoint 是幂等存档点，不是断言式校验）。
    blank = await registry.dispatch(
        "webgis_checkpoint", {"checkpoint_id": "cp-blank"}, session_id="bdw-cp-blank")
    assert isinstance(blank, dict) and blank.get("success") is True, blank
    assert blank.get("ref_count") == 0


@pytest.mark.asyncio
async def test_webgis_rollback_behavioral(registry, session_base):
    # validation：缺 checkpoint_id → 校验错误
    bad = await registry.dispatch("webgis_rollback", {}, session_id="bdw-x")
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert bad.get("code") == "VALIDATION_ERROR"

    sid = "bdw-rollback"
    await _commit_layer(sid)
    cp = await registry.dispatch("webgis_checkpoint", {}, session_id=sid)
    assert cp.get("success") is True, cp
    checkpoint_id = cp.get("checkpoint_id")
    assert checkpoint_id

    # error path：不存在的 checkpoint id → 引擎错误应答
    missing = await registry.dispatch(
        "webgis_rollback", {"checkpoint_id": "cp-ghost"}, session_id=sid)
    assert isinstance(missing, dict) and missing.get("success") is False, missing

    # happy path：回滚到真实 checkpoint → 成功 + 携带回滚后 MapSpec
    out = await registry.dispatch(
        "webgis_rollback", {"checkpoint_id": checkpoint_id}, session_id=sid)
    assert out.get("success") is True, out
    assert out.get("checkpoint_id") == checkpoint_id
    assert isinstance(out.get("mapspec"), dict)


@pytest.mark.asyncio
async def test_webgis_compile_maplibre_behavioral(registry, session_base):
    # validation：缺 session_id → 失败应答
    bad = await registry.dispatch("webgis_compile_maplibre", {})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert "session" in (bad.get("message") or "").lower()

    # error path：全新会话（本测试独占 id）无 MapSpec → 快速失败，
    # 不进入 Node 编译子进程。
    missing = await registry.dispatch(
        "webgis_compile_maplibre", {}, session_id="bdw-compile-fresh-xyz")
    assert isinstance(missing, dict) and missing.get("success") is False
    assert "MapSpec not found" in (missing.get("message") or "")


@pytest.mark.asyncio
async def test_webgis_world_state_behavioral(registry, session_base):
    # validation：缺 session_id → 失败应答
    bad = await registry.dispatch("webgis_world_state", {})
    assert isinstance(bad, dict) and bad.get("success") is False, bad
    assert "session" in (bad.get("message") or "").lower()

    # happy path：空会话也有可读快照（revision/summary，只读、无数据负载）
    fresh = await registry.dispatch("webgis_world_state", {"session_id": "bdw-ws"})
    assert fresh.get("success") is True, fresh
    assert "revision" in fresh
    assert isinstance(fresh.get("summary"), str) and fresh["summary"]

    # happy path：已提交图层 → 世界状态反映图层清单
    sid = "bdw-ws2"
    await _commit_layer(sid)
    out = await registry.dispatch("webgis_world_state", {"session_id": sid})
    assert out.get("success") is True, out
    assert out.get("layer_count_total") == 1
    layers = out.get("layers") or []
    assert layers and layers[0].get("id") == "pop_layer"
