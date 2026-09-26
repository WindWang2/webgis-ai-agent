"""F07：ExecutionCatalog 生成文档 freshness（零漂移）+ manifest 形态契约。

docs/catalog/execution-catalog/ 下的文件是权威 registry 的生成产物；
registry 变更后未重新生成会在本测试爆出（与 cartography catalog_docs
同一防漂移纪律）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_execution_catalog_docs_fresh() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.lib.gis.execution_catalog_docs", "--check"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, (
        f"execution catalog docs 漂移：{result.stdout}\n"
        f"修复：python -m app.lib.gis.execution_catalog_docs"
    )


def test_execution_catalog_docs_nonempty() -> None:
    docs_dir = REPO_ROOT / "docs" / "catalog" / "execution-catalog"
    expected = {
        "summary.md",
        "capability-chains.md",
        "tools.md",
        "recipes.md",
        "deprecations.md",
        "execution-catalog.manifest.json",
    }
    for name in expected:
        path = docs_dir / name
        assert path.exists(), f"missing generated doc: {name}"
        assert path.stat().st_size > 0, f"empty generated doc: {name}"


def test_execution_catalog_manifest_shape() -> None:
    path = (REPO_ROOT / "docs" / "catalog" / "execution-catalog"
            / "execution-catalog.manifest.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["manifest_schema_version"] == 1
    assert payload["catalog_version"] >= 1
    assert len(payload["generation_fingerprint"]) == 32
    assert set(payload["counts"]) == {"capability", "algorithm", "tool", "recipe"}
    assert payload["counts"]["tool"] > 100
    assert "conformance" in payload and "codes" in payload["conformance"]
    assert payload["conformance"]["fatal"] == 0, (
        "manifest 声称 0 fatal —— 若 registry 引用破损，本断言与启动 strict "
        "闸同时爆（诚实性）")
    ids = {(e["kind"], e["id"]) for e in payload["entries"]}
    assert ("tool", "spatial_aggregate") in ids
    assert len(payload["entries"]) > 300
    # entries 按字典序确定排列（审计 diff 友好）
    keys = [(e["kind"], e["id"]) for e in payload["entries"]]
    assert keys == sorted(keys)
