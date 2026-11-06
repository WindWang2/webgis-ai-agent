"""Run the actual source lint without importing the optional raster runtime."""
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]

# A fresh interpreter prevents a full backend test session's already-imported
# numpy/rasterio modules from hiding accidental eager imports in adapter classes.
_LINT_WITHOUT_RASTER = r'''
import importlib.abc
from pathlib import Path
import runpy
import sys

class NoRasterRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app.lib.geo_raster" or fullname.split(".")[0] in {
            "numpy", "rasterio", "osgeo", "geopandas", "pyogrio", "pyproj", "shapely",
        }:
            raise AssertionError(f"Source lint imported raster runtime: {fullname}")
        return None

sys.meta_path.insert(0, NoRasterRuntime())
namespace = runpy.run_path(sys.argv[1])
main = namespace["main"]
if len(sys.argv) > 2:
    main.__globals__["REPO"] = Path(sys.argv[2])
raise SystemExit(main())
'''


def _lint(root=None):
    command = [sys.executable, "-c", _LINT_WITHOUT_RASTER,
               str(REPO / "scripts/check_source_registry.py")]
    if root is not None:
        command.append(str(root))
    return subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=30)


def test_full_source_registry_lint_does_not_load_raster_runtime():
    result = _lint()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[lint] sources loaded:" in result.stdout
    assert "[lint] OK" in result.stdout


@pytest.mark.parametrize("claim", ["bbox", "cql", "pagination"])
def test_lightweight_lint_still_checks_real_cog_capabilities(tmp_path, claim):
    directory = tmp_path / "config/sources"
    directory.mkdir(parents=True)
    (directory / "bad.yaml").write_text(yaml.safe_dump({
        "source_id": "bad_cog", "name": "Invalid COG capability", "protocol": "cog",
        "endpoint": "${LOCAL_DEM_PATH}", "pushdown": {claim: True},
    }))
    result = _lint(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"claims pushdown '{claim}' but adapter 'cog' does not support it" in result.stdout
    assert "Source lint imported raster runtime" not in result.stderr


def test_lightweight_lint_still_rejects_undeclared_fallback(tmp_path):
    directory = tmp_path / "config/sources"
    directory.mkdir(parents=True)
    (directory / "bad.yaml").write_text(yaml.safe_dump({
        "source_id": "bad_cog", "name": "Invalid fallback", "protocol": "cog",
        "endpoint": "${LOCAL_DEM_PATH}", "fallbacks": ["not_declared"],
    }))
    result = _lint(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "fallback 'not_declared' is not a declared source_id" in result.stdout
    assert "Source lint imported raster runtime" not in result.stderr
