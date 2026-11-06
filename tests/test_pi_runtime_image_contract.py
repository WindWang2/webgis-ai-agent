"""Deployment targets carry the locked Pi extension loader and a Node runtime."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def _stage(name: str, text: str) -> str:
    parts = re.split(r"(?im)^FROM ", text)
    return next(part for part in parts if re.search(rf"(?im)^.* AS {re.escape(name)}$", part))


def test_compose_backend_target_has_node_and_external_extension_loader():
    stage = _stage("backend-builder", (ROOT / "Dockerfile").read_text())
    assert "COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node" in stage
    assert "COPY --from=pi-builder /app/vendor/pi/node_modules/jiti ./vendor/pi/node_modules/jiti" in stage
    assert "packages/coding-agent/dist/bundle" in stage


def test_both_final_images_carry_jiti_alongside_the_bundle():
    for filename in ("Dockerfile", "Dockerfile.prod"):
        stage = _stage("runner", (ROOT / filename).read_text())
        assert "COPY --from=pi-builder /app/vendor/pi/node_modules/jiti ./vendor/pi/node_modules/jiti" in stage
        assert "packages/coding-agent/dist/bundle" in stage
