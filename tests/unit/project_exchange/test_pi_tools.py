"""Pi 工具面测试（T9）——六工具注册 + 元数据面 + typed 错误投影。"""
import pytest

from app.tools.registry import ToolRegistry
from app.tools.project_exchange_tools import register_project_exchange_tools

_TOOL_NAMES = (
    "project_exchange_inspect",
    "project_exchange_export_plan",
    "project_exchange_export",
    "project_exchange_import_inspect",
    "project_exchange_import_plan",
    "project_exchange_import",
)


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_project_exchange_tools(reg)
    return reg


class TestRegistration:
    def test_all_six_tools_registered(self, registry):
        for name in _TOOL_NAMES:
            assert registry.get(name) is not None, f"缺工具: {name}"

    def test_mutation_tools_marked_state_mutation(self, registry):
        for name in ("project_exchange_export", "project_exchange_import"):
            meta = registry.metadata(name)
            assert meta.get("side_effect") == "state_mutation", name

    def test_read_tools_marked_pure(self, registry):
        for name in ("project_exchange_inspect",
                     "project_exchange_export_plan",
                     "project_exchange_import_inspect",
                     "project_exchange_import_plan"):
            meta = registry.metadata(name)
            assert meta.get("side_effect") == "pure", name

    def test_schemas_declared_for_all_tools(self, registry):
        """Pi dump 依赖真实 schema —— 六工具都必须有参数面。"""
        for name in _TOOL_NAMES:
            func = registry.get(name)
            import inspect

            sig = inspect.signature(func)
            assert len(sig.parameters) >= 1, name


class TestTypedErrors:
    @pytest.mark.asyncio
    async def test_import_inspect_invalid_upload_id(self, registry):
        tool = registry.get("project_exchange_import_inspect")
        result = await tool("garbage")
        assert result["status"] == "error"
        assert result["error_type"] == "IMPORT_SOURCE_INVALID"

    @pytest.mark.asyncio
    async def test_import_missing_upload_not_found(self, registry):
        tool = registry.get("project_exchange_import_inspect")
        result = await tool("pkgnz-" + "0" * 16)
        assert result["status"] == "error"
        assert result["error_type"] == "IMPORT_SOURCE_INVALID"

    @pytest.mark.asyncio
    async def test_export_inspect_unknown_project(self, registry):
        tool = registry.get("project_exchange_inspect")
        result = await tool("proj-not-exist-xyz")
        assert result["status"] == "error"
        assert result["error_type"] == "PROJECT_NOT_FOUND"
