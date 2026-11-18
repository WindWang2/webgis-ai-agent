"""Real storage/caller regressions for the service -> API boundary extraction."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import textwrap
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.fixture
def storage(tmp_path, monkeypatch):
    from app.services import export_paths, export_storage

    monkeypatch.setattr(export_paths, "exports_root", lambda: tmp_path)
    export_storage.EXPORT_OWNERS.clear()
    yield export_storage
    export_storage.EXPORT_OWNERS.clear()


def test_storage_roundtrip_and_restart_sidecar_recovery(storage, tmp_path):
    storage.persist_export_file("one.png", b"complete-image", ".png")
    storage.set_export_owner("one.png", "session:owner")
    assert (tmp_path / "one.png").read_bytes() == b"complete-image"
    assert (tmp_path / "one.png.owner").read_text() == "session:owner"
    storage.EXPORT_OWNERS.clear()
    assert storage.get_export_owner("one.png") == "session:owner"
    assert storage.get_export_owner("unknown.png") is None
    assert not list(tmp_path.glob("*.tmp"))


def test_route_and_service_use_same_cache_and_private_wrappers(storage):
    from app.api.routes import map as route

    assert route._EXPORT_OWNERS is storage.EXPORT_OWNERS
    storage.set_export_owner("producer.png", "alice")
    assert route._get_export_owner("producer.png") == "alice"
    route._set_export_owner("producer.png", "bob")
    assert storage.get_export_owner("producer.png") == "bob"
    storage.set_export_owner("producer.png", "carol")
    assert route._get_export_owner("producer.png") == "carol"
    route._set_export_owner("route.png", "bob")
    assert storage.get_export_owner("route.png") == "bob"
    route._persist_export_file("route.png", b"route-bytes", ".png")
    assert (Path(route._exports_dir_str()) / "route.png").read_bytes() == b"route-bytes"


def test_bounded_cache_eviction_keeps_durable_ownership(storage, tmp_path):
    from collections import OrderedDict

    cache = OrderedDict()
    for i in range(3):
        storage.set_export_owner(f"{i}.png", f"owner-{i}", owners=cache, max_entries=2)
    assert list(cache) == ["1.png", "2.png"]
    assert storage.get_export_owner("0.png", owners=cache, max_entries=2) == "owner-0"
    assert list(cache) == ["2.png", "0.png"]
    assert (tmp_path / "0.png.owner").read_text() == "owner-0"


def test_owner_replace_failure_is_atomic_and_not_cached(storage, tmp_path, monkeypatch):
    storage.set_export_owner("one.png", "alice")

    def fail_replace(*args):
        raise OSError("receipt disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail_replace)
        with pytest.raises(OSError, match="receipt disk failure"):
            storage.set_export_owner("one.png", "bob")
    assert (tmp_path / "one.png.owner").read_text() == "alice"
    assert storage.get_export_owner("one.png") == "alice"
    assert not list(tmp_path.glob(".*.tmp"))


def test_byte_replace_failure_preserves_existing_artifact(storage, tmp_path, monkeypatch):
    storage.persist_export_file("one.png", b"old-complete", ".png")

    def fail_replace(*args):
        raise OSError("artifact disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail_replace)
        with pytest.raises(OSError, match="artifact disk failure"):
            storage.persist_export_file("one.png", b"new-complete", ".png")
    assert (tmp_path / "one.png").read_bytes() == b"old-complete"


def test_call_time_root_and_concurrent_owner_receipts(storage, tmp_path, monkeypatch):
    from app.services import export_paths

    def publish(i):
        storage.persist_export_file(f"{i}.png", str(i).encode(), ".png")
        storage.set_export_owner(f"{i}.png", f"owner-{i}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(publish, range(16)))
    storage.EXPORT_OWNERS.clear()
    for i in range(16):
        assert storage.get_export_owner(f"{i}.png") == f"owner-{i}"
        assert (tmp_path / f"{i}.png").read_bytes() == str(i).encode()
    second = tmp_path / "second-root"
    monkeypatch.setattr(export_paths, "exports_root", lambda: second)
    storage.persist_export_file("new.png", b"new-root", ".png")
    storage.set_export_owner("new.png", "new-owner")
    assert (second / "new.png").read_bytes() == b"new-root"
    assert (second / "new.png.owner").read_text() == "new-owner"
    assert not (tmp_path / "new.png").exists()


@pytest.mark.asyncio
async def test_signed_anonymous_download_and_tamper_rejection(storage):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.api.routes import map as route
    from app.services.temporal_media.store import publish_bytes

    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    url = await asyncio.to_thread(publish_bytes, "frame.png", b"actual-frame", ".png", "session:owner")
    parsed = urlsplit(url)
    assert set(parse_qs(parsed.query)) == {"exp", "sig"}
    storage.EXPORT_OWNERS.clear()  # Cold worker must recover the persisted receipt.
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get(url)
        assert response.status_code == 200
        assert response.content == b"actual-frame"
        assert (await client.get(parsed.path)).status_code == 401
        bad = url.replace("sig=", "sig=tampered")
        assert (await client.get(bad)).status_code == 401
        storage.persist_export_file("other.png", b"other-frame", ".png")
        storage.set_export_owner("other.png", "session:other")
        assert (await client.get(url.replace("frame.png", "other.png"))).status_code == 401
        (Path(route._exports_dir_str()) / "frame.png.owner").unlink()
        storage.EXPORT_OWNERS.clear()
        assert (await client.get(url)).status_code == 404


def test_three_real_producers_publish_without_importing_api(tmp_path):
    """Fresh interpreter rejects API imports and performs actual file/receipt I/O."""
    code = textwrap.dedent('''
        import asyncio, importlib.abc, json, sys
        from pathlib import Path
        from urllib.parse import urlsplit, parse_qs
        class RejectApi(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "app.api" or fullname.startswith("app.api."):
                    raise AssertionError("service imported API: " + fullname)
        sys.meta_path.insert(0, RejectApi())
        from app.core.config import settings
        settings.DATA_DIR = sys.argv[1]
        from app.core.signing import verify_signature
        from app.services.mixed_map_export import persist_mixed_export
        from app.services.raster_authoring.delivery import persist_snapshot_artifact
        from app.services.temporal_media.store import publish_bytes
        from app.services.export_paths import exports_root
        async def main():
            mixed = await persist_mixed_export("s", {"extension": ".svg", "bytes": b"<svg/>", "media_type": "image/svg+xml", "manifest": {"format": "svg"}}, principal="session:s", owner_token="unchanged-context")
            raster = await persist_snapshot_artifact("s", {"extension": ".png", "bytes": b"image", "media_type": "image/png", "sidecar": {"layers": [{"style_id": "style-1", "statement": "frozen"}], "limits": {}}, "frozen_revision": 7, "frozen": {"omitted_layers": []}, "embedded": True}, principal="session:s")
            temporal = publish_bytes("frame.png", b"frame", ".png", "session:s")
            urls = [mixed["download_url"], mixed["manifest_url"], raster["download_url"], temporal]
            for url in urls:
                parsed = urlsplit(url); query = parse_qs(parsed.query)
                assert verify_signature(parsed.path, query["exp"][0], query["sig"][0])
                filename = parsed.path.rsplit("/", 1)[1]
                assert (exports_root() / filename).exists()
                assert (exports_root() / (filename + ".owner")).read_text() == "session:s"
            assert (exports_root() / mixed["filename"]).read_bytes() == b"<svg/>"
            assert json.loads((exports_root() / (mixed["filename"] + ".manifest.json")).read_text())["filename"] == mixed["filename"]
            assert raster["frozen_revision"] == 7
            assert raster["expression_summary"] == "frozen"
            assert (exports_root() / (raster["filename"] + ".sidecar.json")).exists()
            print(json.dumps({"published": len(urls), "api_imported": any(k == "app.api" or k.startswith("app.api.") for k in sys.modules)}))
        asyncio.run(main())
    ''')
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"published": 4, "api_imported": False}
