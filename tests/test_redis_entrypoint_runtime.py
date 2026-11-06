"""Exercise both entrypoint branches without Docker; CI also runs real Redis."""

import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("helper", [None, "setpriv", "gosu", "missing"])
def test_entrypoint_passes_runtime_config_as_explicit_include(tmp_path, helper):
    """Run real shell control flow with each image's privilege-drop utility."""
    runtime = tmp_path / "redis-runtime.conf"
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_text(
        (ROOT / "deploy/redis-entrypoint.sh").read_text().replace(
            "/dev/shm/redis-runtime.conf", str(runtime)
        )
    )
    commands = {
        "id": f"#!/bin/sh\necho {'0' if helper else '1000'}\n",
        "find": "#!/bin/sh\nexit 0\n",
        "chown": "#!/bin/sh\nexit 0\n",
        "redis-server": (
            f"#!{sys.executable}\nimport json, sys\n"
            "print(json.dumps(sys.argv[1:]))\n"
        ),
    }
    if helper in {"setpriv", "gosu"}:
        expected = (
            ["--reuid", "redis", "--regid", "redis", "--clear-groups"]
            if helper == "setpriv" else ["redis"]
        )
        commands[helper] = (
            f"#!{sys.executable}\nimport json, os, pathlib, sys\n"
            f"expected = {expected!r}\n"
            "assert sys.argv[1:1+len(expected)] == expected\n"
            "pathlib.Path(os.environ['HELPER_TRACE']).write_text(json.dumps(expected))\n"
            "argv = sys.argv[1+len(expected):]\n"
            "os.execvp(argv[0], argv)\n"
        )
    for name, script in commands.items():
        command = tmp_path / name
        command.write_text(script)
        command.chmod(0o700)
    password = "test-only-not-a-real-secret"
    # Do not let the test host's setpriv/gosu leak into the image simulation.
    (tmp_path / "chmod").symlink_to("/bin/chmod")
    trace = tmp_path / "helper.json"
    env = {**os.environ, "REDIS_PASSWORD": password, "HELPER_TRACE": str(trace)}
    env["PATH"] = str(tmp_path)
    result = subprocess.run(
        ["/bin/sh", str(entrypoint)], env=env, capture_output=True, text=True
    )
    if helper == "missing":
        assert result.returncode != 0
        assert "refusing to run Redis as root" in result.stderr
        assert not result.stdout
        return
    assert result.returncode == 0, result.stderr
    assert trace.exists() == (helper is not None)
    argv = json.loads(result.stdout)
    assert argv == ["/usr/local/etc/redis/redis.conf", "--include", str(runtime)]
    assert password not in result.stdout
    assert runtime.read_text() == f"requirepass {password}\n"
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o400


def test_real_redis_smoke_is_release_blocking_and_matches_production_image():
    workflow = yaml.safe_load((ROOT / ".github/workflows/production.yml").read_text())
    jobs = workflow["jobs"]
    assert "deploy-config" in jobs["release-gate"]["needs"]
    step = next(
        s for s in jobs["deploy-config"]["steps"]
        if s.get("name") == "Validate Redis production entrypoint"
    )
    assert not step.get("continue-on-error")
    assert not step.get("if")
    image = yaml.safe_load((ROOT / "docker-compose.prod.secure.yml").read_text())[
        "services"
    ]["redis"]["image"]
    assert shlex.split(step["run"]) == ["sh", "deploy/smoke-redis-entrypoint.sh", image]
