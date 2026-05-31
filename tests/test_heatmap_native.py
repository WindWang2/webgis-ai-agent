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


def _dispatch_native(n=20, palette="classic", radius=2000):
    """Dispatch heatmap_data with render_type='native' through ToolRegistry."""
    reg = ToolRegistry()
    register_spatial_tools(reg)
    storage = {}
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            reg.dispatch(
                "heatmap_data",
                {"geojson": _make_point_fc(n), "render_type": "native", "palette": palette, "radius": radius},
                session_id=None,
            )
        )


class TestHeatmapNativeLegendSpec:
    """RED: native mode should return legend_spec but currently doesn't."""

    def test_native_returns_legend_spec(self):
        result = _dispatch_native(n=20, palette="classic")
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

    def test_native_legend_uses_requested_palette(self):
        result = _dispatch_native(n=20, palette="viridis")
        assert "legend_spec" in result
        assert result["legend_spec"]["palette"] == "Viridis"

    def test_native_includes_weight_field_in_metadata(self):
        """Verify native result carries the render metadata needed by frontend."""
        result = _dispatch_native(n=10, palette="thermal", radius=3000)
        assert result["metadata"]["render_type"] == "native"
        assert result["metadata"]["palette"] == "thermal"
        assert result["metadata"]["radius"] == 3000
