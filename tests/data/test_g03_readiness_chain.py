"""G03-GEODATA-READINESS —— M1 characterization + 断链失败测试（红灯先行）。

本文件在实现前固定两类事实：
1. **characterization（现在必须绿）**：既有契约边界 —— 五态资格词表稳定、
   CSV 无坐标诚实拒收、CSV+坐标 FC 已走 ingest→descriptor 通道（修正
   审计「断链 7」的误判：CSV+坐标链路完整，纯表格不在 CSV+坐标契约内）。
2. **红灯（M3-M5 实现后转绿）**：六条真实断链的目标行为 ——
   - B1  explorer geocoded rows 桥接后必须有语义身份（descriptor）
   - B2  工具分发输入侧必须有数据资格闸（stale/corrupt 阻断 + 可解释 + 可关）
   - B3  远程 GeoJSON URL 必须有真实 adapter（预算截断 + typed 错误）
   - B5  preflight 必须支持任务范围（bbox/地名）一致性检查
   - B6  城市切换必须使旧资格失效（scope 指纹 + staleness 判定）
   - B8/B9 license/attribution 流入 descriptor、维度级 provenance 三态、
      恶意 GeoJSON（NaN/深结构/超大属性）类型化拒绝
"""
from __future__ import annotations

import pytest

from app.services.data_ingest.pipeline import (
    get_ingest_pipeline,
    reset_ingest_pipeline,
)
from app.services.dataset_semantics import store as sem_store_mod
from app.services.dataset_semantics.store import DatasetSemanticStore
from app.services.gis_harness.data_qualification import QUALIFICATION_STATES


def _fc(n: int = 3, value: str = "v") -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0]},
                "properties": {"v": f"{value}{i}", "name": f"poi-{i}"},
            }
            for i in range(n)
        ],
    }


@pytest.fixture(autouse=True)
def _reset():
    reset_ingest_pipeline()
    yield
    reset_ingest_pipeline()


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(sem_store_mod, "_store", store)
    yield store
    from app.services.dataset_semantics import reset_dataset_semantic_store

    reset_dataset_semantic_store()


async def _ingest(sid: str, fc: dict | None = None, **kw) -> tuple[str, object]:
    result = await get_ingest_pipeline().ingest(
        sid, fc or _fc(), name="g03 测试数据", **kw
    )
    assert result.ok, f"{result.error_code}: {result.error}"
    return result.ref_id


# ═══════════════════ characterization（既有契约，现在必须绿） ════════════════


class TestCharacterization:
    def test_qualification_states_vocabulary_stable(self):
        """五态资格词表是稳定契约（G01）—— 本方向不得增删枚举。"""
        assert QUALIFICATION_STATES == (
            "eligible",
            "transform_required",
            "degraded",
            "blocked",
            "unknown",
        )

    def test_csv_without_coordinates_detected_and_rejected(self):
        """无坐标纯表格无法自动检测经纬度列 —— 诚实拒收，不造几何。"""
        import pandas as pd

        from app.services.data_parser import _detect_csv_columns

        df = pd.DataFrame({"区县": ["锦江区", "青羊区"], "人口": [100, 200]})
        lng, lat = _detect_csv_columns(df)
        assert lng is None and lat is None

    async def test_csv_shaped_fc_ingests_with_descriptor(self, sem_store):
        """CSV+坐标 → FC 形状走 IngestPipeline 已有 descriptor（断链 7 修正）。"""
        ref = await _ingest("g03-char", crs="EPSG:4326")
        rec = await sem_store.get("g03-char", ref)
        assert rec.ok and rec.descriptor is not None


# ═══════════════════ B1 explorer 桥接语义身份（红灯） ══════════════════════


async def _bridge_rows(task_id: str, sid: str, rows: list) -> dict:
    """rows 按 explorer 命名空间落 store 后走真实桥接入口。"""
    from app.services.explorer.validate_stage import bridge_geocoded_to_session
    from app.services.session_data import session_data_manager

    payload = {"rows": rows, "summary": {"total": len(rows)}}
    ref = await session_data_manager.store(f"explorer:{task_id}", payload)
    return await bridge_geocoded_to_session(task_id, sid, ref)


class TestB1ExplorerSemanticBridge:
    async def test_bridged_geocoded_rows_have_descriptor(self, sem_store):
        """geocoded rows 桥进 session 后必须可被语义面读到 descriptor。

        现状（红）：bridge 只存 payload，semantic store 无记录 →
        role_binding 对 explorer 数据永远 not_found（断链 1）。
        """
        rows = [
            {"名称": f"学校{i}", "_lat": 30.6 + i * 0.01, "_lon": 104.06,
             "_geocode_status": "ok", "_geocode_provider": "stub"}
            for i in range(3)
        ] + [
            # geocode 失败的行：无坐标 → 不进 FC，不虚构点位
            {"名称": "失败点", "_geocode_status": "failed"},
        ]
        bridge = await _bridge_rows("g03-b1-task", "g03-b1", rows)
        assert bridge.get("session_ref_id"), "桥接必须产出 session ref"

        semantic_ref = bridge.get("semantic_ref") or bridge["session_ref_id"]
        rec = await sem_store.get("g03-b1", semantic_ref)
        assert rec.ok and rec.descriptor is not None, (
            "explorer 数据必须有语义身份，role_binding 才能消费"
        )
        d = rec.descriptor
        assert d.feature_count == 3, "只有 geocode 成功的行才成为要素"
        assert "Point" in (d.geometry_types or [])

    async def test_all_rows_failed_geocode_bridges_without_fc(self, sem_store):
        """全部行 geocode 失败 → 不铸 FC（诚实零要素），桥接不炸。"""
        bridge = await _bridge_rows("g03-b1z", "g03-b1z-s", [
            {"名称": "x", "_geocode_status": "failed"},
        ])
        assert bridge.get("session_ref_id"), "原始 rows 桥接仍成立"
        assert not bridge.get("semantic_ref"), "零要素不得虚构语义身份"


# ═══════════════════ B2 分发数据资格闸（红灯） ═════════════════════════════


class TestB2DispatchQualificationGate:
    async def test_corrupt_descriptor_blocks(self, sem_store, monkeypatch):
        """descriptor 记录损坏 → 闸门 BLOCK + 可解释原因（不静默放行）。"""
        from app.services.dataset_semantics.dispatch_gate import (
            evaluate_dispatch_data_gate,
        )

        async def _raise(self, session_id, dataset_key):
            raise ValueError("corrupt descriptor json")

        monkeypatch.setattr(DatasetSemanticStore, "get", _raise)
        verdict = await evaluate_dispatch_data_gate(
            "g03-b2", "buffer_analysis", {"data": "ref:geojson-bad"}
        )
        assert verdict.status == "blocked"
        assert "CORRUPT" in verdict.code.upper()
        assert verdict.reason, "阻断必须可解释"

    async def test_stale_record_blocks_with_reason(self, sem_store):
        """记录面期望指纹与 descriptor 指纹不一致（stale）→ BLOCK。"""
        from app.services.dataset_semantics.dispatch_gate import (
            evaluate_dispatch_data_gate,
        )

        sid = "g03-b2s"
        ref = await _ingest(sid)
        verdict = await evaluate_dispatch_data_gate(
            sid, "buffer_analysis", {"data": ref},
            expected_fingerprints={ref: "f" * 32},
        )
        assert verdict.status == "blocked"
        assert "STALE" in verdict.code.upper()

    async def test_fresh_record_passes(self, sem_store):
        """descriptor 新鲜一致 → 放行并附数据资格上下文（模型可见事实）。"""
        from app.services.dataset_semantics.dispatch_gate import (
            evaluate_dispatch_data_gate,
        )

        sid = "g03-b2f"
        ref = await _ingest(sid)
        verdict = await evaluate_dispatch_data_gate(sid, "buffer_analysis", {"data": ref})
        assert verdict.status == "pass"
        ctx = verdict.qualification_context.get(ref)
        assert ctx and ctx.get("crs"), "放行也必须携带可见的数据事实"

    async def test_kill_switch_disables_gate(self, sem_store, monkeypatch):
        """DATA_QUALIFICATION_GATE=0 → 一键回滚旧行为（全放行）。"""
        from app.services.dataset_semantics.dispatch_gate import (
            evaluate_dispatch_data_gate,
        )

        monkeypatch.setenv("DATA_QUALIFICATION_GATE", "0")
        verdict = await evaluate_dispatch_data_gate(
            "g03-b2k", "buffer_analysis", {"data": "ref:geojson-whatever"}
        )
        assert verdict.status == "pass"

    async def test_exempt_namespaces_skip_gate(self, sem_store):
        """raster/profile/sentinel ref 不走资格闸（能力投影，非数据集）。"""
        from app.services.dataset_semantics.dispatch_gate import (
            evaluate_dispatch_data_gate,
        )

        verdict = await evaluate_dispatch_data_gate(
            "g03-b2e", "heatmap",
            {"image": "ref:raster/abc", "profile": "ref:profile-x"},
        )
        assert verdict.status == "pass"
        assert verdict.qualification_context == {}

    async def test_missing_record_passes_with_disclosure(self, sem_store, monkeypatch):
        """无语义记录 → 默认放行但披露 descriptor_missing；strict 模式阻断。"""
        from app.services.dataset_semantics.dispatch_gate import (
            evaluate_dispatch_data_gate,
        )

        verdict = await evaluate_dispatch_data_gate(
            "g03-b2m", "buffer_analysis", {"data": "ref:geojson-none"}
        )
        assert verdict.status == "pass"
        assert verdict.disclosure.get("ref:geojson-none") == "descriptor_missing"

        monkeypatch.setenv("DATA_QUALIFICATION_GATE_STRICT", "1")
        strict = await evaluate_dispatch_data_gate(
            "g03-b2m", "buffer_analysis", {"data": "ref:geojson-none"}
        )
        assert strict.status == "blocked"
        assert "MISSING" in strict.code.upper()


# ═══════════════════ B3 远程 GeoJSON URL adapter（红灯） ═══════════════════


class TestB3GeojsonUrlAdapter:
    def test_registry_resolves_geojson_url(self):
        from app.services.data_fabric.registry import resolve_adapter_spec

        spec = resolve_adapter_spec("geojson_url")
        assert spec.source_type == "geojson_url"
        assert not getattr(spec, "is_demo", False), "真实 adapter，非 demo"

    def test_query_respects_feature_cap(self, monkeypatch):
        """远程 FC 超要素帽 → 截断并披露 truncated，不无界拉取。"""
        from tests.data.fabric_fixtures import FakeSourceServer, patched_safe_sessions

        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.geojson_url_adapter import (
            GeojsonUrlAdapter,
        )

        big_fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                 "properties": {"i": i}}
                for i in range(50)
            ],
        }
        server = FakeSourceServer()

        def _handler(request):
            from tests.fixtures.data_fabric.fake_server import make_response

            return make_response(
                request.url, json_body=big_fc,
                headers={"Content-Type": "application/geo+json"},
            )

        server._add("/data.geojson", _handler)
        patched_safe_sessions(monkeypatch)(server)

        adapter = GeojsonUrlAdapter(ConnectionProfile(
            id="gj_url", source_type="geojson_url", url=server.base_url + "/data.geojson",
        ))
        assert adapter.probe() is True
        preview = adapter.preview("default", limit=10)
        assert len(preview.get("features", [])) == 10, "要素帽必须生效"
        assert preview.get("truncated") is True

    def test_non_fc_content_typed_error(self, monkeypatch):
        """假 Content-Type / 非 FC 体 → 类型化错误，不静默空结果。"""
        from tests.data.fabric_fixtures import FakeSourceServer, patched_safe_sessions

        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.geojson_url_adapter import (
            GeojsonUrlAdapter,
        )
        from app.services.data_fabric.errors import SourceBadResponseError

        server = FakeSourceServer()

        def _handler(request):
            from tests.fixtures.data_fabric.fake_server import make_response

            return make_response(
                request.url,
                json_body={"type": "Feature", "geometry": None},
                headers={"Content-Type": "application/geo+json"},
            )

        server._add("/notfc.geojson", _handler)
        patched_safe_sessions(monkeypatch)(server)

        adapter = GeojsonUrlAdapter(ConnectionProfile(
            id="gj_bad", source_type="geojson_url",
            url=server.base_url + "/notfc.geojson",
        ))
        with pytest.raises(SourceBadResponseError):
            adapter.probe()


# ═══════════════════ B5 preflight 任务范围（红灯） ═════════════════════════


class TestB5PreflightTaskScope:
    async def test_dataset_bbox_disjoint_from_task_scope_is_gap(self, sem_store):
        """数据 bbox 与任务范围（成都）不相交 → typed gap（断链 5）。"""
        from app.services.dataset_semantics.preflight import (
            CODE_SCOPE_MISMATCH,
            preflight_multi_dataset,
        )

        ref = await _ingest("g03-b5", _fc(), crs="EPSG:4326")  # 北京坐标
        rec = await sem_store.get("g03-b5", ref)
        report = preflight_multi_dataset(
            {"poi": rec.descriptor},
            task_scope={"place": "成都", "bbox": [103.9, 30.5, 104.2, 30.8]},
        )
        codes = [g.code for g in report.gaps]
        assert CODE_SCOPE_MISMATCH in codes

    async def test_dataset_bbox_inside_task_scope_no_scope_gap(self, sem_store):
        from app.services.dataset_semantics.preflight import (
            CODE_SCOPE_MISMATCH,
            preflight_multi_dataset,
        )

        ref = await _ingest("g03-b5b", _fc(), crs="EPSG:4326")
        rec = await sem_store.get("g03-b5b", ref)
        report = preflight_multi_dataset(
            {"poi": rec.descriptor},
            task_scope={"place": "北京", "bbox": [115.9, 38.9, 116.2, 39.1]},
        )
        codes = [g.code for g in report.gaps]
        assert CODE_SCOPE_MISMATCH not in codes

    async def test_scope_check_only_with_declared_scope(self, sem_store):
        """未声明任务范围 → 不产生 scope gap（向后兼容：旧调用不受影响）。"""
        from app.services.dataset_semantics.preflight import (
            CODE_SCOPE_MISMATCH,
            preflight_multi_dataset,
        )

        ref = await _ingest("g03-b5c", _fc(), crs="EPSG:4326")
        rec = await sem_store.get("g03-b5c", ref)
        report = preflight_multi_dataset({"poi": rec.descriptor})
        assert CODE_SCOPE_MISMATCH not in [g.code for g in report.gaps]


# ═══════════════════ B6 城市切换失效（红灯） ═══════════════════════════════


class TestB6CityScopeInvalidation:
    def test_scope_fingerprint_changes_with_city(self):
        from app.services.dataset_semantics.task_scope import scope_fingerprint

        chengdu = scope_fingerprint(place="成都", bbox=[103.9, 30.5, 104.2, 30.8])
        beijing = scope_fingerprint(place="北京", bbox=[115.9, 39.0, 116.5, 40.0])
        assert chengdu and beijing and chengdu != beijing
        assert scope_fingerprint(place="成都", bbox=[103.9, 30.5, 104.2, 30.8]) == chengdu

    def test_scope_change_requires_requalification(self):
        """城市切换后，旧 scope 下绑定的资格必须被判需重判定。"""
        from app.services.dataset_semantics.task_scope import (
            evaluate_scope_staleness,
            scope_fingerprint,
        )

        recorded = scope_fingerprint(place="成都", bbox=[103.9, 30.5, 104.2, 30.8])
        current = scope_fingerprint(place="北京", bbox=[115.9, 39.0, 116.5, 40.0])
        st = evaluate_scope_staleness(recorded, current)
        assert st.stale is True
        assert st.reason, "必须给出可解释原因"

        same = evaluate_scope_staleness(recorded, recorded)
        assert same.stale is False

    def test_place_only_scope_bboxes_differ_place_wins(self):
        """同名城市不同精确 bbox：地名相同视为同 scope（地名是用户事实）。"""
        from app.services.dataset_semantics.task_scope import scope_fingerprint

        a = scope_fingerprint(place="成都")
        b = scope_fingerprint(place="成都", bbox=[103.9, 30.5, 104.2, 30.8])
        assert a != b, "有 bbox 与无 bbox 是不同的资格粒度，不得混用"


# ═══════════════════ B8 license 流入 + provenance 三态（红灯） ═════════════


class TestB8LicenseProvenance:
    async def test_ingest_license_flows_to_descriptor(self, sem_store):
        result = await get_ingest_pipeline().ingest(
            "g03-b8", _fc(), name="带许可数据", crs="EPSG:4326",
            license="CC-BY-4.0", attribution="某市数据开放平台",
        )
        assert result.ok
        rec = await sem_store.get("g03-b8", result.ref_id)
        assert rec.descriptor.license == "CC-BY-4.0"
        assert rec.descriptor.attribution == "某市数据开放平台"
        assert rec.descriptor.dimension_provenance.get("license") == "observed"

    async def test_dimension_provenance_tri_state(self, sem_store):
        """核心维度均有 observed/inferred/unknown 标注；未知不虚构。"""
        ref = await _ingest("g03-b8b", _fc())  # 未声明 CRS
        rec = await sem_store.get("g03-b8b", ref)
        prov = rec.descriptor.dimension_provenance
        for dim in ("geometry_type", "feature_count", "bbox", "crs", "license"):
            assert prov.get(dim) in ("observed", "inferred", "unknown"), dim
        assert prov.get("crs") == "unknown", "未声明 CRS 必须是 unknown 而非虚构"
        assert prov.get("geometry_type") == "observed"


# ═══════════════════ B9 恶意 GeoJSON 守卫（红灯） ══════════════════════════


class TestB9MaliciousGeojsonGuard:
    async def test_nan_coordinate_rejected_typed(self, sem_store):
        fc = _fc(2)
        fc["features"][0]["geometry"]["coordinates"] = [float("nan"), 39.0]
        result = await get_ingest_pipeline().ingest("g03-b9a", fc)
        assert not result.ok
        assert "NONFINITE" in (result.error_code or "").upper()

    async def test_deep_nesting_rejected_typed(self, sem_store):
        fc = _fc(1)
        node = fc["features"][0]["properties"]
        for _ in range(120):
            node["child"] = {}
            node = node["child"]
        result = await get_ingest_pipeline().ingest("g03-b9b", fc)
        assert not result.ok
        assert "DEPTH" in (result.error_code or "").upper()

    async def test_oversized_property_string_rejected(self, sem_store):
        fc = _fc(1)
        fc["features"][0]["properties"]["blob"] = "A" * (2 * 1024 * 1024)
        result = await get_ingest_pipeline().ingest("g03-b9c", fc)
        assert not result.ok
        assert "OVERSIZED" in (result.error_code or "").upper()

    async def test_guard_bounded_on_large_valid_fc(self, sem_store):
        """合法大 FC（5 万要素）必须通过守卫且有界耗时。"""
        import time

        features = [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [116.0 + (i % 100) * 0.001, 39.0]},
             "properties": {"i": i}}
            for i in range(50_000)
        ]
        t0 = time.monotonic()
        result = await get_ingest_pipeline().ingest(
            "g03-b9d", {"type": "FeatureCollection", "features": features},
            name="大点表", crs="EPSG:4326",
        )
        elapsed = time.monotonic() - t0
        assert result.ok, result.error
        assert elapsed < 60, f"5 万要素守卫+摄入应远快于 60s（实测 {elapsed:.1f}s）"
