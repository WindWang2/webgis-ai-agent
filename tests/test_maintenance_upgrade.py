"""Run the maintenance script against a command recorder to verify fail-closed ordering."""
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("failure", ["", "stop", "backup", "migration", "still_running"])
def test_maintenance_cutover_stops_backs_up_migrates_then_starts(tmp_path, failure):
    docker = tmp_path / "docker"
    docker.write_text('''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ["UPGRADE_LOG"],"a") as out: out.write(json.dumps(args)+"\\n")
mode=os.environ["UPGRADE_FAIL"]
if "stop" in args and mode=="stop": sys.exit(1)
if "ps" in args and mode=="still_running": print("celery-worker")
if "exec" in args:
    if mode=="backup": sys.exit(1)
    sys.stdout.buffer.write(b"PGDMP-test-backup")
if "run" in args and mode=="migration": sys.exit(1)
''')
    docker.chmod(0o700)
    log = tmp_path / "calls.jsonl"
    script = Path(__file__).resolve().parents[1] / "deploy/maintenance-upgrade.sh"
    result = subprocess.run(["sh", str(script), "private.env", "stack.yml"], cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "UPGRADE_LOG": str(log), "UPGRADE_FAIL": failure},
        capture_output=True, text=True, timeout=10)
    calls = [json.loads(line)[5:] for line in log.read_text().splitlines()]
    assert calls[0] == ["stop", "--timeout", "300", "api", "celery-worker"]
    if failure:
        assert result.returncode != 0
        assert ["up", "-d"] not in calls
        if failure in {"stop", "still_running", "backup"}:
            assert not any("run" in call for call in calls)
    else:
        assert result.returncode == 0, result.stderr
        assert calls[-1] == ["up", "-d"]
        backup_call = next(i for i, call in enumerate(calls) if "exec" in call)
        migration_call = next(i for i, call in enumerate(calls) if "run" in call)
        assert backup_call < migration_call < len(calls) - 1
        assert "OWNER_TOKEN_MIGRATION_QUIESCED=1" in calls[migration_call]
        backup = next((tmp_path / "backups").iterdir())
        assert backup.read_bytes() == b"PGDMP-test-backup"
        assert backup.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", ["", "load", "backup", "migration"])
def test_production_remote_script_propagates_failure_before_pruning(tmp_path, failure):
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/production.yml").read_text()
    remote = next(block for block in re.findall(
        r"ssh \$\{\{ vars\.SSH_HOST \}\} << 'EOF'\n(.*?)^\s*EOF", workflow, re.M | re.S,
    ) if "sh deploy/maintenance-upgrade.sh" in block)
    # Adapt only the remote image's filesystem location; execute the actual
    # workflow body and maintenance helper, including their error handling.
    image = tmp_path / "webgis-image.tar"
    image.write_bytes(b"image-fixture")
    remote = remote.replace("~/webgis-image.tar", shlex.quote(str(image)))
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "maintenance-upgrade.sh").write_text((root / "deploy/maintenance-upgrade.sh").read_text())
    docker = tmp_path / "docker"
    docker.write_text('''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ["UPGRADE_LOG"],"a") as out: out.write(json.dumps(args)+"\\n")
mode=os.environ["UPGRADE_FAIL"]
if args == ["load"] and mode == "load": sys.exit(17)
if "exec" in args:
    if mode == "backup": sys.exit(18)
    sys.stdout.buffer.write(b"PGDMP-test-backup")
if "run" in args and mode == "migration": sys.exit(19)
''')
    docker.chmod(0o700)
    log = tmp_path / "remote-calls.jsonl"
    result = subprocess.run(["sh"], input=remote, cwd=tmp_path, text=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "UPGRADE_LOG": str(log), "UPGRADE_FAIL": failure},
        capture_output=True, timeout=10)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0] == ["load"]
    if failure:
        assert result.returncode != 0
        assert ["system", "prune", "-f"] not in calls
        assert not any(call[-2:] == ["up", "-d"] for call in calls)
    else:
        assert result.returncode == 0, result.stderr
        assert calls[-2][-2:] == ["up", "-d"]
        assert calls[-1] == ["system", "prune", "-f"]
