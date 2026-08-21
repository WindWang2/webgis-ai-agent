"""Tests for heatmap_data tool — native render mode (RC3 regression).

RC3: render_type="native" must include legend_spec in the result so the
frontend FloatingLegend / ThematicLegend can render a color gradient.
"""
import pytest
from unittest.mock import patch, MagicMock

from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
from app.lib.tool_cache import _reset_redis_client_for_tests
from app.services import tool_metrics


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(tmp_path / "tool_metrics.jsonl"))
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()
    yield
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()


def _make_point_fc(n: int) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0 + i * 0.01]},
             "properties": {"weight": float(i)}}
            for i in range(n)
        ],
    }


async def _dispatch_native(n=20, palette="classic", radius=2000):
    """Dispatch heatmap_data with render_type='native' through ToolRegistry."""
    reg = ToolRegistry()
    register_spatial_tools(reg)
    storage = {}
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        return await reg.dispatch(
            "heatmap_data",
            {"geojson": _make_point_fc(n), "render_type": "native", "palette": palette, "radius": radius},
            session_id=None,
        )


class TestHeatmapNativeLegendSpec:
    """RED: native mode should return legend_spec but currently doesn't."""

    async def test_native_returns_legend_spec(self):
        result = await _dispatch_native(n=20, palette="classic")
        assert isinstance(result, dict)
        assert result.get("command") == "add_native_heatmap"
        # This assertion should FAIL in RED phase — native mode has no legend_spec
        assert "legend_spec" in result, "native heatmap must include legend_spec"
        legend = result["legend_spec"]
        assert legend["type"] == "continuous"
        assert "min" in legend
        assert "max" in legend
        assert "palette_colors" in legend
        assert len(legend["palette_colors"]) >= 3

    async def test_native_legend_uses_requested_palette(self):
        result = await _dispatch_native(n=20, palette="viridis")
        assert "legend_spec" in result
        assert result["legend_spec"]["palette"] == "Viridis"

    async def test_native_legend_colors_match_frontend_palette(self):
        """图例渐变色与前端 HEATMAP_PALETTES 同源（classic：蓝→青→绿→黄→橙→红）。

        此前 native 图例走 matplotlib YlOrRd（黄→红），与地图 heatmap-color
        的蓝→红渐变错位——用户看到「地图单蓝、图例另一套色」。图例色现在
        直出与前端停靠点相同的 6 色。
        #690: n 需 >= 阈值(10)才走 native 成功路径，否则被确定性守卫拦截。
        """
        result = await _dispatch_native(n=12, palette="classic")
        colors = result["legend_spec"]["palette_colors"]
        assert len(colors) == 6
        assert colors[0] == "#428cd2"  # 与 renderer.ts classic 首色 rgb(66,140,210) 一致
        assert colors[-1] == "#eb2828"  # 尾色 rgb(235,40,40)
        # viridis 同样直出前端色
        result_v = await _dispatch_native(n=12, palette="viridis")
        assert result_v["legend_spec"]["palette_colors"][0] == "#482878"

    async def test_native_carries_type_hint_for_mapspec_authoring(self):
        """type_hint=heatmap 驱动 dispatch 的 MapSpec 授权产出 heatmap 图层。

        无 type_hint 时点要素被推断为 circle —— 热力图从未以 heatmap 层
        挂到地图上（用户实测「热力图没有」的根因之一）。
        #690: n 需 >= 阈值才走 native 成功路径。
        """
        result = await _dispatch_native(n=12, palette="classic")
        assert result["type_hint"] == "heatmap"
        assert result["metadata"]["palette"] == "classic"

    async def test_native_includes_weight_field_in_metadata(self):
        """Verify native result carries the render metadata needed by frontend."""
        result = await _dispatch_native(n=10, palette="thermal", radius=3000)
        assert result["metadata"]["render_type"] == "native"
        assert result["metadata"]["palette"] == "thermal"
        assert result["metadata"]["radius"] == 3000
