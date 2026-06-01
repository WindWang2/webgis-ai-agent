"""Test: multi_ring_buffer must not use mutable default argument."""
import ast


def test_multi_ring_buffer_no_mutable_default():
    """distances parameter must use None default, not [500, 1000, 1500]."""
    with open("app/tools/spatial_stats.py") as f:
        source = f.read()

    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "multi_ring_buffer":
            defaults = node.args.defaults
            for d in defaults:
                assert not isinstance(d, ast.List), (
                    f"Mutable default argument found. Use None and initialize inside function."
                )
            return

    pytest.fail("multi_ring_buffer not found in spatial_stats.py")


import pytest
