"""Wave 8/9 OGC/STAC 集成加固测试（不触网）。

覆盖三块：
1. WMS/WMTS describe() 的 CRS 诚实性——CRS/bbox 只来自 capabilities 声明，
   绝不伪造 EPSG:3857 / 全球 extent（项目红线「never silently assume CRS」）。
2. geo_raster /vsicurl 远端 href 的 SSRF 门禁（复用 data_fabric.security）。
3. STAC 客户端：基地址可配置 + asset href 的 SSRF 校验。

所有用例使用字面 IP / 打桩 DNS / FakeSession，零真实网络。
"""
from __future__ import annotations

import socket

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.adapters.wms_wmts_adapter import (
    WMSWMTSAdapter,
)
from app.services.data_fabric.security import DataFabricSecurityError
from app.lib.geo_raster.env import validate_remote_href
from app.lib.geo_raster.reader import RasterReader

# 公网字面 IP（example.com 的历史地址）：字面 IP 不触发 DNS，测试保持离线。
PUBLIC_IPV4 = "93.184.216.34"


# ── 测试替身：最小 FakeSession/FakeResponse（兼容 bounded_get 协议）────────


class FakeResponse:
    def __init__(self, content: bytes = b"", status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.headers = {"Content-Type": "application/vnd.ogc.wms_xml"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int = 65536):
        body = self.content
        for i in range(0, len(body), chunk_size):
            yield body[i: i + chunk_size]

    def close(self):
        pass


class FakeSession:
    """按 REQUEST 参数路由的假 session（记录调用，不发网络请求）。"""

    def __init__(self, response=None, error: Exception | None = None):
        self.calls: list[dict] = []
        self._response = response
        self._error = error

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "params": dict(params or {})})
        if self._error is not None:
            raise self._error
        return self._response


def _wms_adapter(caps_xml: bytes | None, *, error: Exception | None = None,
                 source_type: str = "wms") -> WMSWMTSAdapter:
    profile = ConnectionProfile(
        source_type=source_type,
        url=f"http://{PUBLIC_IPV4}/geoserver/wms",
        name="t",
    )
    adapter = WMSWMTSAdapter(profile)
    adapter.session = FakeSession(
        None if caps_xml is None else FakeResponse(caps_xml), error=error
    )
    return adapter


# ── fixtures：inline capabilities XML ──────────────────────────────────────

WMS_130_CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities xmlns="http://www.opengis.net/wms" version="1.3.0">
  <Capability>
    <Layer>
      <Name>root</Name>
      <Title>Root</Title>
      <CRS>EPSG:4326</CRS>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>-15.0</westBoundLongitude>
        <eastBoundLongitude>35.0</eastBoundLongitude>
        <southBoundLatitude>30.0</southBoundLatitude>
        <northBoundLatitude>65.0</northBoundLatitude>
      </EX_GeographicBoundingBox>
      <Layer queryable="1">
        <Name>roads</Name>
        <Title>Roads</Title>
        <CRS>EPSG:3857</CRS>
        <CRS>urn:ogc:def:crs:EPSG::4326</CRS>
        <EX_GeographicBoundingBox>
          <westBoundLongitude>100.5</westBoundLongitude>
          <eastBoundLongitude>120.5</eastBoundLongitude>
          <southBoundLatitude>20.25</southBoundLatitude>
          <northBoundLatitude>40.75</northBoundLatitude>
        </EX_GeographicBoundingBox>
      </Layer>
      <Layer queryable="1">
        <Name>terrain</Name>
        <Title>Terrain (inherits CRS/bbox from parent Layer)</Title>
      </Layer>
    </Layer>
  </Capability>
</WMS_Capabilities>"""

WMS_111_CAPS = b"""<?xml version="1.0"?>
<WMT_MS_Capabilities version="1.1.1">
  <Capability>
    <Layer>
      <Name>top</Name>
      <SRS>EPSG:4326 EPSG:3857</SRS>
      <LatLonBoundingBox minx="-10.5" miny="20.0" maxx="30.25" maxy="55.5"/>
      <Layer>
        <Name>parcels</Name>
        <Title>Parcels</Title>
      </Layer>
    </Layer>
  </Capability>
</WMT_MS_Capabilities>"""

# 无 CRS / 无 bbox 的 WMTS 风格 capabilities：诚实空值，不伪造。
WMTS_BARE_CAPS = b"""<?xml version="1.0"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0">
  <Layer>
    <ows:Title xmlns:ows="http://www.opengis.net/ows/1.1">ortho</ows:Title>
    <ows:Identifier xmlns:ows="http://www.opengis.net/ows/1.1">ortho</ows:Identifier>
  </Layer>
</Capabilities>"""


# ── 1) WMS/WMTS describe() CRS 诚实性 ──────────────────────────────────────


def test_wms_describe_reports_advertised_crs_and_bbox():
    """WMS 1.3.0：CRS 列表与地理 bbox 忠实取自 capabilities 文档。"""
    desc = _wms_adapter(WMS_130_CAPS).describe("roads")
    assert desc.srs == "EPSG:4326"  # urn:ogc:def:crs:EPSG::4326 规范化
    assert "EPSG:3857" in desc.metadata["normalized_crs"]
    assert desc.metadata["advertised_crs"] == ["EPSG:3857", "urn:ogc:def:crs:EPSG::4326"]
    assert desc.bbox == [100.5, 20.25, 120.5, 40.75]  # [w, s, e, n]
    assert desc.metadata["bbox_crs"] == "EPSG:4326"


def test_wms_describe_inherits_parent_layer_crs_and_bbox():
    """WMS 继承语义：子 Layer 未声明时沿祖先 Layer 链取最近声明。"""
    desc = _wms_adapter(WMS_130_CAPS).describe("terrain")
    assert desc.srs == "EPSG:4326"
    assert desc.bbox == [-15.0, 30.0, 35.0, 65.0]


def test_wms_111_describe_parses_srs_list_and_latlonbbox():
    """WMS 1.1.1：<SRS> 空白分隔列表 + LatLonBoundingBox 属性。"""
    desc = _wms_adapter(WMS_111_CAPS).describe("parcels")
    assert desc.srs == "EPSG:4326"
    assert desc.metadata["normalized_crs"] == ["EPSG:4326", "EPSG:3857"]
    assert desc.bbox == [-10.5, 20.0, 30.25, 55.5]


def test_wms_describe_without_crs_is_honest_no_epsg3857_fabricated():
    """capabilities 无 CRS/bbox → 诚实 None/空 + notes，绝不出现 EPSG:3857。"""
    desc = _wms_adapter(WMTS_BARE_CAPS).describe("ortho")
    assert desc.srs is None
    assert desc.crs is None
    assert desc.bbox is None
    meta = desc.metadata
    assert meta["advertised_crs"] == []
    assert meta["bbox_crs"] is None
    assert meta["notes"], "必须留 note 说明未知原因"
    # 任何值字段都不得出现伪造的 EPSG:3857 / 世界 extent
    assert "EPSG:3857" not in (desc.srs or "")
    assert desc.bbox != [-180.0, -90.0, 180.0, 90.0]


def test_wms_describe_unknown_layer_is_honest():
    desc = _wms_adapter(WMS_130_CAPS).describe("no_such_layer")
    assert desc.srs is None
    assert desc.bbox is None
    assert any("not found" in n for n in desc.metadata["notes"])


def test_wms_describe_capabilities_failure_is_honest():
    """GetCapabilities 拉取失败 → describe 仍返回 descriptor（诚实降级）。"""
    desc = _wms_adapter(None, error=RuntimeError("connection refused")).describe("roads")
    assert desc.srs is None
    assert desc.bbox is None
    assert desc.metadata["describe_error"]
    assert any("GetCapabilities" in n for n in desc.metadata["notes"])


def test_wms_describe_malformed_bbox_is_rejected_not_guessed():
    """Layer 自身声明了畸形地理 bbox（minx > maxx，反经线跨越类）：拒绝该
    声明并留 note；祖先 Layer 的合法声明 bbox 仍可继承（也是文档声明，
    不是伪造）。"""
    caps = WMS_130_CAPS.replace(
        b"<westBoundLongitude>100.5</westBoundLongitude>",
        b"<westBoundLongitude>170.5</westBoundLongitude>",
    ).replace(
        b"<eastBoundLongitude>120.5</eastBoundLongitude>",
        b"<eastBoundLongitude>-170.5</eastBoundLongitude>",
    )
    desc = _wms_adapter(caps).describe("roads")
    assert any("malformed" in n for n in desc.metadata["notes"])
    assert desc.bbox == [-15.0, 30.0, 35.0, 65.0]  # 继承自父 Layer 的声明
    assert desc.metadata["bbox_crs"] == "EPSG:4326"


def test_wms_describe_uses_bounded_get_and_safe_xml():
    """describe 走 bounded_get（有界下载）+ defusedxml 解析。"""
    adapter = _wms_adapter(WMS_130_CAPS)
    adapter.describe("roads")
    call = adapter.session.calls[0]
    assert call["params"]["REQUEST"] == "GetCapabilities"
    assert call["params"]["SERVICE"] == "WMS"


# ── 2) geo_raster /vsicurl href 的 SSRF 门禁 ───────────────────────────────


def test_vsicurl_gate_blocks_private_and_metadata_ips():
    for bad in (
        "http://169.254.169.254/latest/meta-data.tif",  # 云元数据
        "http://127.0.0.1:8080/scene.tif",              # 回环
        "http://10.1.2.3/scene.tif",                    # RFC1918
        "http://192.168.1.10/scene.tif",                # RFC1918
        "http://[::1]/scene.tif",                       # IPv6 回环
        "http://[::ffff:127.0.0.1]/scene.tif",          # IPv4-mapped IPv6
    ):
        with pytest.raises(DataFabricSecurityError, match="SSRF|blocked"):
            validate_remote_href(bad)


def test_vsicurl_gate_blocks_localhost_and_embedded_vsicurl():
    with pytest.raises(DataFabricSecurityError):
        validate_remote_href("http://localhost/scene.tif")
    # /vsicurl/ 前缀内嵌的 http 目标同样校验
    with pytest.raises(DataFabricSecurityError):
        validate_remote_href("/vsicurl/http://127.0.0.1/scene.tif")
    with pytest.raises(DataFabricSecurityError):
        validate_remote_href("/vsicurl/http://169.254.169.254/scene.tif")


def test_vsicurl_gate_public_targets_pass_without_network():
    # 公网字面 IP：不触发 DNS
    assert validate_remote_href(f"http://{PUBLIC_IPV4}/scene.tif") == (
        f"http://{PUBLIC_IPV4}/scene.tif"
    )
    assert validate_remote_href(f"/vsicurl/http://{PUBLIC_IPV4}/scene.tif") == (
        f"/vsicurl/http://{PUBLIC_IPV4}/scene.tif"
    )


def test_vsicurl_gate_non_http_passes_untouched():
    """本地路径与 /vsi*、s3 等非 http(s) 源原样放行。"""
    untouched = [
        "/data/local/scene.tif",
        "C:\\gis\\scene.tif",
        "/vsis3/my-bucket/scene.tif",
        "/vsigs/bucket/scene.tif",
        "s3://my-bucket/scene.tif",
        "ref:raster/abc",
        "",
    ]
    for uri in untouched:
        assert validate_remote_href(uri) == uri


def test_vsicurl_gate_hostname_resolution_public_passes(monkeypatch):
    """公网域名（打桩 DNS → 公网 IP）通过；私网解析结果被拒。"""

    def _fake_getaddrinfo(host, port=None, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IPV4, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert validate_remote_href("https://imagery.public-example.test/scene.tif")

    def _private_getaddrinfo(host, port=None, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _private_getaddrinfo)
    with pytest.raises(DataFabricSecurityError):
        validate_remote_href("https://rebind.private-example.test/scene.tif")


def test_raster_reader_open_gate_blocks_before_gdal(monkeypatch):
    """RasterReader.open（GDAL 打开的唯一 sanctioned 入口）在 rasterio.open
    之前就拒绝私网 href——仅测校验门禁，不真开 GDAL。"""
    import rasterio

    def _must_not_open(uri, *args, **kwargs):
        raise AssertionError(f"rasterio.open must not be reached for {uri!r}")

    monkeypatch.setattr(rasterio, "open", _must_not_open)
    for bad in (
        "http://169.254.169.254/latest/meta-data.tif",
        "http://127.0.0.1/scene.tif",
        "http://localhost/scene.tif",
        "/vsicurl/http://10.0.0.9/scene.tif",
    ):
        with pytest.raises(ValueError, match="SSRF|blocked"):
            RasterReader.open(bad)


def test_raster_reader_open_local_path_passes_gate(monkeypatch):
    """本地路径过校验门禁后正常进入 rasterio.open（打桩，不开真文件）。"""

    class _FakeDS:
        closed = True

    opened: list[str] = []

    def _fake_open(uri, *args, **kwargs):
        opened.append(uri)
        return _FakeDS()

    import rasterio

    monkeypatch.setattr(rasterio, "open", _fake_open)
    reader = RasterReader.open("/data/local/scene.tif")
    assert opened == ["/data/local/scene.tif"]
    reader.close()


def test_raster_reader_open_public_href_passes_gate(monkeypatch):
    """公网字面 IP href 通过门禁并进入 rasterio.open（打桩）。"""

    class _FakeDS:
        closed = True

    opened: list[str] = []

    def _fake_open(uri, *args, **kwargs):
        opened.append(uri)
        return _FakeDS()

    import rasterio

    monkeypatch.setattr(rasterio, "open", _fake_open)
    reader = RasterReader.open(f"http://{PUBLIC_IPV4}/scene.tif")
    assert opened == [f"http://{PUBLIC_IPV4}/scene.tif"]
    reader.close()


def test_raster_reader_gate_is_injectable(monkeypatch):
    """门禁是可注入 seam：reader 模块级绑定可被测试替换。"""
    import app.lib.geo_raster.reader as reader_mod

    seen: list[str] = []

    def _gate(uri):
        seen.append(uri)
        raise RuntimeError("blocked by injected gate")

    monkeypatch.setattr(reader_mod, "validate_remote_href", _gate)
    with pytest.raises(RuntimeError, match="injected"):
        RasterReader.open("https://anything.test/scene.tif")
    assert seen == ["https://anything.test/scene.tif"]


# ── 3) STAC：基地址可配置 + asset href SSRF ────────────────────────────────


def test_stac_api_url_settings_field_default():
    from app.core.config import settings
    from app.services.rs import stac_client

    assert settings.STAC_API_URL == "https://earth-search.aws.element84.com/v1"
    assert stac_client._catalog_url() == settings.STAC_API_URL


def test_stac_api_url_configurable(monkeypatch):
    from app.core.config import settings
    from app.services.rs import stac_client

    monkeypatch.setattr(settings, "STAC_API_URL", "http://stac.internal.test/v1")
    assert stac_client._catalog_url() == "http://stac.internal.test/v1"


def test_stac_asset_href_validation_blocks_private():
    from app.services.rs import stac_client

    for bad in (
        "http://169.254.169.254/latest/meta-data.tif",
        "http://127.0.0.1:9000/scene.tif",
        "http://10.1.2.3/scene.tif",
        "http://localhost/scene.tif",
    ):
        with pytest.raises(DataFabricSecurityError):
            stac_client._validate_asset_href(bad)


def test_stac_asset_href_validation_passes_public_and_non_http():
    from app.services.rs import stac_client

    public = f"http://{PUBLIC_IPV4}/scene.tif"
    assert stac_client._validate_asset_href(public) == public
    # 非 http(s)（本地路径 / /vsis3）原样放行
    assert stac_client._validate_asset_href("/vsis3/sentinel-s2-l2a/scene.tif") == (
        "/vsis3/sentinel-s2-l2a/scene.tif"
    )
