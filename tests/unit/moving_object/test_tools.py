"""Tool-face registration tests: trajectory family in a real ToolRegistry."""
from __future__ import annotations

import pytest

from app.tools.moving_object_tools import TrajectoryBuildArgs
from app.tools.registry import ToolRegistry


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    from app.tools.moving_object_tools import register_moving_object_tools
    register_moving_object_tools(reg)
    return reg


def _build_schema(registry: ToolRegistry, name: str) -> dict:
    subset = registry.get_schemas_subset({name})
    return subset[0] if subset else {}


def test_four_tools_registered(registry: ToolRegistry) -> None:
    for name in ("trajectory_inspect", "trajectory_build",
                 "trajectory_apply", "trajectory_query"):
        assert registry.get(name) is not None, name


def test_dataset_params_are_declared_ref_cursors(registry: ToolRegistry) -> None:
    # ref_cursor declaration: registry transparent-deref must skip these args
    keys = registry._declared_ref_cursor_keys(TrajectoryBuildArgs)
    assert "dataset" in keys
    assert "regions_ref" in keys


def test_session_id_not_in_llm_schema(registry: ToolRegistry) -> None:
    schema = _build_schema(registry, "trajectory_build")
    text = str(schema)
    # registry strips session_id from the model-facing schema (server-injected)
    assert "session_id" not in text


def test_inspect_is_read_only_build_is_compute_apply_is_mutation(
    registry: ToolRegistry,
) -> None:
    d_inspect = registry.descriptor("trajectory_inspect")
    d_build = registry.descriptor("trajectory_build")
    d_apply = registry.descriptor("trajectory_apply")
    assert d_inspect.side_effect == "cacheable_read"
    assert d_build.side_effect == "deterministic_compute"
    assert d_apply.side_effect == "state_mutation"
