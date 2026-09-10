"""Marketplace 只读 HTTP API（ADR-0119 / Wave 5）。

钉死：未配置 registry → 404（不伪装成空目录）；search 分页；download
流式 + digest 头；revoked 包 download → 410；写端点不存在。
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.extensions_platform.marketplace.service import PublishPolicy, RegistryService
from app.extensions_platform.marketplace.store import RegistryStore
from app.extensions_platform.signing import generate_signing_keypair, sign_pack_asymmetric
from app.extensions_platform.trust_store import TrustStore


@pytest.fixture()
def marketplace_env(tmp_path, monkeypatch):
    """配置好 registry + trust store 的环境（指向 tmp）。"""
    keys = tmp_path / "keys"
    _, pub = generate_signing_keypair(keys, "acme-2026")
    ts_path = tmp_path / "trust_store.json"
    ts_path.write_text(
        json.dumps(
            {
                "publishers": {
                    "acme": {
                        "keys": {
                            "acme-2026": {
                                "public_key_pem": pub.read_text(),
                                "state": "active",
                            }
                        }
                    }
                },
                "revoked": {"key_ids": [], "fingerprints": [], "packages": []},
            }
        )
    )
    reg_dir = tmp_path / "reg"
    monkeypatch.setenv("EXTENSION_REGISTRY_DIR", str(reg_dir))
    monkeypatch.setenv("EXTENSION_TRUST_STORE_PATH", str(ts_path))
    # Settings 缓存：直接 patch 属性（TestClient 起 app 读取同一 settings 单例）。
    from app.core.config import settings

    monkeypatch.setattr(settings, "EXTENSION_REGISTRY_DIR", str(reg_dir), raising=False)
    monkeypatch.setattr(settings, "EXTENSION_TRUST_STORE_PATH", str(ts_path), raising=False)
    return {
        "trust_store_path": ts_path,
        "reg_dir": reg_dir,
        "keys": keys,
        "pub": pub,
    }


def _publish_demo_pack(env: dict, ext_id: str = "acme.demo", version: str = "1.0.0") -> None:
    ns, name = ext_id.split(".")
    work = Path(env["reg_dir"]).parent / "work" / f"{ext_id.replace('.', '_')}-{version}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": ext_id,
                "name": name,
                "namespace": ns,
                "version": version,
                "entry_point": "main",
            }
        )
    )
    (work / "main.py").write_text("def activate(ctx):\n    return None\n")
    sign_pack_asymmetric(work, "acme", "acme-2026", env["keys"] / "acme-2026.private.pem")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(work.rglob("*")):
            if path.is_file():
                tar.add(str(path), arcname=str(path.relative_to(work)))
    ts = TrustStore.load(Path(env["trust_store_path"]))
    service = RegistryService(
        RegistryStore(Path(env["reg_dir"])),
        trust_store=ts,
        policy=PublishPolicy(allowed_publishers=frozenset({"acme"})),
    )
    service.publish(buf.getvalue())


def _client() -> TestClient:
    from app.core.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: {"user_id": "tester"}
    return TestClient(app)


def test_marketplace_404_when_not_configured(monkeypatch):
    from app.core.config import settings
    import app.extensions_platform.marketplace.bootstrap as bootstrap

    monkeypatch.setattr(settings, "EXTENSION_REGISTRY_DIR", "", raising=False)
    monkeypatch.setattr(bootstrap, "_SERVICE", None)
    monkeypatch.setattr(bootstrap, "_CONFIGURED", None)
    client = _client()
    resp = client.get("/api/v1/extensions/marketplace/packages")
    assert resp.status_code == 404
    assert "EXTENSION_REGISTRY_DIR" in resp.json()["detail"]


def test_marketplace_search_and_detail(marketplace_env):
    _publish_demo_pack(marketplace_env)
    _publish_demo_pack(marketplace_env, "acme.other", "0.1.0")
    client = _client()
    resp = client.get("/api/v1/extensions/marketplace/packages", params={"q": "demo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "acme.demo"
    detail = client.get("/api/v1/extensions/marketplace/packages/acme.demo").json()
    assert detail["latest_version"] == "1.0.0"
    missing = client.get("/api/v1/extensions/marketplace/packages/acme.nope")
    assert missing.status_code == 404


def test_marketplace_download_stream_with_digest(marketplace_env):
    _publish_demo_pack(marketplace_env)
    client = _client()
    resp = client.get(
        "/api/v1/extensions/marketplace/packages/acme.demo/versions/1.0.0/download"
    )
    assert resp.status_code == 200
    assert resp.headers["x-content-digest"]
    assert len(resp.content) > 0
    # digest 形状防线：畸形版本 → 404，畸形 digest 形状的记录不可能入库。


def test_marketplace_download_revoked_is_410(marketplace_env):
    _publish_demo_pack(marketplace_env)
    ts = TrustStore.load(Path(marketplace_env["trust_store_path"]))
    service = RegistryService(RegistryStore(Path(marketplace_env["reg_dir"])), trust_store=ts)
    service.revoke("acme.demo")
    client = _client()
    resp = client.get(
        "/api/v1/extensions/marketplace/packages/acme.demo/versions/1.0.0/download"
    )
    assert resp.status_code == 410


def test_marketplace_has_no_write_endpoints(marketplace_env):
    """写路径只存在于 CLI：HTTP publish/deprecate/revoke 全部 404/405。"""
    client = _client()
    for method, path in (
        ("post", "/api/v1/extensions/marketplace/packages"),
        ("delete", "/api/v1/extensions/marketplace/packages/acme.demo"),
        ("post", "/api/v1/extensions/marketplace/packages/acme.demo/versions/1.0.0/revoke"),
    ):
        resp = getattr(client, method)(path)
        assert resp.status_code in (404, 405), (method, path, resp.status_code)
