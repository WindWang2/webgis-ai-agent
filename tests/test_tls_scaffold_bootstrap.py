"""Exercise actual TLS generation without Docker, a network, or OpenSSL CLI."""

from datetime import UTC, datetime
import os
from pathlib import Path
import stat
import subprocess
import sys

from cryptography import x509
from cryptography.hazmat.primitives import serialization
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/generate_tls_scaffold.py"


def _generate(path):
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        env={**os.environ, "TLS_DIR": str(path), "PATH": ""},
        capture_output=True, text=True, timeout=30,
    )


def test_real_certificate_generation_is_private_and_idempotent(tmp_path):
    directory = tmp_path / "tls"
    first = _generate(directory)
    assert first.returncode == 0, first.stderr
    key_path, cert_path = directory / "server.key", directory / "server.crt"
    original = (key_path.read_bytes(), cert_path.read_bytes())
    key = serialization.load_pem_private_key(original[0], password=None)
    cert = x509.load_pem_x509_certificate(original[1])
    assert key.key_size == 2048
    assert cert.public_key().public_numbers() == key.public_key().public_numbers()
    assert cert.not_valid_before_utc <= datetime.now(UTC) < cert.not_valid_after_utc
    assert "localhost" in cert.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.DNSName)
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(cert_path.stat().st_mode) == 0o644
    assert "PRIVATE KEY" not in first.stdout + first.stderr
    second = _generate(directory)
    assert second.returncode == 0, second.stderr
    assert (key_path.read_bytes(), cert_path.read_bytes()) == original
    assert sorted(p.name for p in directory.iterdir()) == ["server.crt", "server.key"]


@pytest.mark.parametrize("existing", ["server.key", "server.crt"])
def test_incomplete_pair_fails_without_overwriting_existing_file(tmp_path, existing):
    supplied = tmp_path / existing
    supplied.write_text("operator supplied partial pair")
    result = _generate(tmp_path)
    assert result.returncode != 0
    assert "TLS scaffold is incomplete" in result.stderr
    assert supplied.read_text() == "operator supplied partial pair"
    assert len(list(tmp_path.iterdir())) == 1


def test_mismatched_pair_fails_without_replacing_material(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    assert _generate(first).returncode == 0
    assert _generate(second).returncode == 0
    (first / "server.crt").write_bytes((second / "server.crt").read_bytes())
    before = [(first / name).read_bytes() for name in ("server.key", "server.crt")]
    result = _generate(first)
    assert result.returncode != 0
    assert "key and certificate do not match" in result.stderr
    assert [(first / name).read_bytes() for name in ("server.key", "server.crt")] == before


def test_tls_initializer_uses_existing_offline_image_and_private_volume():
    compose = yaml.safe_load((ROOT / "docker-compose.prod.secure.yml").read_text())
    init = compose["services"]["tls-init"]
    assert init["image"] == compose["services"]["api"]["image"]
    assert init["command"] == []
    assert init["network_mode"] == "none"
    assert init["healthcheck"] == {"disable": True}
    assert "user" not in init  # Keep Dockerfile.prod's non-root appuser.
    assert init["volumes"] == ["nginx_tls_secure:/app/data"]
    assert init["cap_drop"] == ["ALL"]
    assert init["security_opt"] == ["no-new-privileges:true"]
    assert "nginx_tls_secure" in compose["volumes"]
    dockerfile = (ROOT / "Dockerfile.prod").read_text()
    assert init["entrypoint"] == ["python3", "/run/generate-tls-scaffold.py"]
    assert init["configs"] == [{
        "source": "webgis_tls_generator", "target": "/run/generate-tls-scaffold.py",
    }]
    assert compose["configs"]["webgis_tls_generator"] == {
        "file": "./deploy/generate_tls_scaffold.py",
    }
    assert "USER appuser" in dockerfile
    assert "mkdir -p /app/uploads /app/data" in dockerfile


@pytest.mark.parametrize("name", ["server.key", "server.crt"])
@pytest.mark.parametrize("dangling", [True, False])
def test_symlinks_fail_without_following_or_replacing_them(tmp_path, name, dangling):
    target = tmp_path / "operator-material"
    if not dangling:
        target.write_text("preserve operator material")
    link = tmp_path / name
    link.symlink_to(target)
    result = _generate(tmp_path)
    assert result.returncode != 0
    assert "must not use symbolic links" in result.stderr
    assert link.is_symlink()
    assert not (tmp_path / ({"server.key", "server.crt"} - {name}).pop()).exists()
    if not dangling:
        assert target.read_text() == "preserve operator material"


def test_tls_helper_is_transported_with_compose_on_deploy_and_rollback():
    workflow = yaml.safe_load((ROOT / ".github/workflows/production.yml").read_text())
    deploy = next(step["run"] for step in workflow["jobs"]["deploy-prod"]["steps"]
                  if step.get("name") == "Deploy to Server via SSH")
    rollback = next(step["run"] for step in workflow["jobs"]["rollback"]["steps"]
                    if step.get("name") == "Deploy Rollback via SSH")
    assert "scp deploy/redis.conf deploy/redis-entrypoint.sh deploy/generate_tls_scaffold.py " in deploy
    # Source fallback may predate both the helper and the matching Compose service.
    assert "for f in deploy/redis.conf deploy/redis-entrypoint.sh deploy/generate_tls_scaffold.py; do" in rollback
    assert '[ -f "$f" ] && scp "$f"' in rollback
    for run in (deploy, rollback):
        assert "chmod 644 ~/deploy/generate_tls_scaffold.py" in run
