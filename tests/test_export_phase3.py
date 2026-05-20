"""Phase 3: 导出能力强化 — paper_size / dpi / svg / 批量导出"""
import pytest

from app.tools.registry import ToolRegistry
from app.tools.cartography import register_cartography_tools


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_cartography_tools(r)
    return r


@pytest.mark.asyncio
async def test_export_thematic_map_exposes_paper_dpi(registry):
    out = await registry.dispatch("export_thematic_map", {
        "title": "test",
        "format": "pdf",
        "paper_size": "A4",
        "orientation": "portrait",
        "dpi": 300,
    })
    assert out["command"] == "export_map"
    p = out["params"]
    assert p["format"] == "pdf"
    assert p["paperSize"] == "A4"
    assert p["orientation"] == "portrait"
    assert p["dpi"] == 300


@pytest.mark.asyncio
async def test_export_thematic_map_normalizes_invalid_values(registry):
    out = await registry.dispatch("export_thematic_map", {
        "title": "t",
        "format": "xyz",
        "paper_size": "letter",
        "orientation": "sideways",
    })
    assert out["params"]["format"] == "png"
    assert out["params"]["paperSize"] == "screen"
    assert out["params"]["orientation"] == "landscape"


@pytest.mark.asyncio
async def test_export_thematic_map_supports_svg(registry):
    out = await registry.dispatch("export_thematic_map", {"title": "t", "format": "svg"})
    assert out["params"]["format"] == "svg"


@pytest.mark.asyncio
async def test_export_thematic_map_rejects_extreme_dpi(registry):
    out = await registry.dispatch("export_thematic_map", {"title": "t", "dpi": 10000})
    assert "error" in out or out.get("code") == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_export_batch_maps_emits_command_list(registry):
    out = await registry.dispatch("export_batch_maps", {
        "titles": ["总览", "北部", "南部"],
        "format": "pdf",
        "paper_size": "A4",
        "dpi": 200,
    })
    assert out["count"] == 3
    assert len(out["commands"]) == 3
    for i, cmd in enumerate(out["commands"]):
        assert cmd["command"] == "export_map"
        assert cmd["params"]["title"] in ("总览", "北部", "南部")
        assert cmd["params"]["paperSize"] == "A4"
        assert cmd["params"]["dpi"] == 200


@pytest.mark.asyncio
async def test_export_batch_maps_rejects_empty(registry):
    out = await registry.dispatch("export_batch_maps", {"titles": []})
    assert "error" in out
