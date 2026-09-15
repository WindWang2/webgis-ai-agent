"""ADR-0195：空间反幻觉与地理红线安全守护引擎 单测套件（TDD 先行）。

覆盖映射（docs/dev/spatial-guardrails-spec.md §9）：
- T1 经纬度倒置：20 组故意倒置坐标（12 组硬信号 + 8 组软信号），
  断言全部检出、corrected 精确等于 [lng, lat]、置信度分档正确；
- T2 陆地掩膜：公海/大洋建筑点位 → GeographicImpossibilityError；
  近岸置信带不硬杀；内陆水体降级警示；
- T3 行政区划：虚构 6 位码第一道防线截获；模糊容错修正与归属纠偏；
- T4 红线围栏与 bbox 面积预算；L4 拓扑自洽（瞬移线/退化面）；
- T5 编排网关 verdict（GeoJSON FeatureCollection 全层扫描）；
- T6 挂载点：ToolDispatchService（BLOCK 不执行工具 / AUTO_FLIP 改写参数）；
- T7 挂载点：lifecycle_engine.apply_mutation（锁前拦截 + 纠偏）；
- T8 完全离线守护（socket 封禁）、kill switch、fail-open；
- T9 性能预算：单点校验 p95 < 5ms。

全部离线：引擎仅依赖标准库，无任何网络调用（T8 用 socket 封禁证明）。
"""
import json
import socket
import time
import uuid

import pytest

from app.services.spatial_guardrails import (
    GeographicImpossibilityError,
    GuardrailConfig,
    GuardLevel,
    SpatialGuardrails,
    TopologyImplausibilityError,
    auto_flip,
    classify_point,
    detect_pair,
    guardrails_enabled,
)
from app.services.spatial_guardrails.admin_division_verifier import (
    assert_valid_code,
    resolve_name,
    verify_code,
)
from app.services.spatial_guardrails.errors import (
    FabricatedAdminDivisionError,
    GeofenceRedlineViolationError,
)
from app.services.spatial_guardrails.landmask_validator import (
    validate_facility_point,
)
from app.services.spatial_guardrails.redlines import (
    RedlineRegistry,
    check_area_budget,
)
from app.services.spatial_guardrails.topology_checks import (
    validate_linestring,
    validate_polygon,
)

# ---------------------------------------------------------------------------
# 样本集：20 组故意倒置的经纬度（(城市, 倒置输入 [lat, lng], 期望修正 [lng, lat])）
# 12 组硬信号（倒置后第二元 >90，值域矛盾，置信度 1.0）
# 8 组软信号（两种解释均数值合法，靠海陆证据对比，置信度 0.6–0.95）
# ---------------------------------------------------------------------------
HARD_INVERTED = [
    ("北京", [39.9042, 116.4074], [116.4074, 39.9042]),
    ("上海", [31.2304, 121.4737], [121.4737, 31.2304]),
    ("广州", [23.1291, 113.2644], [113.2644, 23.1291]),
    ("成都", [30.5728, 104.0668], [104.0668, 30.5728]),
    ("哈尔滨", [45.8038, 126.5350], [126.5350, 45.8038]),
    ("香港", [22.3193, 114.1694], [114.1694, 22.3193]),
    ("西安", [34.3416, 108.9398], [108.9398, 34.3416]),
    ("沈阳", [41.8057, 123.4315], [123.4315, 41.8057]),
    ("武汉", [30.5928, 114.3055], [114.3055, 30.5928]),
    ("东京", [35.6762, 139.6503], [139.6503, 35.6762]),
    ("悉尼", [-33.8688, 151.2093], [151.2093, -33.8688]),
    ("惠灵顿", [-41.2866, 174.7756], [174.7756, -41.2866]),
]

SOFT_INVERTED = [
    ("乌鲁木齐", [43.8256, 87.6168], [87.6168, 43.8256]),
    ("喀什", [39.4704, 75.9898], [75.9898, 39.4704]),
    ("亚的斯亚贝巴", [9.0250, 38.7469], [38.7469, 9.0250]),
    ("雷克雅未克", [64.1466, -21.9426], [-21.9426, 64.1466]),
    ("都柏林", [53.3498, -6.2603], [-6.2603, 53.3498]),
    ("开普敦", [-33.9249, 18.4241], [18.4241, -33.9249]),
    ("巴马科", [12.6392, -8.0029], [-8.0029, 12.6392]),
    ("巴西利亚", [-15.7975, -47.8919], [-47.8919, -15.7975]),
]

ALL_INVERTED = HARD_INVERTED + SOFT_INVERTED


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def _point_feature(lng, lat, props=None):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
        "properties": dict(props or {}),
    }


# ---------------------------------------------------------------------------
# T1 经纬度倒置检测与自愈
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("city,inverted,expected", ALL_INVERTED, ids=[c for c, _, _ in ALL_INVERTED])
def test_t1_inverted_pair_detected_and_autorepaired(city, inverted, expected):
    report = detect_pair(inverted)
    assert report.is_inverted, f"{city}: 倒置坐标未被检出"
    assert report.corrected is not None
    assert list(report.corrected) == expected, f"{city}: 修正值不等于预期 [lng, lat]"
    assert 0.0 <= report.confidence <= 1.0


@pytest.mark.parametrize("city,inverted,expected", HARD_INVERTED, ids=[c for c, _, _ in HARD_INVERTED])
def test_t1_hard_signal_confidence_is_full(city, inverted, expected):
    """值域矛盾（倒置后第二元 >90）是硬证据，置信度必须为 1.0。"""
    report = detect_pair(inverted)
    assert report.confidence == pytest.approx(1.0)
    assert report.evidence.get("hard_signal") is True


@pytest.mark.parametrize("city,inverted,expected", SOFT_INVERTED, ids=[c for c, _, _ in SOFT_INVERTED])
def test_t1_soft_signal_confidence_band(city, inverted, expected):
    """软信号（海陆证据对比）置信度落在自适应区间，且证据里保留前后分区。"""
    report = detect_pair(inverted)
    assert 0.60 <= report.confidence <= 0.95
    assert report.evidence.get("as_given_zone") is not None
    assert report.evidence.get("flipped_zone") is not None


def test_t1_valid_pair_not_flagged():
    report = detect_pair([116.4074, 39.9042])
    assert not report.is_inverted
    assert report.corrected is None


def test_t1_ambiguous_both_land_not_flipped():
    """两种解释都落在陆地上（如莫斯科倒置）→ 证据不足，不自动翻转。"""
    report = detect_pair([55.7558, 37.6173])
    assert not report.is_inverted
    assert report.corrected is None


def test_t1_auto_flip_combines_detect_and_repair():
    repaired, report = auto_flip([39.9042, 116.4074])
    assert repaired == [116.4074, 39.9042]
    assert report.is_inverted and report.confidence >= 0.6

    # 低置信（两可）不翻转：原样返回
    unchanged, report2 = auto_flip([55.7558, 37.6173])
    assert unchanged == [55.7558, 37.6173]
    assert not report2.is_inverted


def test_t1_out_of_range_lat_hard_flip():
    """lat 槽位数值 >90 的硬信号在 auto_flip 下同样修复。"""
    repaired, report = auto_flip([-33.8688, 151.2093])
    assert repaired == [151.2093, -33.8688]
    assert report.confidence == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# T2 陆地掩膜与设施常识
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("lng,lat", [(-150.0, 0.0), (-30.0, 30.0), (80.0, -20.0), (-120.0, -50.0)])
@pytest.mark.parametrize("kind", ["school", "hospital", "building", "poi"])
def test_t2_deep_ocean_facility_blocked(lng, lat, kind):
    with pytest.raises(GeographicImpossibilityError):
        validate_facility_point(lng, lat, facility_kind=kind)


def test_t2_land_facility_passes():
    validate_facility_point(116.4074, 39.9042, facility_kind="school")
    validate_facility_point(104.0668, 30.5728, facility_kind="hospital")


def test_t2_coastal_band_not_blocked():
    """近岸置信带（东海 ~110km 处）不确定 → 不硬杀。"""
    zone = classify_point(123.0, 30.8)
    assert zone != "OCEAN_CONFIDENT"
    validate_facility_point(123.0, 30.8, facility_kind="school")


def test_t2_inland_water_body_degrades_to_warning():
    """里海中心的教学点 → 水体警示而非硬阻断（粗掩膜不冤杀真实站点）。"""
    assert classify_point(51.0, 41.0) == "WATER_BODY"
    issues = validate_facility_point(51.0, 41.0, facility_kind="school")
    assert issues and any(i.code == "WATER_BODY_FACILITY" for i in issues)


def test_t2_unknown_facility_kind_on_ocean_only_warns():
    """未知设施类型的深海点 → 可疑警示（L2 不误杀航标/浮台类合法要素）。"""
    issues = validate_facility_point(-150.0, 0.0, facility_kind=None)
    assert any(i.code == "SUSPICIOUS_OCEAN_POINT" for i in issues)


# ---------------------------------------------------------------------------
# T3 行政区划校验
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "code", ["999999", "190000", "000000", "990000", "82", "1234567", "abc123", "11000a"]
)
def test_t3_fabricated_code_caught_at_first_line(code):
    with pytest.raises(FabricatedAdminDivisionError):
        assert_valid_code(code)


def test_t3_valid_province_and_prefecture_codes_known():
    r1 = verify_code("110000")
    assert r1.ok and r1.known and r1.name == "北京市"
    r2 = verify_code("440300")
    assert r2.ok and r2.known and "广东" in "".join(r2.parent_chain)


def test_t3_structurally_valid_unknown_code_degrades_to_warning():
    """省段合法但地级段未收录（如 123456）→ 结构合法，降级警示不硬杀。"""
    r = verify_code("123456")
    assert r.ok
    assert not r.known
    assert r.issue_code == "UNKNOWN_BUT_PLAUSIBLE_ADMIN_CODE"


def test_t3_parent_attribution_mismatch_corrected():
    r = verify_code("440300", claimed_parent="广西壮族自治区")
    assert r.issue_code == "ADMIN_PARENT_MISMATCH"
    assert r.suggestion is not None and "广东" in r.suggestion


def test_t3_parent_attribution_match_passes():
    r = verify_code("440300", claimed_parent="440000")
    assert r.ok and r.issue_code is None


def test_t3_name_resolution_and_fuzzy_suggestion():
    assert resolve_name("北京") == "110000"
    assert resolve_name("深圳") == "440300"
    r = verify_code("440309")
    assert not r.known
    assert r.suggestion is not None and "440300" in r.suggestion


# ---------------------------------------------------------------------------
# T4 红线围栏 / 面积预算 / 拓扑
# ---------------------------------------------------------------------------
def test_t4_bbox_area_budget_blocks_mega_requests():
    with pytest.raises(GeofenceRedlineViolationError):
        check_area_budget([-10.0, -10.0, 10.0, 10.0])
    check_area_budget([-0.1, -0.1, 0.1, 0.1])


def test_t4_redline_zone_json_enforcement(tmp_path):
    zone_file = tmp_path / "redlines.json"
    zone_file.write_text(
        json.dumps(
            {
                "zones": [
                    {
                        "name": "campus-restricted",
                        "ring": [[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]],
                        "policy": "no_fetch",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry = RedlineRegistry.from_json(str(zone_file))
    with pytest.raises(GeofenceRedlineViolationError):
        registry.check_bbox([-0.5, -0.5, 0.5, 0.5], action="fetch")
    registry.check_bbox([10.0, 10.0, 11.0, 11.0], action="fetch")  # 围栏外放行


def test_t4_teleport_linestring_blocked():
    with pytest.raises(TopologyImplausibilityError):
        validate_linestring([[116.4, 39.9], [16.4, 9.9]])
    validate_linestring([[116.4, 39.9], [116.5, 40.0]])


def test_t4_degenerate_polygon_blocked():
    with pytest.raises(TopologyImplausibilityError):
        validate_polygon([[[116.4, 39.9], [116.5, 39.9], [116.4, 39.9], [116.4, 39.9]]])
    with pytest.raises(TopologyImplausibilityError):
        validate_polygon([[[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]])
    validate_polygon(
        [[[116.4, 39.9], [116.6, 39.9], [116.6, 40.1], [116.4, 40.1], [116.4, 39.9]]]
    )


# ---------------------------------------------------------------------------
# T5 编排网关 verdict
# ---------------------------------------------------------------------------
def test_t5_gateway_blocks_ocean_facility_geojson():
    gw = SpatialGuardrails()
    fc = _fc(
        _point_feature(116.4074, 39.9042, {"kind": "school", "adcode": "110000"}),
        _point_feature(-150.0, 0.0, {"kind": "hospital"}),
    )
    verdict = gw.check_geojson(fc)
    assert not verdict.passed
    blocking = verdict.blocking()
    assert any(i.code == "OCEAN_POINT_ON_LAND_FACILITY" for i in blocking)
    assert any(i.level == GuardLevel.L2_GEOGRAPHIC_BOUNDS for i in blocking)


def test_t5_gateway_auto_flips_and_mutates_args():
    gw = SpatialGuardrails()
    args = {"facility": "school", "location": [39.9042, 116.4074]}
    verdict = gw.check_tool_args("poi_search", args)
    assert verdict.passed
    assert verdict.mutated
    assert args["location"] == [116.4074, 39.9042]
    flip_issues = [i for i in verdict.issues if i.code == "LATLON_INVERTED_AUTO_FLIP"]
    assert flip_issues and flip_issues[0].evidence["confidence"] >= 0.6


def test_t5_gateway_blocks_fabricated_adcode():
    gw = SpatialGuardrails()
    verdict = gw.check_tool_args("region_stats", {"adcode": "999999"})
    assert not verdict.passed
    assert verdict.blocking()[0].code == "FABRICATED_ADMIN_CODE"


def test_t5_gateway_reports_duration():
    gw = SpatialGuardrails()
    verdict = gw.check_tool_args("poi_search", {"location": [116.4, 39.9]})
    assert verdict.duration_ms >= 0.0


def test_t5_gateway_bbox_budget_block():
    gw = SpatialGuardrails()
    verdict = gw.check_tool_args("fetch_data", {"bbox": [-10.0, -10.0, 10.0, 10.0]})
    assert not verdict.passed
    assert verdict.blocking()[0].code == "BBOX_AREA_BUDGET_EXCEEDED"


# ---------------------------------------------------------------------------
# T6 挂载点：ToolDispatchService
# ---------------------------------------------------------------------------
class _FakeRegistry:
    def __init__(self):
        self.calls = []

    async def dispatch(self, tool_name, tool_args_raw, session_id=None):
        self.calls.append((tool_name, tool_args_raw))
        return {"success": True, "echo": tool_args_raw}


def _tc(name, args):
    return {
        "id": "call_" + uuid.uuid4().hex[:8],
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


@pytest.mark.asyncio
async def test_t6_dispatch_blocks_before_registry_execution():
    from app.services.tool_dispatch_service import ToolDispatchService

    reg = _FakeRegistry()
    svc = ToolDispatchService(registry=reg)
    result = await svc.dispatch(
        _tc("poi_search", {"facility": "school", "location": [-150.0, 0.0]}),
        "sess-guard-1",
        set(),
    )
    assert result.status == "error"
    assert result.geojson_ref is None
    assert "OCEAN_POINT_ON_LAND_FACILITY" in (result.error_msg or "")
    assert not reg.calls, "BLOCK 后工具绝不能被真实执行"
    assert "反幻觉" in result.llm_payload or "拦截" in result.llm_payload


@pytest.mark.asyncio
async def test_t6_dispatch_auto_flip_rewrites_arguments():
    from app.services.tool_dispatch_service import ToolDispatchService

    reg = _FakeRegistry()
    svc = ToolDispatchService(registry=reg)
    result = await svc.dispatch(
        _tc("poi_search", {"facility": "school", "location": [39.9042, 116.4074]}),
        "sess-guard-2",
        set(),
    )
    assert result.status == "ok"
    assert len(reg.calls) == 1
    executed_args = reg.calls[0][1]
    if isinstance(executed_args, str):
        executed_args = json.loads(executed_args)
    assert executed_args["location"] == [116.4074, 39.9042]


@pytest.mark.asyncio
async def test_t6_dispatch_kill_switch_passes_through(monkeypatch):
    from app.services.tool_dispatch_service import ToolDispatchService

    monkeypatch.setenv("SPATIAL_GUARDRAILS", "0")
    assert not guardrails_enabled()
    reg = _FakeRegistry()
    svc = ToolDispatchService(registry=reg)
    result = await svc.dispatch(
        _tc("poi_search", {"facility": "school", "location": [-150.0, 0.0]}),
        "sess-guard-3",
        set(),
    )
    assert result.status == "ok"
    assert len(reg.calls) == 1


# ---------------------------------------------------------------------------
# T7 挂载点：lifecycle_engine.apply_mutation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_t7_apply_mutation_blocks_ocean_layer():
    from app.services.mapspec.lifecycle_engine import (
        UpsertLayerIntent,
        mapspec_lifecycle_engine as engine,
    )

    session = "sess-guard-" + uuid.uuid4().hex[:8]
    res = await engine.apply_mutation(
        session,
        UpsertLayerIntent(
            layer={"id": "L1", "source": "s", "type": "circle", "paint": {}},
            source_data=_fc(_point_feature(-150.0, 0.0, {"kind": "school"})),
        ),
    )
    assert res.is_error
    assert "OCEAN_POINT_ON_LAND_FACILITY" in (res.error_msg or "")
    assert res.correction_hint


@pytest.mark.asyncio
async def test_t7_apply_mutation_auto_flips_view_center():
    from app.services.mapspec.lifecycle_engine import (
        SetViewIntent,
        mapspec_lifecycle_engine as engine,
    )
    from app.services.session_data import session_data_manager

    session = "sess-guard-" + uuid.uuid4().hex[:8]
    res = await engine.apply_mutation(session, SetViewIntent(center=[39.9042, 116.4074]))
    assert not res.is_error
    state = await session_data_manager.get_map_state(session)
    assert state["mapspec"]["view"]["center"] == [116.4074, 39.9042]


@pytest.mark.asyncio
async def test_t7_apply_mutation_clean_data_passes():
    from app.services.mapspec.lifecycle_engine import (
        SetViewIntent,
        mapspec_lifecycle_engine as engine,
    )

    session = "sess-guard-" + uuid.uuid4().hex[:8]
    res = await engine.apply_mutation(session, SetViewIntent(center=[116.4074, 39.9042]))
    assert not res.is_error


# ---------------------------------------------------------------------------
# T8 完全离线守护 / fail-open
# ---------------------------------------------------------------------------
@pytest.fixture
def _deny_network(monkeypatch):
    def _deny(*_a, **_k):
        raise AssertionError("守护引擎在检查期间尝试了网络访问（违反离线纪律）")

    monkeypatch.setattr(socket, "create_connection", _deny)
    monkeypatch.setattr(socket.socket, "connect", _deny)


def test_t8_guardrails_fully_offline(_deny_network):
    """socket 全封禁下：倒置检测、掩膜校验、行政区划、GeoJSON 网关全部可用。"""
    report = detect_pair([39.9042, 116.4074])
    assert report.is_inverted and list(report.corrected) == [116.4074, 39.9042]
    with pytest.raises(GeographicImpossibilityError):
        validate_facility_point(-150.0, 0.0, facility_kind="school")
    with pytest.raises(FabricatedAdminDivisionError):
        assert_valid_code("999999")
    gw = SpatialGuardrails()
    verdict = gw.check_geojson(_fc(_point_feature(-140.0, 40.0, {"kind": "poi"})))
    assert not verdict.passed


def test_t8_kill_switch_disables_gateway(monkeypatch):
    monkeypatch.setenv("SPATIAL_GUARDRAILS", "0")
    gw = SpatialGuardrails()
    verdict = gw.check_tool_args("poi_search", {"facility": "school", "location": [-150.0, 0.0]})
    assert verdict.passed and not verdict.issues and not verdict.mutated


def test_t8_fail_open_on_internal_error(monkeypatch):
    """引擎内部故障 → 记警示放行（fail-open），绝不瘫痪调度面。"""
    gw = SpatialGuardrails()

    def _boom(*_a, **_k):
        raise RuntimeError("index corrupted")

    monkeypatch.setattr(
        "app.services.spatial_guardrails.guardrail_middleware.SpatialGuardrails._scan_value",
        _boom,
    )
    verdict = gw.check_tool_args("poi_search", {"location": [116.4, 39.9]})
    assert verdict.passed


# ---------------------------------------------------------------------------
# T9 性能预算
# ---------------------------------------------------------------------------
def test_t9_single_point_check_p95_under_5ms():
    gw = SpatialGuardrails()
    gw.check_tool_args("warmup", {"location": [116.4, 39.9]})  # 惰性资产预热
    args = {"facility": "school", "location": [116.4074, 39.9042]}
    samples = []
    for _ in range(120):
        t0 = time.perf_counter()
        gw.check_tool_args("poi_search", args)
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
    assert p95 < 5.0, f"单点校验 p95={p95:.3f}ms 超出 5ms 预算"


def test_t9_feature_collection_batch_within_budget():
    gw = SpatialGuardrails()
    fc = _fc(*[_point_feature(104.0 + i * 0.01, 30.5 + i * 0.01, {"kind": "poi"}) for i in range(100)])
    t0 = time.perf_counter()
    verdict = gw.check_geojson(fc)
    cost = (time.perf_counter() - t0) * 1000.0
    assert verdict.passed
    assert cost < 100.0, f"100 点 FeatureCollection 校验 {cost:.1f}ms 超出预算"


# ---------------------------------------------------------------------------
# 配置面
# ---------------------------------------------------------------------------
def test_t10_config_from_env_overrides(monkeypatch):
    monkeypatch.setenv("SPATIAL_GUARDRAILS_OCEAN_CONFIRM_KM", "300")
    cfg = GuardrailConfig.from_env()
    assert cfg.ocean_confirm_km == 300.0
    monkeypatch.setenv("SPATIAL_GUARDRAILS_AUTO_FLIP_MIN_CONFIDENCE", "0.9")
    cfg2 = GuardrailConfig.from_env()
    assert cfg2.auto_flip_min_confidence == pytest.approx(0.9)


def test_t10_low_confidence_threshold_disables_soft_flip(monkeypatch):
    monkeypatch.setenv("SPATIAL_GUARDRAILS_AUTO_FLIP_MIN_CONFIDENCE", "0.99")
    gw = SpatialGuardrails(GuardrailConfig.from_env())
    args = {"facility": "school", "location": [43.8256, 87.6168]}  # 软信号倒置
    verdict = gw.check_tool_args("poi_search", args)
    assert verdict.passed
    assert not verdict.mutated, "阈值提高到软信号之上时不应自动翻转"
    assert any(i.code == "SUSPICIOUS_LATLON_ORDER" for i in verdict.issues)
