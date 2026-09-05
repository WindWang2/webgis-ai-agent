"""Catalog docs freshness — 生成文档与 registry 零漂移（ADR-0101 §文档）.

docs/cartography/ 的目录文件是 registry 的生成产物；registry 变更后未
重新生成会在本测试爆出。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.cartography


def test_catalog_docs_fresh() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.lib.cartography.catalog_docs", "--check"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"catalog docs 漂移：{result.stdout}\n"
        f"修复：python -m app.lib.cartography.catalog_docs"
    )


def test_catalog_docs_nonempty() -> None:
    docs_dir = REPO_ROOT / "docs" / "cartography"
    expected = {
        "map-model-catalog.md",
        "component-catalog.md",
        "composition-template-catalog.md",
        "theme-palette-catalog.md",
        "renderer-parity-matrix.md",
    }
    for name in expected:
        path = docs_dir / name
        assert path.exists() and path.stat().st_size > 500, f"{name} 缺失或空壳"


def test_design_docs_present() -> None:
    docs_dir = REPO_ROOT / "docs" / "cartography"
    for name in ("design-system.md", "layout-solver.md", "template-authoring-guide.md"):
        assert (docs_dir / name).exists(), f"{name} 缺失"
