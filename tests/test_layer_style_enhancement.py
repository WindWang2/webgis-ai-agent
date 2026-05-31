"""Layer style enhancement tests — expanded update_layer_appearance tool params."""
import pytest
import inspect


class TestExpandedLayerStyleTool:
    """Backend should accept all LayerStyle properties in update_layer_appearance."""

    def _get_tool_fn(self):
        from app.tools.registry import ToolRegistry
        registry = ToolRegistry()
        from app.tools.layer_manager import register_layer_management_tools
        register_layer_management_tools(registry)
        return registry._tools.get("update_layer_appearance")

    def test_tool_is_registered(self):
        fn = self._get_tool_fn()
        assert fn is not None, "update_layer_appearance should be registered"

    def test_tool_accepts_stroke_color_param(self):
        fn = self._get_tool_fn()
        sig = inspect.signature(fn)
        assert "stroke_color" in sig.parameters, f"Missing stroke_color param, got: {list(sig.parameters.keys())}"

    def test_tool_accepts_point_size_param(self):
        fn = self._get_tool_fn()
        sig = inspect.signature(fn)
        assert "point_size" in sig.parameters

    def test_tool_accepts_dash_array_param(self):
        fn = self._get_tool_fn()
        sig = inspect.signature(fn)
        assert "dash_array" in sig.parameters

    def test_tool_accepts_fill_param(self):
        fn = self._get_tool_fn()
        sig = inspect.signature(fn)
        assert "fill" in sig.parameters

    def test_tool_accepts_render_type_param(self):
        fn = self._get_tool_fn()
        sig = inspect.signature(fn)
        assert "render_type" in sig.parameters
