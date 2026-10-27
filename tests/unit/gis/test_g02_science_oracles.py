"""G02 科学守门 oracle —— 触发真实执行门的正例/负例语料（W3/W4）。

与 tests/science_oracles/（数值 golden，冻结 JSON）正交：本文件是**守门链
行为 oracle** —— 每组用真实 resolver / 执行入口 / final gate / dispatch
驱动，断言「未知 ≠ 科学通过」「硬风险 fail-closed」「不等价替换必披露」。

oracle 组（≥8，编号即 PLAN.md W4 清单）：
 O1 numeric_field_required（负例：字段已知无数值 → 拒）
 O2 nonzero_variance（负例：常量场 → 拒）
 O3 min_numeric_samples 带状（64=proven / 5=警告 / 3=拒）
 O4 raster_band_required:2（负例：波段不足 → 拒）
 O5 temporal_field_required（负例：无时间字段 → 拒）
 O6 CRS 门 + transform 提议贯通 plan 转录（projected 算法 × 地理 CRS）
 O7 fallback 语义披露贯通（resolver→执行→final→产品编译）
 O8 dispatch typed 穿透（InvalidCRS 负例）
 O9 TOCTOU：plan 合格 → 执行画像劣化 → 引擎级 fail-closed（真 DB 引擎环）
 O10 测地面积披露（aggregation 权威库对照 + area_crs 契约）
 O11 并发/幂等（resolver 纯函数双线程一致；gate 双跑一致）
 O12 性能基线（241 算法 resolve+契约 < 2s；投影零 IO 断言）
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.algorithm_resolver import (
    get_algorithm_resolver,
    reset_algorithm_resolver,
)
from app.lib.gis.capability_registry import get_capability_registry
from app.lib.gis.science_final_gate import (
    run_science_final_gate,
    science_profile_digest,
)
from app.schemas.project_schema import WorkflowStepSpec
from app.services.workflow_engine import WorkflowEngine


@pytest.fixture(autouse=True)
def _fresh_resolver():
    reset_algorithm_resolver()
    yield
    reset_algorithm_resolver()


def _cap_of(algorithm_id: str) -> str:
    reg = get_algorithm_registry()
    for cid in get_capability_registry().all_ids:
        if any(a.id == algorithm_id for a in reg.algorithms_for_capability(cid)):
            return cid
    raise AssertionError(algorithm_id)


GWR = "gwr"  # spatial.gwr：无 fallback
GWR_TOOLS = {"gwr_regression"}
BAND = "band_statistics"  # remote.band_correlation
BAND_TOOLS = {"band_correlation_table"}
TEMPORAL = "temporal_trend"  # temporal.trend
TEMPORAL_TOOLS = {"temporal_trend"}


def _resolve(cap, profile, tools):
    return get_algorithm_resolver().resolve(cap, profile=profile, available_tools=tools)


# ── O1 数值字段（负例：权威口径的空清单）───────────────────────────────
class TestO1NumericFieldRequired:
    def test_known_fields_without_numeric_rejected(self):
        res = _resolve(
            GWR,
            {
                "fields": {"name": {}, "code": {}},
                "numericFields": [],  # 在场且为空 = 权威缺席
                "geometryTypes": ["Polygon"],
                "crs": "EPSG:32648",
            },
            GWR_TOOLS,
        )
        assert res.status == "unavailable"
        assert any(
            "scientific_precondition:spatial.gwr:numeric_field_required" in r
            for r in res.rejected
        )

    def test_unknown_fields_deferred_disclosed(self):
        res = _resolve(
            GWR, {"geometryTypes": ["Polygon"], "crs": "EPSG:32648"}, GWR_TOOLS
        )
        # 事实缺席 → 资格保留 + not_evaluated 披露（未知 ≠ 科学通过）
        assert res.status == "resolved"
        assert any(e.startswith("numeric_field_required:") for e in res.not_evaluated)


# ── O2 非零方差（负例：常量场）─────────────────────────────────────────
class TestO2NonzeroVariance:
    def test_zero_variance_rejected(self):
        res = _resolve(
            GWR,
            {
                "fields": {"pop": {}},
                "numericFields": ["pop"],
                "valueVariance": 0.0,
                "geometryTypes": ["Polygon"],
                "crs": "EPSG:32648",
            },
            GWR_TOOLS,
        )
        assert res.status == "unavailable"
        assert any("nonzero_variance_required" in r for r in res.rejected)

    def test_positive_variance_proven_pass(self):
        res = _resolve(
            GWR,
            {
                "fields": {"pop": {}},
                "numericFields": ["pop"],
                "valueVariance": 12.5,
                "numericSampleCount": 64,
                "geometryTypes": ["Polygon"],
                "crs": "EPSG:32648",
            },
            GWR_TOOLS,
        )
        assert res.status == "resolved"
        assert res.not_evaluated == []


# ── O3 样本量带状（min_numeric_samples:8）──────────────────────────────
class TestO3MinSamplesBands:
    BASE = {
        "fields": {"pop": {}},
        "numericFields": ["pop"],
        "valueVariance": 4.0,
        "geometryTypes": ["Polygon"],
        "crs": "EPSG:32648",
    }

    def test_sufficient_samples_proven(self):
        res = _resolve(GWR, {**self.BASE, "numericSampleCount": 64}, GWR_TOOLS)
        assert res.status == "resolved"
        assert not any(
            "min_numeric_samples" in e
            for e in res.not_evaluated + res.scientific_warnings
        )

    def test_warning_band(self):
        res = _resolve(GWR, {**self.BASE, "numericSampleCount": 5}, GWR_TOOLS)
        assert res.status == "resolved"
        assert any("min_numeric_samples:8" in w for w in res.scientific_warnings)

    def test_below_floor_rejected(self):
        res = _resolve(GWR, {**self.BASE, "numericSampleCount": 3}, GWR_TOOLS)
        assert res.status == "unavailable"
        assert any("min_numeric_samples:8:INSUFFICIENT_DATA" in r for r in res.rejected)


# ── O4 栅格波段（负例）────────────────────────────────────────────────
class TestO4RasterBand:
    def test_band_shortfall_rejected(self):
        res = _resolve(BAND, {"bandCount": 1, "crs": "EPSG:32650"}, BAND_TOOLS)
        assert res.status == "unavailable"
        assert any("raster_band_required:2" in r for r in res.rejected)

    def test_band_count_unknown_deferred(self):
        res = _resolve(BAND, {"crs": "EPSG:32650"}, BAND_TOOLS)
        assert res.status == "resolved"
        assert any(e.startswith("raster_band_required:2:") for e in res.not_evaluated)


# ── O5 时间字段（负例）────────────────────────────────────────────────
class TestO5TemporalField:
    def test_missing_time_field_rejected(self):
        res = _resolve(TEMPORAL, {"hasTimeField": False}, TEMPORAL_TOOLS)
        assert res.status == "unavailable"
        assert any("temporal_field_required" in r for r in res.rejected)

    def test_observation_count_band(self):
        # temporal.trend 声明 min_temporal_observations:8 —— 观测数 3
        # < max(2, 8//2)=4 → INSUFFICIENT_DATA（趋势声明不可得）。
        res = _resolve(
            TEMPORAL,
            {
                "hasTimeField": True,
                "temporalObservationCount": 3,
            },
            TEMPORAL_TOOLS,
        )
        assert res.status == "unavailable"
        assert any("min_temporal_observations:8" in r for r in res.rejected)

    def test_observation_warning_band(self):
        # 观测数 4（≥4 且 <8）→ PASS_WITH_WARNINGS（只能描述性解读）
        res = _resolve(
            TEMPORAL,
            {
                "hasTimeField": True,
                "temporalObservationCount": 4,
            },
            TEMPORAL_TOOLS,
        )
        assert res.status == "resolved"
        assert any("min_temporal_observations:8" in w for w in res.scientific_warnings)


# ── O6 CRS 门 + transform 提议贯通 plan 转录 ──────────────────────────
class TestO6CrsTransformChain:
    def test_geographic_crs_transform_flows_to_plan_record(self):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner import MapProductPlanner

        cap = GWR
        planner = MapProductPlanner()
        # 地理 CRS 违反 PROJECTED_REQUIRED → 拒 + transform 提议
        _, _, selections = planner._resolve_capabilities(
            [cap],
            resolve_map_request_intent("成都回归分析"),
            available_tools=GWR_TOOLS,
            profile={
                "crs": "EPSG:4326",
                "geometryTypes": ["Polygon"],
                "bbox": [104.0, 30.6, 104.1, 30.7],
                "fields": {"pop": {}},
                "numericFields": ["pop"],
                "valueVariance": 5.0,
                "numericSampleCount": 64,
            },
        )
        rec = next(s for s in selections if s.capability == cap)
        assert rec.status == "unavailable"
        # 转录断链修复：required_transformations 不再恒空
        assert rec.required_transformations, "transform 提议必须转录"
        assert any("reproject" in t.lower() for t in rec.required_transformations)

    def test_resolver_level_transform_hint(self):
        res = _resolve(
            GWR,
            {
                "crs": "EPSG:4326",
                "geometryTypes": ["Polygon"],
                "bbox": [104.0, 30.6, 104.1, 30.7],
            },
            GWR_TOOLS,
        )
        assert res.status == "unavailable"
        assert any("crs_class_mismatch:spatial.gwr" in r for r in res.rejected)
        # 拒绝码内嵌 ;transform=reproject to <UTM>，被 resolve 汇成清单
        assert any("reproject" in t.lower() for t in res.required_transformations)


# ── O7 fallback 语义披露贯通三段 ──────────────────────────────────────
class TestO7FallbackSemantics:
    def test_substitution_disclosed_resolver_to_product(self):
        # regression_kriging（样本不足）→ kriging 替补（approximation）
        res = get_algorithm_resolver().resolve(
            "regression_kriging",
            profile={"numericSampleCount": 3, "crs": "EPSG:32648"},
            available_tools={"regression_kriging", "kriging_interpolation"},
        )
        assert res.status == "resolved"
        trail_semantics = [f.semantics for f in res.fallback_trail]
        assert any(s and s != "equivalent" for s in trail_semantics)

        # 执行段 evidence 携带同一语义
        spec = WorkflowStepSpec(
            step_id="s1",
            tool_name="regression_kriging",
            capability="regression_kriging",
            algorithm_preference="interpolation.regression_kriging",
        )
        _, _, _, ev = WorkflowEngine.resolve_step_tool(
            spec,
            _Reg({"regression_kriging", "kriging_interpolation"}),
            available={"regression_kriging", "kriging_interpolation"},
            profile={"numericSampleCount": 3, "crs": "EPSG:32648"},
        )
        assert any(t["semantics"] != "equivalent" for t in ev.get("fallback_trail", []))

        # final 段：非等价替换 → finding + 产品披露面
        from app.services.gis_harness.planner import AlgorithmSelectionRecord
        from app.services.gis_harness.planner import MapProductPlan
        from app.services.gis_harness.intent import resolve_map_request_intent

        rec = AlgorithmSelectionRecord(
            capability="regression_kriging",
            status=res.status,
            algorithm=res.algorithm,
            tool=res.tool,
            fallback_trail=[f.model_dump() for f in res.fallback_trail],
        )
        plan = MapProductPlan(
            plan_id="p",
            query="q",
            intent=resolve_map_request_intent("克里金插值"),
            recipe_id="spatial_analysis",
            algorithm_selections=[rec],
        )
        gate = run_science_final_gate(plan)
        assert any(f.code == "G-FALLBACK-SEMANTICS" for f in gate.findings)

        # 产品编译：披露进入 fallbacks 面（真实 compile）
        from app.services.gis_harness.product_compiler import compile_product_spec
        from app.services.gis_harness.product_shapes import (
            build_product_spec_from_plan,
        )
        from app.services.gis_harness.product_templates import (
            get_product_template_registry,
        )

        intent = resolve_map_request_intent("成都人口分布图")
        template = get_product_template_registry().find_for_recipe(
            "poi_distribution_overview", intent.subject.category or ""
        )
        plan2 = MapProductPlan(
            plan_id="p2",
            query="q",
            intent=intent,
            recipe_id="poi_distribution_overview",
            template_id=template.id if template else "",
            algorithm_selections=[rec],
        )
        spec2 = build_product_spec_from_plan(plan2, intent, template)
        compiled = compile_product_spec(spec2, plan=plan2, template=template)
        codes = [f["code"] for f in compiled.science_findings]
        assert "G-FALLBACK-SEMANTICS" in codes
        assert any(
            fb.get("code") == "science_fallback_substitution"
            for fb in compiled.fallbacks
        )


# ── O8 dispatch typed 穿透（InvalidCRS 负例）──────────────────────────
class TestO8DispatchTypedErrors:
    def test_invalid_crs_preserved(self):
        from app.lib.gis.scientific_errors import InvalidCRS
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()

        def probe(crs: str = "EPSG:4326") -> dict:
            raise InvalidCRS(
                "距离类算法需要投影 CRS，得到地理 CRS",
                ref={"precondition": "projected_crs_required"},
            )

        reg.register("g02_o8_probe", "probe", probe)
        resp = asyncio.run(reg.dispatch("g02_o8_probe", {"crs": "EPSG:4326"}))
        assert resp["success"] is False
        assert resp["code"] == "INVALID_CRS"
        assert resp["scientific_ref"]["precondition"] == "projected_crs_required"
        assert resp["error_type"] == "InvalidCRS"


# ── O9 TOCTOU：引擎级 fail-closed（真 resolver + 真 DB 引擎环）────────
class TestO9ToctouEngineLevel:
    def test_plan_eligible_execute_degraded_blocks_run(self, monkeypatch):
        """plan 期 GWR 合格 → 执行期画像劣化（无数值字段）→ 引擎拒发。

        走真实 execute_workflow_run 循环 + 真实 resolver（GWR 无 fallback，
        科学拒绝不可替补）；_resolver_profile_for_args 是画像注入口（诚实
        降级 seam，其生产实现从 session ref 取 descriptor）。
        """
        from app.services.workflow_engine import WorkflowEngine as WE
        from tests.unit.test_reproducible_gis_runtime import (
            _mk_project_and_workflow,
        )

        degraded = {
            "fields": {"name": {}},
            "numericFields": [],
            "geometryTypes": ["Polygon"],
            "crs": "EPSG:32648",
        }

        async def _fake_profile(session_id, tool_args):
            return degraded

        monkeypatch.setattr(
            WE, "_resolver_profile_for_args", staticmethod(_fake_profile)
        )

        steps = [
            WorkflowStepSpec(
                step_id="gwr_step",
                tool_name="gwr_regression",
                capability="gwr",
                algorithm_preference="spatial.gwr",
                args_template={"formula": "y~x"},
            )
        ]
        pid, wid = f"proj_{uuid4hex()}", f"wf_{uuid4hex()}"
        _mk_project_and_workflow(pid, wid, steps)

        class _NoDispatch:
            calls = []

            def list_tools(self):
                return list(GWR_TOOLS)

            def tool_version(self, name):
                return "1.0"

            async def dispatch(self, name, args, session_id=None):
                self.calls.append(name)
                return {"success": True}

        reg = _NoDispatch()
        run = asyncio.run(
            WE.execute_workflow_run(
                db=next(_session()),
                workflow_id=wid,
                tool_registry=reg,
                expected_project_id=pid,
            )
        )
        assert run.status == "failed"
        assert reg.calls == [], "科学硬拒后绝不派发工具"
        assert (
            "science" in (run.error_message or "").lower()
            or "precondition" in (run.error_message or "").lower()
        )
        # trace 保留结构化证据（error_code 机读）
        trace = run.execution_trace or []
        blocked_entries = [
            t for t in trace if t.get("error_code") == "SCIENCE_PRECONDITION_FAILED"
        ]
        assert blocked_entries, trace
        ev = blocked_entries[0].get("resolution_evidence", {})
        assert ev.get("science_hard_block", "").startswith("scientific_precondition:")

    def test_data_growth_between_stages_disclosed_not_blocked(self):
        """非破坏性 TOCTOU（数据规模变化）→ final gate freshness 披露。"""
        from types import SimpleNamespace

        plan_digest = science_profile_digest(
            {"numericSampleCount": 64, "featureCount": 64, "crs": "EPSG:32648"}
        )
        exec_digest = science_profile_digest(
            {"numericSampleCount": 128, "featureCount": 128, "crs": "EPSG:32648"}
        )
        rec = SimpleNamespace(
            capability="gwr",
            status="resolved",
            algorithm="spatial.gwr",
            fallback_trail=[],
            not_evaluated=[],
            profile_digest=plan_digest,
        )
        gate = run_science_final_gate(
            SimpleNamespace(algorithm_selections=[rec]),
            execution_evidence={
                "s1": {
                    "capability": "gwr",
                    "resolver_status": "resolved",
                    "profile_digest": exec_digest,
                }
            },
        )
        codes = [f.code for f in gate.findings]
        assert "G-FRESHNESS" in codes
        assert gate.blocked is False


# ── O10 测地面积披露（权威库对照）─────────────────────────────────────
class TestO10GeodesicAreaDisclosure:
    def test_wide_extent_uses_geodesic_and_discloses(self):
        geopandas = pytest.importorskip("geopandas")
        from shapely.geometry import Polygon

        from app.lib.geo_analysis.aggregation import _metric_zone_area_m2

        # 10°×10°（跨 >6° 单 UTM 带）→ 必须走测地 + area_crs 披露
        poly = Polygon([(100, 0), (110, 0), (110, 10), (100, 10)])
        gdf = geopandas.GeoDataFrame(geometry=[poly], crs="EPSG:4326")
        areas, crs_used, _data_class = _metric_zone_area_m2(gdf)
        assert crs_used == "geodesic:WGS84"
        # 权威库对照（pyproj Geod 同椭球同几何 —— 一致性 oracle）
        from pyproj import Geod

        geod = Geod(ellps="WGS84")
        expected = abs(geod.geometry_area_perimeter(poly)[0])
        assert areas.iloc[0] == pytest.approx(expected, rel=1e-9)
        # 量级健全性（10° 赤道带 ≈ 1.2e12 m²，非平面度混乱值）
        assert 1.0e12 < areas.iloc[0] < 1.5e12


# ── O11 并发/幂等 ─────────────────────────────────────────────────────
class TestO11ConcurrencyIdempotence:
    PROFILES = [
        {"crs": "EPSG:4326", "geometryTypes": ["Polygon"]},
        {
            "crs": "EPSG:32648",
            "geometryTypes": ["Polygon"],
            "numericFields": ["v"],
            "numericSampleCount": 64,
            "valueVariance": 3.0,
        },
    ]

    def test_resolver_thread_deterministic(self):
        def work(profile):
            return (
                get_algorithm_resolver()
                .resolve(GWR, profile=profile, available_tools=GWR_TOOLS)
                .model_dump()
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(work, p) for p in self.PROFILES * 3]
            results = [f.result() for f in futures]
        by_profile = {}
        for profile, dump in zip(self.PROFILES * 3, results):
            by_profile.setdefault(id(profile), set()).add(str(sorted(dump.items())))
        for variants in by_profile.values():
            assert len(variants) == 1, "同画像并发 resolve 必须逐位一致"

    def test_gate_idempotent(self):
        from types import SimpleNamespace

        plan = SimpleNamespace(
            algorithm_selections=[
                SimpleNamespace(
                    capability="gwr",
                    status="resolved",
                    algorithm="spatial.gwr",
                    fallback_trail=[{"from": "a", "to": "b", "semantics": "proxy"}],
                    not_evaluated=["numeric_field_required: x"],
                    profile_digest="d1",
                )
            ]
        )
        r1 = run_science_final_gate(plan).model_dump()
        r2 = run_science_final_gate(plan).model_dump()
        assert r1 == r2


# ── O12 性能基线 ──────────────────────────────────────────────────────
class TestO12PerformanceBudget:
    def test_full_registry_resolve_and_contract_under_budget(self, monkeypatch):
        import builtins

        from app.lib.gis.method_use_contract import iter_method_use_contracts

        caps = get_capability_registry().all_ids
        resolver = get_algorithm_resolver()
        profile = {
            "crs": "EPSG:4326",
            "geometryTypes": ["Polygon"],
            "featureCount": 100,
        }

        # 零 IO 断言：契约投影与 resolve 循环内不得打开任何文件
        real_open = builtins.open

        def _no_open(*a, **kw):
            raise AssertionError("registry/contract 投影路径发生文件 IO")

        monkeypatch.setattr(builtins, "open", _no_open)
        try:
            t0 = time.perf_counter()
            resolves = sum(
                1
                for cid in caps
                if resolver.resolve(cid, profile=profile).status == "resolved"
            )
            contracts = sum(1 for _ in iter_method_use_contracts())
            elapsed = time.perf_counter() - t0
        finally:
            monkeypatch.setattr(builtins, "open", real_open)
        # G3D03（ADR-0274）：terrain.measurement 族 +4 算法 → 241
        assert contracts == get_algorithm_registry().count == 241
        assert resolves > 0
        assert elapsed < 2.0, f"241 算法 resolve+契约投影 {elapsed:.2f}s 超预算"


# ── helpers ───────────────────────────────────────────────────────────
class _Reg:
    def __init__(self, tools):
        self._tools = list(tools)

    def list_tools(self):
        return list(self._tools)


def uuid4hex() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


def _session():
    from app.core.database import SessionLocal

    yield SessionLocal()
