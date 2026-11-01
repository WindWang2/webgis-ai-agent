"""GOAL-02 M5：OGC 服务工具面 —— 注册纪律/发现/资格双轨/#1868 兼容。"""
import pytest

from app.services.data_fabric.service_qualification import (
    DISPLAY_ONLY,
    NUMERIC_RASTER,
    qualification_for_descriptor,
)
from app.tools.categories import _MODULE_DEFAULTS as MODULE_CATEGORY_MAP
from app.tools.registry import ToolRegistry
from app.tools.ogc_service_tools import register_ogc_service_tools

_TOOLS = {
    "inspect_data_service",
    "read_coverage_subset",
    "materialize_remote_raster_asset",
    "add_tile_service_layer",
}


class TestRegistration:
    def test_registered_with_descriptor_discipline(self):
        reg = ToolRegistry()
        register_ogc_service_tools(reg)
        names = set(reg.list_tools())
        assert _TOOLS <= names
        # categories 归类（audit 门：未分类模块会红）
        assert MODULE_CATEGORY_MAP.get("app.tools.ogc_service_tools") == "data_access"

    def test_global_registry_no_name_conflicts(self):
        """全量装载（init_tools）后无重名且四工具在场（append-only 契约下
        重名注册会当场抛 ValueError）。"""
        from app.tools import init_tools

        reg = ToolRegistry()
        init_tools(reg)
        names = set(reg.list_tools())
        assert _TOOLS <= names
        # 与既有工具无重名（同时在场）
        assert {"inspect_data_source", "materialize_dataset"} <= names


class TestInspectDataService:
    async def test_tiles_autodetected_with_three_state(self, patched_safe_sessions, ogc_fake_server):
        url = ogc_fake_server.add_ogc_tiles()
        patched_safe_sessions(ogc_fake_server)
        reg = ToolRegistry()
        register_ogc_service_tools(reg)
        result = await reg.dispatch("inspect_data_service", {
            "url": url, "session_id": "sess-inspect-01",
        })
        assert result["status"] == "success"
        assert result["service_type"] == "ogc_api_tiles"
        ds = result["datasets"][0]
        assert ds["id"] == "admin:WebMercatorQuad"
        assert ds["metadata"]["capability"]["state"] == "supported"
        # G01 双轨：瓦片服务 display_only，不可数值分析
        assert ds["qualification"]["role_class"] == DISPLAY_ONLY
        assert ds["qualification"]["numeric_raster_eligible"] is False

    async def test_coverages_autodetected_numeric(self, patched_safe_sessions, ogc_fake_server):
        url = ogc_fake_server.add_ogc_coverages()
        patched_safe_sessions(ogc_fake_server)
        reg = ToolRegistry()
        register_ogc_service_tools(reg)
        result = await reg.dispatch("inspect_data_service", {
            "url": url, "session_id": "sess-inspect-02",
        })
        assert result["status"] == "success"
        assert result["service_type"] == "ogc_api_coverages"
        ds = result["datasets"][0]
        assert ds["qualification"]["role_class"] == NUMERIC_RASTER
        assert ds["qualification"]["numeric_raster_eligible"] is True

    async def test_unreachable_honest(self, patched_safe_sessions, ogc_fake_server):
        # fake server 无路由的 URL → 404 → probe False → 诚实不可达
        patched_safe_sessions(ogc_fake_server)
        reg = ToolRegistry()
        register_ogc_service_tools(reg)
        result = await reg.dispatch("inspect_data_service", {
            "url": ogc_fake_server.base_url + "/nothing-here",
        })
        assert result["status"] == "unreachable_or_unsupported"
        assert "hint" in result

    async def test_invalid_url_rejected_without_network(self):
        reg = ToolRegistry()
        register_ogc_service_tools(reg)
        result = await reg.dispatch("inspect_data_service", {"url": "ftp://x/y"})
        assert result["status"] == "error"


class TestQualificationDualTrack:
    def test_tile_protocols_are_display_only(self):
        for st in ("ogc_api_tiles", "wms", "wmts", "pmtiles"):
            ev = qualification_for_descriptor({"source_type": st, "metadata": {}})
            assert ev["role_class"] == DISPLAY_ONLY
            assert ev["numeric_raster_eligible"] is False

    def test_coverage_ready_is_numeric(self):
        ev = qualification_for_descriptor({
            "source_type": "ogc_api_coverages",
            "metadata": {"subset_read_ready": True, "capability": {"state": "supported"}},
        })
        assert ev["role_class"] == NUMERIC_RASTER

    def test_coverage_not_ready_is_unknown(self):
        ev = qualification_for_descriptor({
            "source_type": "ogc_api_coverages",
            "metadata": {"subset_read_ready": False},
        })
        assert ev["role_class"] == "unknown"
        assert ev["numeric_raster_eligible"] is False

    def test_materialized_cog_ref_is_numeric(self):
        ev = qualification_for_descriptor("ref:fabric-cog/abc123")
        assert ev["role_class"] == NUMERIC_RASTER


class TestPrep1868Compatibility:
    async def test_prepare_accepts_raster_ref_typed_not_crash(self, tmp_path, monkeypatch):
        """#1868 inspect 对本方向 raster ref 给出类型化回应（不崩溃、不冒充
        可绑定字段），工作台契约不变。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
        from app.services.data_preparation.service import InspectError, inspect_source

        # #1868 的设计契约：不可用 ref → 类型化 InspectError（不伪造收据、
        # 不崩溃、不冒充可绑定矢量字段）。raster ref 走此分支即"接受合法
        # descriptor"（拒绝也是类型化拒绝）。
        with pytest.raises(InspectError) as ei:
            await inspect_source("sess-prep-01", "ref:fabric-cog/deadbeef0000ffff")
        assert "ref" in str(ei.value).lower() or "unavailable" in str(ei.value).lower()
