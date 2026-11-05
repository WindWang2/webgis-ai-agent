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


@pytest.mark.parametrize("root_user", [False, True])
def test_entrypoint_passes_runtime_config_as_explicit_include(tmp_path, root_user):
    """Capture the actual exec argv and config mode, including the gosu path."""
    runtime = tmp_path / "redis-runtime.conf"
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_text(
        (ROOT / "deploy/redis-entrypoint.sh").read_text().replace(
            "/dev/shm/redis-runtime.conf", str(runtime)
        )
    )
    commands = {
        "id": f"#!/bin/sh\necho {'0' if root_user else '1000'}\n",
        "find": "#!/bin/sh\nexit 0\n",
        "chown": "#!/bin/sh\nexit 0\n",
        "gosu": '#!/bin/sh\nshift\nexec "$@"\n',
        "redis-server": (
            f"#!{sys.executable}\nimport json, sys\n"
            "print(json.dumps(sys.argv[1:]))\n"
        ),
    }
    for name, script in commands.items():
        command = tmp_path / name
        command.write_text(script)
        command.chmod(0o700)
    password = "test-only-not-a-real-secret"
    env = {**os.environ, "REDIS_PASSWORD": password}
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    result = subprocess.run(
        ["sh", str(entrypoint)], env=env, capture_output=True, text=True, check=True
    )
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
