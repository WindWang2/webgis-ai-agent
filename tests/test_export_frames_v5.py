"""V5（ADR-0118 D8）frames 生产入口 —— export_thematic_map 工具面契约。

review-r1 MAJOR-3：frame-composer 运行时必须有 agent 可达入口。
frames 经 ExportMapArgs → export_thematic_map 归一校验 → export_map 命令
params → 前端 ExportRequest.frames（runFrameExport）。
"""
import pytest

from app.tools.cartography import register_cartography_tools
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_cartography_tools(r)
    return r


@pytest.mark.asyncio
async def test_frames_pass_through_to_command_params(registry):
    out = await registry.dispatch("export_thematic_map", {
        "title": "分市小倍数",
        "format": "pdf",
        "frames": [
            {"title": "朝阳区", "where": {"layerId": "lyr-district", "field": "city", "equal": "朝阳"}},
            {"title": "海淀区", "where": {"layerId": "lyr-district", "field": "city", "equal": "海淀"}},
            {"extent": [116.0, 39.6, 116.8, 40.2]},
        ],
    })
    assert out["command"] == "export_map"
    frames = out["params"]["frames"]
    assert len(frames) == 2 + 1
    assert frames[0]["where"]["equal"] == "朝阳"
    assert frames[2]["extent"] == [116.0, 39.6, 116.8, 40.2]
    assert "3 帧" in out["system_message"]


@pytest.mark.asyncio
async def test_frames_invalid_shape_rejected_loud(registry):
    out = await registry.dispatch("export_thematic_map", {
        "title": "t",
        "frames": [{"where": {"field": "city"}},  # 缺 layerId
                   ],
    })
    assert out.get("success") is False
    assert "frames[0]" in out["error"]


@pytest.mark.asyncio
async def test_frames_unknown_field_rejected(registry):
    out = await registry.dispatch("export_thematic_map", {
        "title": "t",
        "frames": [{"boom": 1}],
    })
    assert out.get("success") is False
    assert "boom" in out["error"]


@pytest.mark.asyncio
async def test_frames_over_limit_rejected(registry):
    out = await registry.dispatch("export_thematic_map", {
        "title": "t",
        "frames": [{"title": f"f{i}"} for i in range(51)],
    })
    assert out.get("success") is False
    assert "≤50" in out["error"]


@pytest.mark.asyncio
async def test_no_frames_omits_param(registry):
    out = await registry.dispatch("export_thematic_map", {"title": "t"})
    assert "frames" not in out["params"]
