"""GeoCompute/Data Plane boundary enforcement (ADR-0096 D1).

The GeoCompute / Data Plane layers must stay free of Agent/Product Plane concerns:
no gis_harness internals, no Pi bridge, no chat/turn semantics, no tool-registry
dispatch, no frontend state. Enforced structurally (AST import scan) so the
boundary survives refactors instead of relying on review convention.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Data-plane owned layers (ADR-0096 D1).
DATA_PLANE_ROOTS = [
    "app/services/geocompute",
    "app/services/data_fabric",
    "app/lib/geo_raster",
    "app/lib/geo_analysis",
    "app/lib/geo_processor",
]

# Agent/Product Plane internals the data plane must never depend on.
FORBIDDEN_PREFIXES = (
    "app.services.gis_harness",
    "app.agent_pi_bridge",
    "app.services.chat",
    "app.services.task_tracker",
    "app.tools",
    "app.services.skills",
    "app.services.execution_engine",
)

# Module-level shims that re-export execution_plane internals are fine to import
# from the data plane itself (they are part of it).
_OWN_ROOTS = tuple(Path(REPO_ROOT, p) for p in DATA_PLANE_ROOTS)


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in DATA_PLANE_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        files.extend(sorted(base.rglob("*.py")))
    return files


def _imported_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


@pytest.mark.parametrize("path", _iter_python_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_data_plane_imports_stay_inside_boundary(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for module in _imported_modules(tree):
        for forbidden in FORBIDDEN_PREFIXES:
            assert not (
                module == forbidden or module.startswith(forbidden + ".")
            ), (
                f"{path.relative_to(REPO_ROOT)} imports '{module}' which crosses the "
                f"GeoCompute/Data Plane boundary (ADR-0096 D1): data-plane layers must not "
                f"depend on {forbidden}"
            )


def test_boundary_scan_actually_covers_layers() -> None:
    """Guard against the scan silently shrinking to zero files."""
    files = _iter_python_files()
    assert len(files) >= 20, f"boundary scan found only {len(files)} files; layers moved?"
    assert any("data_fabric" in str(p) for p in files)
