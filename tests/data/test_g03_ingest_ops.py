"""G03 M5 —— GD-06 协作取消/幂等重试、GD-10 计时观测、零记录诚实、
GD-04 字段映射 fail-closed 核验。

全部零模拟：本地生成的 FC 走真实 IngestPipeline / preflight /
qualification，断言以真实产物为准。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.data_ingest.pipeline import (
    get_ingest_pipeline,
    reset_ingest_pipeline,
)
from app.services.dataset_semantics import store as sem_store_mod
from app.services.dataset_semantics.store import DatasetSemanticStore


def _fc(n: int = 3) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0]},
             "properties": {"v": i, "name": f"poi-{i}"}}
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


# ═══════════════════ GD-06 协作取消 + 幂等重试 ═══════════════════════════


class TestGd06Cancellation:
    async def test_cancel_before_store_leaves_no_ref(self, sem_store):
        """profile 之后、store 之前取消：typed 取消结果，无 ref 铸造。"""
        cancel = asyncio.Event()
        cancel.set()  # 一进 pipeline 即取消（第一个检查点在 detect 后）
        result = await get_ingest_pipeline().ingest(
            "g03-m5-cancel", _fc(), crs="EPSG:4326", cancel_event=cancel)
        assert not result.ok
        assert result.error_code == "INGEST_CANCELLED"
        assert result.ref_id == ""

    async def test_cancel_after_store_rolls_back_orphan(self, sem_store):
        """store 之后取消：孤儿 ref 被回滚（无半截产物）。"""
        from app.services.session_data import session_data_manager

        sid = "g03-m5-cancel2"
        state = {"cancelled": False}
        original_store = session_data_manager.store

        async def _store_then_arm(session_id, payload, prefix="ingest"):
            ref = await original_store(session_id, payload, prefix=prefix)
            state["cancelled"] = True  # store 完成 → 取消在下一检查点生效
            return ref

        import app.services.data_ingest.pipeline as pipe_mod

        # pipeline 在方法内 import session_data_manager → patch 模块属性
        from app.services import session_data as sd_mod

        orig_store = sd_mod.session_data_manager.store
        sd_mod.session_data_manager.store = _store_then_arm
        try:
            cancel = lambda: state["cancelled"]  # noqa: E731
            result = await get_ingest_pipeline().ingest(
                sid, _fc(), crs="EPSG:4326", cancel_event=cancel)
        finally:
            sd_mod.session_data_manager.store = orig_store
        assert result.error_code == "INGEST_CANCELLED"
        assert result.ref_id == ""
        assert "rollback-on-cancel" in result.steps_completed

    async def test_retry_after_cancel_is_idempotent_via_dedup(self, sem_store):
        """取消后重试同载荷：要么全新成功要么 dedup 复用——绝不双份。"""
        from app.services.artifact_registry import list_artifacts

        sid = "g03-m5-retry"
        cancel = asyncio.Event()
        cancel.set()
        first = await get_ingest_pipeline().ingest(
            sid, _fc(), crs="EPSG:4326", cancel_event=cancel)
        assert first.error_code == "INGEST_CANCELLED"

        second = await get_ingest_pipeline().ingest(sid, _fc(), crs="EPSG:4326")
        assert second.ok
        third = await get_ingest_pipeline().ingest(sid, _fc(), crs="EPSG:4326")
        assert third.ok and third.duplicate and third.ref_id == second.ref_id
        artifacts = await list_artifacts(sid)
        assert len(artifacts) == 1, "取消-重试不得产生重复逻辑对象"


# ═══════════════════ GD-10 计时观测 ═══════════════════════════════════════


class TestGd10Timings:
    async def test_timings_recorded_and_honest(self, sem_store):
        """步级耗时与端到端墙钟如实记录（有值、非负、覆盖关键步）。"""
        result = await get_ingest_pipeline().ingest(
            "g03-m5-timing", _fc(50), crs="EPSG:4326")
        assert result.ok
        assert result.elapsed_ms > 0
        assert "validate" in result.step_timings_ms
        assert "profile" in result.step_timings_ms
        assert all(v >= 0 for v in result.step_timings_ms.values())
        assert result.to_dict()["step_timings_ms"]


# ═══════════════════ 零记录诚实（GD-05 后半） ════════════════════════════


class TestZeroRecordHonesty:
    def test_geojson_url_empty_fc_reports_zero_not_synthetic(self, patched_safe_sessions):
        """空 FC 源 → 如实零要素（不造数据、不报错、不 truncated）。"""
        from tests.data.fabric_fixtures import FakeSourceServer

        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.geojson_url_adapter import (
            GeojsonUrlAdapter,
        )

        server = FakeSourceServer()

        def _handler(request):
            from tests.fixtures.data_fabric.fake_server import make_response

            return make_response(
                request.url,
                json_body={"type": "FeatureCollection", "features": []},
                headers={"Content-Type": "application/geo+json"},
            )

        server._add("/empty.geojson", _handler)
        patched_safe_sessions(server)
        adapter = GeojsonUrlAdapter(ConnectionProfile(
            id="gj_empty", source_type="geojson_url",
            url=server.base_url + "/empty.geojson"))
        assert adapter.probe() is True
        preview = adapter.preview("default", limit=10)
        assert preview["features"] == []
        assert preview["feature_count"] == 0
        assert preview["total_available"] == 0
        assert preview["truncated"] is False

    async def test_empty_fc_ingest_is_zero_not_error(self, sem_store):
        """空 FC 上传：诚实零要素入库（不虚构、不静默拒收）。"""
        result = await get_ingest_pipeline().ingest(
            "g03-m5-empty",
            {"type": "FeatureCollection", "features": []},
            crs="EPSG:4326")
        assert result.ok
        assert result.profile_summary.get("row_count") == 0


# ═══════════════════ GD-09 远端异常演练 ═══════════════════════════════════


class TestGd09RemoteHostile:
    def test_http_429_and_5xx_typed_unreachable(self, patched_safe_sessions):
        """429/500 → SourceUnreachableError（typed，绝不静默空结果）。"""
        from tests.data.fabric_fixtures import FakeSourceServer

        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.geojson_url_adapter import (
            GeojsonUrlAdapter,
        )
        from app.services.data_fabric.errors import SourceUnreachableError

        for status in (429, 500, 503):
            server = FakeSourceServer()

            def _handler(request, _status=status):
                from tests.fixtures.data_fabric.fake_server import make_response

                server._record(request)
                return make_response(request.url, status=_status)

            server._add(f"/s{status}.geojson", _handler)
            patched_safe_sessions(server)
            adapter = GeojsonUrlAdapter(ConnectionProfile(
                id=f"gj_{status}", source_type="geojson_url",
                url=server.base_url + f"/s{status}.geojson"))
            with pytest.raises(SourceUnreachableError):
                adapter.preview("default", limit=5)

    def test_body_over_byte_budget_typed_rejected(self, patched_safe_sessions):
        """超字节帽体 → SourceBadResponseError（有界下载，不整包吞）。"""
        from tests.data.fabric_fixtures import FakeSourceServer

        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.geojson_url_adapter import (
            GeojsonUrlAdapter,
        )
        from app.services.data_fabric.errors import SourceBadResponseError

        server = FakeSourceServer()

        def _handler(request):
            from tests.fixtures.data_fabric.fake_server import make_response

            server._record(request)
            # 假装远端流式回超大体（> 生产帽；fixture 无需真造 16MB 内容）
            big = {"type": "FeatureCollection",
                   "features": [], "pad": "x" * 64}
            resp = make_response(request.url, json_body=big,
                                 headers={"Content-Type": "application/geo+json"})
            resp.raw = _OverBudgetStream()
            resp._content = None
            resp._content_consumed = False
            return resp

        server._add("/huge.geojson", _handler)
        patched_safe_sessions(server)
        # 帽收紧到 1KB 使演练确定性（不依赖生产 16MiB 默认值）
        import app.services.data_fabric.adapters.geojson_url_adapter as gj_mod
        import unittest.mock as _mock

        with _mock.patch.object(gj_mod, "max_response_bytes", lambda: 1024):
            adapter = GeojsonUrlAdapter(ConnectionProfile(
                id="gj_huge", source_type="geojson_url",
                url=server.base_url + "/huge.geojson"))
            with pytest.raises(SourceBadResponseError):
                adapter.preview("default", limit=5)


class _OverBudgetStream:
    """假流：每个 chunk 64KB，永远读不完（解压/流式炸弹形状）。"""

    def read(self, *args):  # noqa: ANN001, D401 — requests raw 协议最小实现
        return b"x" * 65536

    def close(self):
        return None


# ═══════════════════ GD-04 fail-closed 核验 ══════════════════════════════


class TestGd04FailClosed:
    def test_ambiguous_field_mapping_needs_clarification(self):
        """歧义字段映射 → needs_clarification / 拒绝理由（fail-closed，不猜）。"""
        from app.lib.data.profile import FieldProfile
        from app.services.data_preparation.field_proposal import (
            propose_field_roles,
        )

        def _fp(name, dtype="number", mn=None, mx=None, unique=None):
            return FieldProfile(name=name, dtype=dtype, null_ratio=0.0,
                                min=mn, max=mx, unique=unique,
                                temporal=False, samples=[])

        # 「人口」语义命中两个字段 → 提案歧义面必须显式（其一被拒或需澄清）
        props = {p.field: p for p in propose_field_roles({
            "总人口": _fp("总人口", mn=100.0, mx=900.0),
            "常住人口": _fp("常住人口", mn=90.0, mx=850.0),
            "区县名": _fp("区县名", dtype="string", unique=20),
        })}
        assert set(props) == {"总人口", "常住人口", "区县名"}
        # 区县名 → admin_code 候选（置信 0.68 < 确认阈值 → needs_clarification，
        # fail-closed：低置信不冒充确定映射）
        admin = props["区县名"]
        assert admin.top is not None and admin.top.role == "admin_code"
        assert admin.needs_clarification, "低置信候选必须显式需澄清，不静默确认"
        # 值域反证 fail-closed：名为纬度但值域 [73,135] → lat 被反证拒绝，
        # 只剩低置信 lon 候选 → needs_clarification（不冒充确定纬度）
        bad = propose_field_roles({"纬度": _fp("纬度", mn=73.0, mx=135.0)})
        assert bad[0].needs_clarification
        assert ("lat", "range_conflict") in bad[0].rejected
        assert all(c.role != "lat" for c in bad[0].candidates)

    def test_unit_conflict_yields_typed_gap(self):
        """同名字段单位冲突 → PREFLIGHT_UNIT_CONFLICT（fail-closed typed gap）。"""
        from app.services.dataset_semantics.preflight import (
            CODE_UNIT_CONFLICT,
            preflight_multi_dataset,
        )

        from app.lib.gis.dataset_profile import DatasetProfile
        from app.services.dataset_semantics import derive_descriptor

        profile = DatasetProfile(
            source="ref_descriptor", feature_count=10,
            geometry_types=["Polygon"], crs="EPSG:4326",
            fields={"长度_km": "number"}, numeric_fields=["长度_km"],
            fields_status="explicit",
        )

        def _desc(unit_overrides=None, key="k"):
            return derive_descriptor(
                profile, dataset_key=key,
                features=[{"properties": {"长度_km": 5}} for _ in range(3)],
                unit_overrides=unit_overrides)

        report = preflight_multi_dataset({
            "declared_km": _desc(),
            "declared_m": _desc(unit_overrides={"长度_km": "meters"}, key="k2"),
        })
        codes = [g.code for g in report.gaps]
        assert CODE_UNIT_CONFLICT in codes, (
            "单位冲突必须 fail-closed 披露 typed gap，不得静默 compatible"
        )
        assert report.verdict == "gaps"


# ═══════════════════ GD-07 版本失效链核验 ════════════════════════════════


class TestGd07VersionInvalidation:
    async def test_descriptor_change_invalidates_reuse(self, sem_store):
        """语义变化（要素数/字段 schema）→ descriptor 指纹不同（复用门失效基础）。

        既有契约（ADR-0265）：语义身份 = schema 级——值分布本身不入指纹。
        """
        from app.lib.gis.dataset_profile import DatasetProfile
        from app.services.dataset_semantics import derive_descriptor

        def _desc(feature_count=10, extra_field=False):
            fields = {"val": "number"}
            if extra_field:
                fields["kind"] = "string"
            profile = DatasetProfile(
                source="ref_descriptor", feature_count=feature_count,
                geometry_types=["Point"], crs="EPSG:4326",
                fields=fields, numeric_fields=["val"],
                categorical_fields=["kind"] if extra_field else [],
                fields_status="explicit",
            )
            return derive_descriptor(
                profile, dataset_key="k",
                features=[{"properties": {"val": i}} for i in range(5)])

        d1 = _desc(feature_count=10)
        d2 = _desc(feature_count=25)
        d3 = _desc(feature_count=10, extra_field=True)
        assert d1.descriptor_fingerprint != d2.descriptor_fingerprint, (
            "要素数变化必须改变语义指纹"
        )
        assert d1.descriptor_fingerprint != d3.descriptor_fingerprint, (
            "字段 schema 变化必须改变语义指纹"
        )
        # 值分布本身不入指纹（语义身份 = schema 级，ADR-0265 契约）
        p = DatasetProfile(
            source="ref_descriptor", feature_count=10,
            geometry_types=["Point"], crs="EPSG:4326",
            fields={"val": "number"}, numeric_fields=["val"],
            fields_status="explicit")
        d4 = derive_descriptor(
            p, dataset_key="k",
            features=[{"properties": {"val": i * 100}} for i in range(5)])
        assert d1.descriptor_fingerprint == d4.descriptor_fingerprint

    async def test_license_metadata_change_keeps_semantic_identity(self, sem_store):
        """补填 license 不改变语义指纹（不误判 stale）——GD-07 契约。"""
        from app.lib.gis.dataset_profile import DatasetProfile
        from app.services.dataset_semantics import derive_descriptor

        p = DatasetProfile(
            source="ref_descriptor", feature_count=10,
            geometry_types=["Point"], crs="EPSG:4326",
            fields={"val": "number"}, numeric_fields=["val"],
            fields_status="explicit",
        )
        feats = [{"properties": {"val": i}} for i in range(5)]
        no_license = derive_descriptor(p, dataset_key="k2", features=feats)
        with_license = derive_descriptor(
            p, dataset_key="k2", features=feats, license="CC-BY-4.0")
        assert no_license.descriptor_fingerprint == with_license.descriptor_fingerprint
        assert with_license.license == "CC-BY-4.0"
        assert with_license.dimension_provenance["license"] == "observed"
        assert no_license.dimension_provenance["license"] == "unknown"
