"""Test: Alembic env.py must have target_metadata set (not None)."""
import ast


def test_target_metadata_not_none():
    """target_metadata must be assigned to Base.metadata, not None."""
    with open("migrations/env.py") as f:
        source = f.read()

    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "target_metadata":
                    # Must NOT be None constant
                    assert not (isinstance(node.value, ast.Constant) and node.value.value is None), (
                        "target_metadata = None — Alembic autogenerate will produce empty migrations. "
                        "Should be: from app.core.database import Base; target_metadata = Base.metadata"
                    )
                    return

    pytest.fail("target_metadata assignment not found")


import pytest
