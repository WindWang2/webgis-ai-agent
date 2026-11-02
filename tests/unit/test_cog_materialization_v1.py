"""GOAL-02 M4：STAC→COG 远程资产受控物化 —— 真实 HTTP 字节路径离线测试。

字节面用**本地 loopback HTTP 文件服务器**（127.0.0.1 线程 http.server，
全程离线）：rasterio /vsicurl 真实走 HTTP range 请求。SSRF 门默认拦
loopback 是安全不变量（有专门 fail-closed 用例）——字节路径用例经显式
模块级 allow_private 注入（内网部署语义），绝不修改核心安全代码。
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import numpy as np
import pytest

from app.core.config import settings
from app.lib.geo_raster.cog import write_cog
from app.services.data_fabric.cog_materialization import (
    CogMaterializationError,
    discover_stac_cog_assets,
    extract_cog_assets,
    materialize_remote_cog_asset,
    preview_remote_cog,
    RC_BUDGET_EXCEEDED,
    RC_SSRF_BLOCKED,
)
from app.services.data_fabric.security import DataFabricSecurity

_SESSION = "cog-mat-sess-01"

# 4×4° 覆盖、600×600（overview 阶梯非空：600//2=300 ≥ 256），值 =
# 100 + row*_NX + col（逐点可验）。
_NX = _NY = 600
_X0, _Y0, _X1, _Y1 = 100.0, 20.0, 104.0, 24.0


def _truth(row: int, col: int) -> float:
    return float(100 + row * _NX + col)


def _build_source_cog(path: Path) -> None:
    import rasterio
    from rasterio.transform import Affine

    xres = (_X1 - _X0) / (_NX - 1)
    yres = (_Y1 - _Y0) / (_NY - 1)
    transform = Affine.translation(_X0 - xres / 2, _Y1 + yres / 2) * Affine.scale(xres, -yres)
    raw = path.parent / "source_raw.tif"
    arr = np.empty((1, _NY, _NX), dtype="float32")
    for r in range(_NY):
        arr[0, r, :] = [_truth(r, c) for c in range(_NX)]
    with rasterio.open(
        raw, "w", driver="GTiff", width=_NX, height=_NY, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=-9999.0,
    ) as dst:
        dst.write(arr)
    write_cog(str(raw), str(path))


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """静默 + Range 支持（GDAL /vsicurl 硬依赖；3.13 http.server 仍无 Range）。

    ``protocol_version = HTTP/1.1`` 开 keep-alive —— GDAL 逐 range 请求
    复用连接（否则每 range 一次 TCP 握手，窗口读耗时爆炸）。HEAD 同样
    支持并显式带 Content-Length（HTTP/1.1 keep-alive 下无长度的响应会让
    客户端等到超时 —— GDAL 的 .xml/.msk 探测正是 HEAD）。
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: ANN002 — 静默测试服务器
        pass

    def _send_range(self) -> None:
        import os

        self.close_connection = True
        self.headers["Connection"] = "close"
        range_header = self.headers.get("Range")
        full = super().translate_path(self.path)
        try:
            size = os.path.getsize(full)
        except OSError:
            super().do_GET()
            return
        if not range_header or not range_header.startswith("bytes="):
            self.send_response(200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(size))
            self.send_header("Connection", "close")
            self.end_headers()
            with open(full, "rb") as f:
                self.wfile.write(f.read())
            return
        # 仅支持单一范围（GDAL 逐 range 请求语义）
        try:
            spec = range_header[len("bytes="):]
            start_s, _, end_s = spec.partition("-")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
        except ValueError:
            self.send_response(416)
            self.send_header("Connection", "close")
            self.end_headers()
            return
        start = max(0, start)
        end = min(size - 1, end)
        if start > end:
            self.send_response(416)
            self.send_header("Connection", "close")
            self.end_headers()
            return
        length = end - start + 1
        self.send_response(206)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Connection", "close")
        self.end_headers()
        with open(full, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    do_GET = _send_range

    def do_HEAD(self):
        import os

        # keep-alive 下 GDAL/curl 对无 body 响应的连接复用会空等到超时 ——
        # 每响应显式 close（本地回环，握手成本可忽略）。
        self.close_connection = True
        full = super().translate_path(self.path)
        try:
            size = os.path.getsize(full)
        except OSError:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(size))
        self.send_header("Connection", "close")
        self.end_headers()


@pytest.fixture(scope="module")
def http_cog_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("cog-http")
    source = serve_dir / "scene-001.tif"
    _build_source_cog(source)
    handler = functools.partial(_QuietHandler, directory=str(serve_dir))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/scene-001.tif", source
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))


@pytest.fixture
def allow_private_href(monkeypatch, http_cog_server):
    """显式内网部署语义：geo_raster SSRF 门允许 loopback（仅本模块注入）。

    核心 DataFabricSecurity.validate_url 代码零改动；默认 fail-closed 由
    test_private_href_blocked_by_default 单独验证。
    """
    from app.lib.geo_raster import env as geo_env
    from app.lib.geo_raster.env import _extract_remote_target

    def _validate(uri: str) -> str:
        target = _extract_remote_target(uri)
        if target is None:
            return uri
        DataFabricSecurity.validate_url(target, allow_private=True)
        return uri

    monkeypatch.setattr(geo_env, "validate_remote_href", _validate)
    return http_cog_server[0]


class TestDiscover:
    def test_extract_cog_assets_declared_and_inferred(self):
        item = {
            "id": "scene-001",
            "assets": {
                "data": {
                    "href": "https://example.com/a.tif?sig=SECRETTOKEN",
                    "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                    "roles": ["data"],
                },
                "thumb": {"href": "https://example.com/a.png", "type": "image/png"},
                "nohref": {"type": "image/tiff"},
            },
        }
        assets = extract_cog_assets(item)
        assert [a["asset_key"] for a in assets] == ["data"]
        assert assets[0]["match"] == "declared"
        # 推断路径：无媒体类型但 .tif 后缀
        item2 = {"assets": {"grey": {"href": "https://example.com/b.tif"}}}
        assert extract_cog_assets(item2)[0]["match"] == "inferred"

    def test_discover_via_stac_fixture(self, patched_safe_sessions, ogc_fake_server):
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters import STACAdapter

        url = ogc_fake_server.add_stac_cog()
        patched_safe_sessions(ogc_fake_server)
        adapter = STACAdapter(
            ConnectionProfile(source_type="stac", endpoint_url=url, name="stac-cog")
        )
        result = _run(discover_stac_cog_assets(adapter, bbox=[100.0, 20.0, 105.0, 25.0]))
        assert result["total_items"] == 1
        item = result["items"][0]
        assert item["item_id"] == "scene-001"
        assert item["assets"][0]["asset_key"] == "data"
        assert item["assets"][0]["href"].endswith("scene-001.tif")


class TestPreview:
    def test_preview_reads_header_no_pixels(self, allow_private_href):
        info = preview_remote_cog(allow_private_href)
        assert info["width"] == _NX and info["height"] == _NY
        assert info["crs"] == "EPSG:4326"
        assert info["cog_structure_ok"] is True
        assert "SECRETTOKEN" not in info["href_redacted"]

    def test_private_href_blocked_by_default(self, http_cog_server):
        """安全不变量：默认（无 allow_private）SSRF 门拦截 loopback。"""
        with pytest.raises(CogMaterializationError) as ei:
            preview_remote_cog(http_cog_server[0])
        assert ei.value.reason_code == RC_SSRF_BLOCKED


class TestMaterialize:
    async def test_aoi_window_materializes_with_truth(self, allow_private_href):
        """AOI 物化：窗口读 → COG ref → 采样点与真值逐点一致。"""
        result = await materialize_remote_cog_asset(
            allow_private_href,
            session_id=_SESSION,
            bbox=[100.5, 20.5, 101.5, 21.5],
            bands=[1],
        )
        assert result["status"] == "success"
        assert result["ref"].startswith("ref:fabric-cog/")
        ledger = result["ledger"]
        # AOI 1°/0.01° ≈ 101×101 像元 —— 远小于整幅 401×401（窗口语义）
        assert ledger["window"][2] <= 202 and ledger["window"][3] <= 202  # AOI ≪ 整幅
        import rasterio

        with rasterio.open(result["path"]) as ds:
            assert ds.crs.to_string() == "EPSG:4326"
            # 采样点：网格中心若干坐标 → 反解行列 → 对照真值
            for (px, py) in [(100.6, 21.4), (101.0, 21.0), (101.4, 20.6)]:
                c_, r_ = ~ds.transform * (px, py)
                r, c = int(r_), int(c_)
                # 窗口原点对应整幅坐标 → 全幅真值行列
                wcol, wrow = ledger["window"][0], ledger["window"][1]
                # rasterio 元组 window 是 (start, stop) 语义 —— 单像元采样
                # 用 ((r, r+1), (c, c+1))。
                assert ds.read(1, window=((r, r + 1), (c, c + 1)))[0, 0] == pytest.approx(
                    _truth(wrow + r, wcol + c), abs=1e-4
                )

    async def test_idempotent_same_params_same_content(self, allow_private_href):
        r1 = await materialize_remote_cog_asset(
            allow_private_href, session_id=_SESSION, bbox=[100.0, 20.0, 101.0, 21.0],
        )
        r2 = await materialize_remote_cog_asset(
            allow_private_href, session_id=_SESSION, bbox=[100.0, 20.0, 101.0, 21.0],
        )
        assert r1["params_fingerprint"] == r2["params_fingerprint"]
        assert r1["content_sha256"] == r2["content_sha256"]
        # CAS 幂等：同内容 → 同 data_object_id（deduped）
        if "data_object_id" in r1 and "data_object_id" in r2:
            assert r1["data_object_id"] == r2["data_object_id"]
            assert r2.get("deduped") is True

    async def test_budget_refusal_with_hint(self, allow_private_href):
        """整幅 401×401×4B ≈ 643KB；压预算到 100KB → typed 拒绝 + 窗口事实。"""
        with pytest.raises(CogMaterializationError) as ei:
            await materialize_remote_cog_asset(
                allow_private_href, session_id=_SESSION, max_output_bytes=100 * 1024,
            )
        assert ei.value.reason_code == RC_BUDGET_EXCEEDED
        assert ei.value.details["full_raster"] == [_NX, _NY]

    async def test_remote_404_typed(self, http_cog_server, allow_private_href, monkeypatch):
        base = http_cog_server[0].rsplit("/", 1)[0]
        with pytest.raises(CogMaterializationError) as ei:
            await materialize_remote_cog_asset(
                f"{base}/missing.tif", session_id=_SESSION,
            )
        assert ei.value.reason_code in ("unreachable", "not_a_raster")

    async def test_secret_redaction_in_receipt(self, allow_private_href):
        """签名 URL 的 query（token/sig）绝不出现在回执/provenance。"""
        signed = f"{allow_private_href}?sig=SUPERSECRET&X-Goog-Algorithm=GOOG4"
        result = await materialize_remote_cog_asset(
            signed, session_id=_SESSION, bbox=[100.2, 20.2, 100.8, 20.8],
        )
        blob = repr(result)
        assert "SUPERSECRET" not in blob
        assert "X-Goog-Algorithm" not in blob
        assert result["provenance"]["source_url"].startswith("http://127.0.0.1")

    async def test_cancel_token_aborts(self, allow_private_href):
        class _Tok:
            cancelled = True
            reason = "user-cancel"

        with pytest.raises(CogMaterializationError) as ei:
            await materialize_remote_cog_asset(
                allow_private_href, session_id=_SESSION, cancel_token=_Tok(),
            )
        assert ei.value.reason_code == "cancelled"


def _run(coro):
    import asyncio

    return asyncio.run(coro)
