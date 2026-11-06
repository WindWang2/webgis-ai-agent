"""Actual child exits and signals must terminate the combined container."""
from __future__ import annotations

import json
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


def _start(commands: list[list[str]], grace: float = 0.3) -> subprocess.Popen:
    code = (
        'import json; from app.lib.runtime.service_supervisor import supervise; '
        f'raise SystemExit(supervise(json.loads({json.dumps(json.dumps(commands))}), grace_seconds={grace}))'
    )
    return subprocess.Popen([sys.executable, '-c', code], cwd=REPO)


def _healthy(path: Path, *, ignore_term: bool = False) -> list[str]:
    handler = 'signal.SIG_IGN' if ignore_term else f'lambda *args: (Path({str(path.with_suffix(".stopped"))!r}).write_text("term"), sys.exit(0))'
    return [sys.executable, '-c',
            'import signal,sys,time; from pathlib import Path; '
            f'signal.signal(signal.SIGTERM, {handler}); '
            f'Path({str(path)!r}).write_text("ready"); time.sleep(60)']


def _wait_ready(path: Path) -> None:
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), 'child did not become ready'


@pytest.mark.parametrize('status', [0, 7])
def test_child_exit_stops_surviving_sibling(tmp_path: Path, status: int) -> None:
    ready = tmp_path / 'healthy'
    failing = [sys.executable, '-c',
               'import time; from pathlib import Path; '
               f'p=Path({str(ready)!r}); '
               'exec("while not p.exists(): time.sleep(.01)"); '
               f'raise SystemExit({status})']
    parent = _start([_healthy(ready), failing])
    try:
        assert parent.wait(timeout=8) == (status or 1)
        assert ready.with_suffix('.stopped').read_text() == 'term'
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()


def test_sigterm_forwards_and_bounds_uncooperative_child(tmp_path: Path) -> None:
    first, second = tmp_path / 'one', tmp_path / 'two'
    parent = _start([_healthy(first), _healthy(second, ignore_term=True)])
    try:
        _wait_ready(first)
        _wait_ready(second)
        started = time.monotonic()
        parent.send_signal(signal.SIGTERM)
        assert parent.wait(timeout=5) == 128 + signal.SIGTERM
        assert time.monotonic() - started < 3
        assert first.with_suffix('.stopped').read_text() == 'term'
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()


def test_all_combined_deployments_use_supervisor() -> None:
    for name in ('Dockerfile', 'Dockerfile.prod'):
        assert 'CMD ["python", "-m", "app.lib.runtime.service_supervisor"]' in (REPO / name).read_text()
    resources = list(yaml.safe_load_all((REPO / 'deploy/k8s/02-api-deployment.yaml').read_text()))
    deployment = next(row for row in resources if row.get('kind') == 'Deployment')
    container = deployment['spec']['template']['spec']['containers'][0]
    assert container['command'] == ['python', '-m', 'app.lib.runtime.service_supervisor']


@pytest.mark.parametrize('digest', ['master', 'latest', 'a' * 40, 'sha256:' + '0' * 64])
def test_image_pin_refuses_mutable_or_placeholder_before_registry(digest: str) -> None:
    result = subprocess.run(['bash', str(REPO / 'deploy/k8s/pin-image.sh'), digest], capture_output=True, text=True)
    assert result.returncode == 1
    assert 'mutable tags are not accepted' in result.stderr


def test_image_pin_verifies_digest_before_manifest_mutation(tmp_path: Path) -> None:
    script = tmp_path / 'pin-image.sh'
    script.write_text((REPO / 'deploy/k8s/pin-image.sh').read_text())
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    calls = tmp_path / 'calls'
    for name, body in [('docker', 'exit "${REGISTRY_STATUS:-0}"'), ('kustomize', 'exit 0')]:
        tool = bin_dir / name
        tool.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "$CALLS"\n{body}\n')
        tool.chmod(0o755)
    import os
    env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', CALLS=str(calls))
    digest = 'sha256:' + '1' * 64
    failed = subprocess.run(['bash', str(script), digest], env=dict(env, REGISTRY_STATUS='9'), capture_output=True)
    assert failed.returncode == 9
    assert 'kustomize' not in calls.read_text()
    calls.unlink()
    result = subprocess.run(['bash', str(script), digest], env=env, capture_output=True)
    assert result.returncode == 0
    recorded = calls.read_text().splitlines()
    assert recorded[0] == f'docker manifest inspect ghcr.io/windwang2/webgis-ai-agent@{digest}'
    assert recorded[1] == f'kustomize edit set image ghcr.io/windwang2/webgis-ai-agent=ghcr.io/windwang2/webgis-ai-agent@{digest}'


def test_unconfigured_kubernetes_images_cannot_run_a_floating_release() -> None:
    images = []
    for name in ('02-api-deployment.yaml', '03-celery-deployment.yaml'):
        for resource in yaml.safe_load_all((REPO / 'deploy/k8s' / name).read_text()):
            if resource.get('kind') != 'Deployment':
                continue
            pod = resource['spec']['template']['spec']
            images.extend(container['image'] for key in ('containers', 'initContainers') for container in pod.get(key, []))
    assert len(images) == 4
    assert all(image.endswith('@sha256:' + '0' * 64) for image in images)
