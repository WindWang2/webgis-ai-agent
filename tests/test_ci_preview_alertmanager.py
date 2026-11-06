"""Preview must render Alertmanager's receiver URLs before starting the stack.

Alertmanager v0.28.0 validates URLs when loading its configuration. The
production template's bare ``__ALERT_WEBHOOK_URL__`` has no HTTP(S) scheme,
so mounting it unchanged makes the preview container restart continuously.
These tests exercise the renderer without contacting any webhook endpoint.
"""

import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "deploy" / "alertmanager.yml"
RENDERER = REPO_ROOT / "deploy" / "render-alertmanager-config.sh"


def test_preview_renders_alertmanager_before_starting_compose():
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "production.yml").read_text(
            encoding="utf-8"
        )
    )
    run = next(
        step["run"]
        for step in workflow["jobs"]["preview"]["steps"]
        if step.get("name") == "Start Preview Stack"
    )
    commands = [line.strip() for line in run.splitlines()]
    # Clear any ambient webhook explicitly: CI previews must never send their
    # synthetic alerts to production recipients. Render separately before
    # replacing the disposable checkout's bind-mount source.
    render = (
        "ALERT_WEBHOOK_URL='' sh deploy/render-alertmanager-config.sh "
        "/tmp/preview-alertmanager.yml"
    )
    install = "cp /tmp/preview-alertmanager.yml deploy/alertmanager.yml"
    start = (
        "docker compose --env-file .env.Priv "
        "-f docker-compose.prod.secure.yml up -d"
    )
    assert render in commands
    assert install in commands
    assert start in commands
    assert commands.index(render) < commands.index(install) < commands.index(start)


@pytest.mark.parametrize("webhook_url", [None, ""])
def test_alertmanager_renderer_has_a_valid_nonproduction_fallback(
    tmp_path, webhook_url
):
    """Missing preview secrets must not leave an invalid URL in either receiver."""
    env = dict(os.environ)
    env.pop("ALERT_WEBHOOK_URL", None)
    if webhook_url is not None:
        env["ALERT_WEBHOOK_URL"] = webhook_url
    before = TEMPLATE.read_bytes()
    output = tmp_path / "alertmanager.yml"

    subprocess.run(
        ["sh", str(RENDERER), str(output)],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    rendered = output.read_text(encoding="utf-8")
    assert "__ALERT_WEBHOOK_URL__" not in rendered
    config = yaml.safe_load(rendered)
    assert {receiver["name"] for receiver in config["receivers"]} == {
        "webgis-ops", "webgis-ops-critical"
    }
    for receiver in config["receivers"]:
        for webhook in receiver["webhook_configs"]:
            assert webhook["url"] == "http://alertmanager-webhook.invalid/"
            parsed = urlsplit(webhook["url"])
            assert parsed.scheme in {"http", "https"}
            assert parsed.hostname
            assert webhook["send_resolved"] is True
    assert TEMPLATE.read_bytes() == before, "Rendering must preserve the source template"


def test_alertmanager_renderer_preserves_configured_webhook_url(tmp_path):
    """Keep production rendering intact, including URL query metacharacters."""
    webhook_url = "https://alerts.example.invalid/receive?team=webgis&token=a%2Fb"
    output = tmp_path / "alertmanager.yml"
    result = subprocess.run(
        ["sh", str(RENDERER), str(output)],
        cwd=REPO_ROOT,
        env={**os.environ, "ALERT_WEBHOOK_URL": webhook_url},
        check=True,
        capture_output=True,
        text=True,
    )

    config = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert [
        webhook["url"]
        for receiver in config["receivers"]
        for webhook in receiver["webhook_configs"]
    ] == [webhook_url, webhook_url]
    assert webhook_url not in result.stdout + result.stderr
