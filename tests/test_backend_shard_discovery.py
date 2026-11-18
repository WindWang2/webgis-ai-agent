"""Exhaustive file sharding; native pytest remains the discovery oracle."""
from collections import Counter
import json
import os
import subprocess
import sys

import pytest

from scripts.backend_test_shards import discover_modules, partition_modules


def test_every_module_once_balanced_and_order_independent():
    modules = [f"tests/delivery/test_security_{i}.py" for i in range(79)]
    shards = partition_modules(modules)
    assert shards == partition_modules(reversed(modules))
    assert Counter(p for shard in shards for p in shard) == Counter(modules)
    assert max(map(len, shards)) - min(map(len, shards)) <= 1
    assert len(shards) == 6


def test_duplicate_input_is_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        partition_modules(["tests/test_a.py", "tests/test_a.py"])


def test_discovery_matches_native_pytest(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --ignore=tests/test_ignored.py\n")
    for name in ("tests/test_root.py", "tests/delivery/test_project_authorization_2196.py",
                 "tests/unit/new_directory/test_security.py", "tests/unit/reverse_test.py",
                 "tests/test_ignored.py", "tests/.hidden/test_hidden.py",
                 "tests/node_modules/test_vendor.py", "tests/helper.py"):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("raise AssertionError('module bodies must not execute in discovery probe')\n")
    probe = tmp_path / "probe.py"
    probe.write_text("""import json
from pathlib import Path
import pytest
paths = []
class Inventory(pytest.Module):
    def collect(self):
        return []
def pytest_pycollect_makemodule(module_path, parent):
    paths.append(module_path.relative_to(parent.config.rootpath).as_posix())
    return Inventory.from_parent(parent, path=module_path)
def pytest_sessionfinish(session, exitstatus):
    Path('native.json').write_text(json.dumps(sorted(paths)))
""")
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONPATH=str(tmp_path))
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                             "--noconftest", "-p", "probe", "tests"],
                            cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode == 5, result.stdout + result.stderr
    expected = json.loads((tmp_path / "native.json").read_text())
    assert discover_modules(tmp_path) == expected
    assert expected == ["tests/delivery/test_project_authorization_2196.py",
                        "tests/test_root.py", "tests/unit/new_directory/test_security.py",
                        "tests/unit/reverse_test.py"]


def test_argument_manifests_collect_actual_test_nodes_once(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    modules = []
    for index in range(8):
        relative = f"tests/delivery/test_security_{index}.py"
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_sentinel():\n    assert True\n")
        modules.append(relative)
    manifests = []
    for index, shard in enumerate(partition_modules(modules)):
        path = tmp_path / f"shard-{index}.txt"
        path.write_text("\n".join(shard) + "\n")
        manifests.append("@" + str(path))
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    command = [sys.executable, "-m", "pytest", "--collect-only", "--noconftest", "-q"]
    direct = subprocess.run(command + ["tests"], cwd=tmp_path, env=env,
                            text=True, capture_output=True)
    sharded = subprocess.run(command + manifests, cwd=tmp_path, env=env,
                             text=True, capture_output=True)
    assert direct.returncode == sharded.returncode == 0
    expected = sorted(line for line in direct.stdout.splitlines() if "::test_sentinel" in line)
    actual = sorted(line for line in sharded.stdout.splitlines() if "::test_sentinel" in line)
    assert len(actual) == len(set(actual)) == 8
    assert actual == expected


@pytest.mark.parametrize("shard", ["0", "7"])
def test_invalid_shard_fails_closed(shard):
    result = subprocess.run([sys.executable, "scripts/backend_test_shards.py", "--shard", shard],
                            text=True, capture_output=True)
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
