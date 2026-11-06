"""数据准备 E2E V1 —— M8 合成城市场景（学校 CSV + 行政区 polygon + 分母缺失）。

用户故事：「把我上传的 CSV 学校数据和行政区 shp 合起来，先检查坐标和字段，
再统计各区小学数量，准备做图」。本测试用同一合成城市（确定性 seed）走完：
上传面（session ref）→ 检查/提案/澄清 → 计划（映射+过滤+对调修正）→ 幂等执行
（原件不可变）→ 绑定写回 → 跨「重启」恢复（新 store 实例读回）→ 数据替换后
旧指纹拒复用（STALE_INPUT）→ 分母缺失的诚实降级披露。

不mock 任何被测层：走 service/plan/executor 真实实现（HTTP 层在
test_preparation_api_v1 已覆盖；工具层在 test_data_preparation_tools_v1 已覆盖）。
"""
from __future__ import annotations

import pytest

from app.lib.data.profile import DatasetProfileV3, profile_features
from app.lib.gis.dataset_descriptor import SourceRef
from app.services.data_preparation.executor import ExecError, execute_plan
from app.services.data_preparation.plan import (
    AxisSwapStep,
    FieldMappingStep,
    FilterRowsStep,
    build_plan,
    save_plan,
)
from app.services.data_preparation.service import (
    bind_field_roles,
    inspect_source,
)
from app.services.data_preparation.vocabulary import (
    FINDING_AXIS_ORDER_SUSPECT,
    FINDING_DENOMINATOR_ABSENT,
)
from app.services.dataset_semantics import (
    derive_descriptor_from_v3,
    get_dataset_semantic_store,
    reset_dataset_semantic_store,
)
from app.services.session_data import session_data_manager

# ── 合成城市（确定性；成都 bbox 口径，与 gis_samples 同域但按本场景定制）──

DISTRICTS = ["锦江区", "青羊区", "金牛区", "武侯区", "成华区"]
DISTRICT_BOXES = {
    "锦江区": (104.08, 30.62, 104.13, 30.67),
    "青羊区": (104.02, 30.65, 104.07, 30.70),
    "金牛区": (104.02, 30.70, 104.07, 30.75),
    "武侯区": (104.02, 30.58, 104.07, 30.63),
    "成华区": (104.08, 30.67, 104.13, 30.72),
}


def _schools_fc(n=40, swap=False, bad_every=0):
    """学校 POI：中文字段、确定性坐标按区分布；swap=True 模拟经纬对调上传。"""
    features = []
    for i in range(n):
        d = DISTRICTS[i % len(DISTRICTS)]
        x0, y0, x1, y1 = DISTRICT_BOXES[d]
        lon = x0 + (x1 - x0) * ((i * 37) % 100) / 100
        lat = y0 + (y1 - y0) * ((i * 53) % 100) / 100
        if bad_every and i % bad_every == 0:
            lon, lat = 999.0, 999.0
        coords = [lat, lon] if swap else [lon, lat]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coords},
            "properties": {
                "学校编号": f"S{i:03d}",
                "学校名称": f"{d}第{(i // 5) + 1}小学",
                "办学类型": "小学" if i % 4 else "九年一贯制",
                "所属区县": d,
                "经度": lat if swap else lon,
                "纬度": lon if swap else lat,
                "建校年份": str(1950 + (i * 7) % 70),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _districts_fc():
    """行政区 polygon FC（矩形近似，唯一 adcode）。"""
    features = []
    for i, d in enumerate(DISTRICTS):
        x0, y0, x1, y1 = DISTRICT_BOXES[d]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]},
            "properties": {"行政区划代码": f"51010{i + 1}", "区县名": d,
                           "常住人口": 60000 + i * 12000},
        })
    return {"type": "FeatureCollection", "features": features}


async def _store_with_descriptor(session_id, fc, prefix="test"):
    payload = {"geojson": fc, "crs": "EPSG:4326"}
    ref = await session_data_manager.store(session_id, payload, prefix=prefix)
    vp, _q = profile_features(fc["features"], crs="EPSG:4326")
    profile = DatasetProfileV3(target_ref=ref, category="vector",
                               crs="EPSG:4326", extent=vp.extent, vector=vp)
    descriptor = derive_descriptor_from_v3(
        profile, dataset_key=ref, features=fc["features"],
        source_refs=[SourceRef(type="ref", ref=ref)])
    put = await get_dataset_semantic_store().put(session_id, ref, descriptor)
    assert put.ok
    return ref, put.fingerprint


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)
    reset_dataset_semantic_store()
    yield
    reset_dataset_semantic_store()


class TestCityScenarioE2E:
    async def test_schools_csv_happy_path_to_prepared(self):
        """两阶段真实旅程：坏行先污染坐标列值域（lon/lat 被反证退 value）→
        过滤执行后重查 → 中文坐标映射恢复正确；角色写回可恢复；原件不变。"""
        sid = "sess-e2e-city-1"
        ref, fp = await _store_with_descriptor(sid, _schools_fc(40, bad_every=10))

        # ── 阶段 1：带脏数据的检查 ──
        result = await inspect_source(sid, ref)
        top = {p.field: (p.top.role if p.top else None)
               for p in result["proposals"]}
        # 非坐标角色不受坏行影响：中文映射正确
        assert top["学校编号"] == "id"
        assert top["学校名称"] == "name"
        assert top["所属区县"] == "admin_code"
        # 坏行（999）污染 min/max 值域 → lon/lat 候选被值域反证（诚实拒绝，
        # 不是猜）——这是引导用户先修数据的信号
        assert top["经度"] != "lon" and top["纬度"] != "lat"
        codes = [f.code for f in result["receipt"].findings]
        assert "impossible_coordinates" in codes
        assert FINDING_DENOMINATOR_ABSENT in codes

        # ── 阶段 2：过滤坏行 → 重查 → 坐标映射恢复 ──
        fields = ["学校编号", "学校名称", "办学类型", "所属区县", "经度", "纬度", "建校年份"]
        plan = build_plan(
            session_id=sid, owner="user-1", input_ref=ref, input_fingerprint=fp,
            steps=[
                FieldMappingStep(role="id", field="学校编号", source="user"),
                FieldMappingStep(role="name", field="学校名称", source="user"),
                FieldMappingStep(role="admin_code", field="所属区县", source="user"),
                FilterRowsStep(condition_code="impossible_coordinates",
                               max_drop_frac=0.2),
            ],
            available_fields=fields)
        await save_plan(plan)
        receipt = await execute_plan(plan)
        assert receipt.row_before == 40 and receipt.row_after == 36
        # 原件不可变
        original = await session_data_manager.get(sid, ref)
        assert len(original["geojson"]["features"]) == 40

        after = await inspect_source(sid, receipt.output_ref)
        top2 = {p.field: (p.top.role if p.top else None)
                for p in after["proposals"]}
        assert top2["经度"] == "lon"
        assert top2["纬度"] == "lat"
        assert not after["receipt"].blocked

        # ── 绑定写回 + 跨「重启」恢复（reset 单例 = 新进程视角）──
        bind = await bind_field_roles(sid, ref, {
            "学校编号": "id", "学校名称": "name", "所属区县": "admin_code"})
        assert set(bind["bound_fields"]) == {"学校编号", "学校名称", "所属区县"}
        reset_dataset_semantic_store()
        rec = await get_dataset_semantic_store().get(sid, ref)
        assert rec.ok
        roles = {f.name: (f.roles, f.role_confidence) for f in rec.descriptor.fields}
        assert roles["所属区县"][0] == ["admin_dimension"]
        assert roles["所属区县"][1] == "user_declared"
        # prepared ref 的 descriptor 同样可恢复（谱系身份）
        rec2 = await get_dataset_semantic_store().get(sid, receipt.output_ref)
        assert rec2.ok and rec2.descriptor.descriptor_fingerprint

    async def test_swapped_coordinates_block_then_fix(self):
        """xy 对调上传：blocked 收据 → 用户确认 AxisSwap → 坐标修正落位。"""
        sid = "sess-e2e-city-2"
        ref, fp = await _store_with_descriptor(sid, _schools_fc(20, swap=True))
        result = await inspect_source(sid, ref)
        sus = [f for f in result["receipt"].findings
               if f.code == FINDING_AXIS_ORDER_SUSPECT]
        assert sus and sus[0].severity.value == "blocked"
        assert result["receipt"].blocked

        # 无证据构造 swap 步 → 拒绝；带收据证据 → 放行
        with pytest.raises(Exception):
            build_plan(session_id=sid, owner="u", input_ref=ref,
                       input_fingerprint=fp,
                       steps=[AxisSwapStep(lon_field="经度", lat_field="纬度")])
        plan = build_plan(
            session_id=sid, owner="u", input_ref=ref, input_fingerprint=fp,
            steps=[AxisSwapStep(lon_field="经度", lat_field="纬度")],
            axis_suspect_evidence=sus[0].evidence)
        await save_plan(plan)
        receipt = await execute_plan(plan)
        assert receipt.row_before == receipt.row_after == 20   # 对调不改行数
        # 修正后的数据检查健康
        after = await inspect_source(sid, receipt.output_ref)
        assert not after["receipt"].blocked
        assert after["receipt"].coordinate_range[0] > 100       # 经度回到成都带

    async def test_districts_dataset_roles_for_join(self):
        """行政区数据：adcode/人口分母角色提案 + 绑定（统计各区小学数量的 B 端）。"""
        sid = "sess-e2e-city-3"
        ref, fp = await _store_with_descriptor(sid, _districts_fc())
        result = await inspect_source(sid, ref)
        top = {p.field: (p.top.role if p.top else None)
               for p in result["proposals"]}
        assert top["行政区划代码"] == "admin_code"
        assert top["区县名"] == "admin_code"
        assert top["常住人口"] == "denominator"
        # 有分母 → 无 DENOMINATOR_ABSENT 披露
        codes = [f.code for f in result["receipt"].findings]
        assert FINDING_DENOMINATOR_ABSENT not in codes
        bind = await bind_field_roles(sid, ref, {
            "行政区划代码": "admin_code", "常住人口": "denominator"})
        assert "常住人口" in bind["bound_fields"]

    async def test_replacement_invalidates_old_plan(self):
        """数据替换后旧计划不得复用（STALE_INPUT）—— 旧指纹拒执行。"""
        sid = "sess-e2e-city-4"
        ref, fp = await _store_with_descriptor(sid, _schools_fc(20))
        plan = build_plan(
            session_id=sid, owner="u", input_ref=ref, input_fingerprint=fp,
            steps=[FieldMappingStep(role="name", field="学校名称", source="user")],
            available_fields=["学校编号", "学校名称", "经度", "纬度"])
        await save_plan(plan)
        # 替换：覆写 + 新 descriptor（指纹变化）
        await session_data_manager.overwrite(sid, ref,
                                             {"geojson": _schools_fc(35),
                                              "crs": "EPSG:4326"})
        payload = await session_data_manager.get(sid, ref)
        vp, _q = profile_features(payload["geojson"]["features"], crs="EPSG:4326")
        profile = DatasetProfileV3(target_ref=ref, category="vector",
                                   crs="EPSG:4326", extent=vp.extent, vector=vp)
        descriptor = derive_descriptor_from_v3(
            profile, dataset_key=ref,
            features=payload["geojson"]["features"],
            source_refs=[SourceRef(type="ref", ref=ref)])
        put = await get_dataset_semantic_store().put(sid, ref, descriptor)
        assert put.ok and put.fingerprint != fp
        with pytest.raises(ExecError) as ei:
            await execute_plan(plan)
        assert ei.value.code == "STALE_INPUT"

    async def test_idempotent_rerun_across_restart(self):
        """执行后「重启」（reset store 单例）再执行同 hash 计划 → 同 receipt。"""
        sid = "sess-e2e-city-5"
        ref, fp = await _store_with_descriptor(sid, _schools_fc(15, bad_every=5))
        plan = build_plan(
            session_id=sid, owner="u", input_ref=ref, input_fingerprint=fp,
            steps=[FilterRowsStep(condition_code="impossible_coordinates",
                                  max_drop_frac=0.3)],
            available_fields=["学校编号", "学校名称", "经度", "纬度"])
        await save_plan(plan)
        r1 = await execute_plan(plan)
        reset_dataset_semantic_store()   # 模拟新进程
        r2 = await execute_plan(plan)
        assert r2.output_ref == r1.output_ref
        assert r2.created_at == r1.created_at
