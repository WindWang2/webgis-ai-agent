"""Run the smoke shell end-to-end with deterministic Docker/Redis responses.

This exercises error handling and redaction, not a substitute for the pinned
image's real Redis check in the release-blocking deployment job.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(("fault", "diagnostic"), [
    ("none", None),
    ("readiness", "Redis entrypoint did not become ready"),
    ("auth", "unauthenticated PING was not rejected"),
    ("appendonly", "appendonly is not enabled"),
    ("eviction", "maxmemory-policy is not noeviction"),
    ("mode", "runtime config mode is not 0400"),
    ("owner", "runtime config is not owned by Redis user"),
    ("root_user", "Redis user has root UID"),
    ("process", "Redis process did not drop privileges"),
    ("identity_missing", "cannot read Redis process identity"),
    ("argv_empty", "Redis command line is empty"),
    ("argv_missing", "cannot read Redis command line"),
    ("argv_secret", "Redis password leaked into argv"),
])
def test_smoke_reports_named_failures_without_secrets(tmp_path, fault, diagnostic):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    status = tmp_path / "process-status"
    cmdline = tmp_path / "process-cmdline"
    password = "fixture-secret-never-log"
    if fault != "identity_missing":
        uids = "0 0 0 0" if fault == "process" else "999 999 999 999"
        status.write_text(f"Name:\tredis-server\nUid:\t{uids}\n")
    if fault != "argv_missing":
        cmdline.write_bytes(
            b"" if fault == "argv_empty" else
            password.encode() if fault == "argv_secret" else b"redis-server\x00*:6379"
        )
    script = tmp_path / "deploy" / "smoke-redis-entrypoint.sh"
    script.parent.mkdir()
    script.write_text(
        (ROOT / "deploy/smoke-redis-entrypoint.sh").read_text()
        .replace("/proc/1/status", str(status))
        .replace("/proc/1/cmdline", str(cmdline))
    )
    log = tmp_path / "docker.jsonl"
    commands = {
        "openssl": f"print({password!r})",
        "sleep": "pass",
        "id": "print('0' if os.environ['FAULT'] == 'root_user' else '999')",
        "stat": (
            "fault = os.environ['FAULT']\n"
            "print(('644' if fault == 'mode' else '400') if sys.argv[2] == '%a' "
            "else ('0' if fault == 'owner' else '999'))"
        ),
        "redis-cli": (
            "fault = os.environ['FAULT']\n"
            "if sys.argv[-1] == 'ping':\n"
            "    auth = bool(os.environ.get('REDISCLI_AUTH'))\n"
            "    print('PONG' if (auth and fault != 'readiness') or fault == 'auth' "
            "else 'NOAUTH Authentication required.')\n"
            "elif sys.argv[-1] == 'appendonly':\n"
            "    print('appendonly\\n' + ('no' if fault == 'appendonly' else 'yes'))\n"
            "else:\n"
            "    print('maxmemory-policy\\n' + ('allkeys-lru' if fault == 'eviction' else 'noeviction'))"
        ),
        "docker": (
            "with open(os.environ['DOCKER_LOG'], 'a') as log:\n"
            "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "action = sys.argv[1]\n"
            "if action == 'run':\n"
            "    print('test-redis-container')\n"
            "elif action == 'exec':\n"
            "    env = dict(os.environ)\n"
            "    env.pop('REDISCLI_AUTH', None)\n"
            "    os.execve('/bin/sh', ['sh', *sys.argv[4:]], env)\n"
            "elif action == 'logs':\n"
            "    print('Redis fixture logs ' + os.environ['REDIS_PASSWORD'])\n"
            "else:\n"
            "    assert sys.argv[1:] == ['rm', '-fv', 'test-redis-container']"
        ),
    }
    for name, body in commands.items():
        command = bindir / name
        command.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n{body}\n"
        )
        command.chmod(0o700)
    for name in ("dirname", "seq", "grep", "tail", "awk", "tr"):
        executable = shutil.which(name)
        assert executable is not None
        (bindir / name).symlink_to(executable)
    result = subprocess.run(
        ["/bin/sh", str(script), "redis:test-fixture"],
        env={**os.environ, "PATH": str(bindir), "FAULT": fault, "DOCKER_LOG": str(log)},
        capture_output=True, text=True, timeout=15,
    )
    output = result.stdout + result.stderr
    assert password not in output
    if diagnostic is None:
        assert result.returncode == 0, output
        assert "Redis entrypoint smoke passed" in output
    else:
        assert result.returncode != 0
        assert diagnostic in output
        assert "smoke passed" not in output
        if fault == "readiness":
            assert "[REDACTED]" in output
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[-1] == ["rm", "-fv", "test-redis-container"]
