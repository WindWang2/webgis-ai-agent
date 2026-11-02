"""GOAL-02 离线 fixture 层：OGC API - Tiles / Coverages / STAC-COG 模拟源（M7）。

扩展 ``tests/data/fabric_fixtures.py`` 的 FakeSourceServer（子类，不改
既有文件的契约）：每个 installer 挂一段**最小真响应**的 canned 路由，
供 OGC Tiles/Coverages adapter、瓦片代理与 coverage 受控读取离线往返。

真值约定（测试断言以此为准，服务端与断言共享同一常量，不存在"测试
伪造通过"）：

- 瓦片：``TILE_PNG_BYTES``（1x1 红 PNG）/``TILE_MVT_BYTES``（gzip 魔数
  前缀）；仅 z/x/y == ``VALID_TILE`` 的瓦片存在，其余 404（真实服务的
  稀疏行为）。
- Coverage 网格：x=[100.0,100.02,100.04]，y=[20.0,20.02,20.04]，值
  ``value = 10 + row*nx + col``，nodata=-9999 落在 [0][0]（COVJSON
  NdArray axisNames=["y","x"]）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import parse_qs

import pytest
import requests

from tests.data.fabric_fixtures import FAKE_HOST, FakeSourceServer
from tests.fixtures.data_fabric.fake_server import make_response

# ── Tiles 常量 ───────────────────────────────────────────────────────────────

TMS_WEBMERCATOR_URI = "http://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad"
TMS_CRS84_URI = "http://www.opengis.net/def/tilematrixset/OGC/1.0/WorldCRS84Quad"
CRS_3857_URI = "http://www.opengis.net/def/crs/EPSG/0/3857"
MEDIA_MVT = "application/vnd.mapbox-vector-tile"
MEDIA_PNG = "image/png"

#: 存在的瓦片（z, col, row) —— XYZ 语义下即 (z, x, y)。
VALID_TILE = (2, 1, 1)

_MERCATOR_BOUND = 20037508.342789244

#: 最小真 PNG：1x1 红色像素（确定性字节）。
TILE_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\x80"
    b"\x0d\x00\x00\x86\x00\x81\xb3\x9e\x8f\xc3\xd9\x00\x00\x00\x00IEND\xaeB`\x82"
)

#: gzip 魔数前缀的伪 MVT 体（代理只透传字节 + 魔数嗅探，不解析矢量）。
TILE_MVT_BYTES = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + b"\x00" * 22


def webmercator_tms_doc(max_zoom: int = 5) -> Dict[str, Any]:
    """WebMercatorQuad 注册定义的最小真形状（OGC 17-083r2 Annex E.4）。"""
    matrices = []
    for z in range(max_zoom + 1):
        n = 1 << z
        matrices.append({
            "id": str(z),
            "matrixWidth": n,
            "matrixHeight": n,
            "tileWidth": 256,
            "tileHeight": 256,
            "pointOfOrigin": [-_MERCATOR_BOUND, _MERCATOR_BOUND],  # topLeft
            "cellSize": 2 * _MERCATOR_BOUND / (256 * n),
        })
    return {
        "id": "WebMercatorQuad",
        "uri": TMS_WEBMERCATOR_URI,
        "title": "Web Mercator Quad",
        "crs": CRS_3857_URI,
        "orderedAxes": ["E", "N"],
        "tileMatrices": matrices,
    }


def tiles_tileset_doc(
    *,
    collection_id: str,
    media_type: str = MEDIA_MVT,
    tms_uri: str = TMS_WEBMERCATOR_URI,
    crs: Optional[str] = CRS_3857_URI,
    with_tile_links: bool = True,
    embed_tms: bool = False,
    relative_hrefs: bool = False,
    prefix: str = "/ogc",
) -> Dict[str, Any]:
    """tileset 元数据（OGC API - Tiles Part 1 tileset JSON 最小真形状）。

    ``relative_hrefs=True`` 时链接用相对路径（RFC 8288 允许；相当比例的
    真实实现如此返回 —— R1 P1-3 的覆盖面）。"""
    base = f"https://{FAKE_HOST}{prefix}/collections/{collection_id}/tiles/WebMercatorQuad"
    tile_base = (
        f"{prefix}/collections/{collection_id}/tiles/WebMercatorQuad"
        if relative_hrefs
        else base
    )
    doc: Dict[str, Any] = {
        "title": f"{collection_id} tiles",
        "tileMatrixSetDefinition": tms_uri,
        "boundingBox": {
            "bbox": [-_MERCATOR_BOUND, -_MERCATOR_BOUND, _MERCATOR_BOUND, _MERCATOR_BOUND],
            "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
        },
        "links": [],
    }
    if crs is not None:
        doc["crs"] = crs
    if embed_tms:
        doc["tileMatrixSet"] = webmercator_tms_doc()
    if with_tile_links:
        doc["links"].append({
            "rel": "tile",
            "type": media_type,
            "templated": True,
            "href": f"{tile_base}/{{tileMatrix}}/{{tileRow}}/{{tileCol}}",
        })
    doc["links"].append({
        "rel": "http://www.opengis.net/def/rel/ogc/1.0/tiling-scheme",
        "type": "application/json",
        "href": (
            f"{prefix}/tileMatrixSets/WebMercatorQuad"
            if relative_hrefs
            else f"https://{FAKE_HOST}{prefix}/tileMatrixSets/WebMercatorQuad"
        ),
    })
    return doc


def tiles_conformance_doc(include_tiles: bool = True) -> Dict[str, Any]:
    uris = ["http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/core"]
    if include_tiles:
        uris += [
            "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/core",
            "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/tileset",
            "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/tile",
        ]
    return {"conformsTo": uris}


# ── Coverage 常量 ────────────────────────────────────────────────────────────

COV_FORMAT_JSON = "application/prs.coverage+json"
COVERAGE_AXIS_X_VALUES = [100.0, 100.02, 100.04]
COVERAGE_AXIS_Y_VALUES = [20.0, 20.02, 20.04]
COVERAGE_NODATA = -9999.0


def coverage_values(
    x_values: Sequence[float] = COVERAGE_AXIS_X_VALUES,
    y_values: Sequence[float] = COVERAGE_AXIS_Y_VALUES,
) -> List[List[float]]:
    """真值网格：value = 10 + row*nx + col；[0][0] = nodata。"""
    ny, nx = len(y_values), len(x_values)
    out = []
    for r in range(ny):
        row = []
        for c in range(nx):
            row.append(COVERAGE_NODATA if (r == 0 and c == 0) else float(10 + r * nx + c))
        out.append(row)
    return out


def coverage_domainset_doc() -> Dict[str, Any]:
    return {
        "type": "DomainSet",
        "gridType": "regular",
        "axes": {
            "x": {"values": list(COVERAGE_AXIS_X_VALUES)},
            "y": {"values": list(COVERAGE_AXIS_Y_VALUES)},
        },
    }


def coverage_rangetype_doc() -> Dict[str, Any]:
    return {
        "type": "RangeType",
        "bands": [
            {
                "name": "elevation",
                "datatype": "http://www.opengis.net/def/dataType/OGC/0/float",
                "nodata": COVERAGE_NODATA,
                "unit": {"symbol": "m"},
            }
        ],
    }


#: COVJSON domain.referencing 的 CRS 声明（服务自己的地理 CRS 声明）。
COVJSON_CRS84_REFERENCING = [
    {
        "coordinates": ["x", "y"],
        "system": {
            "type": "GeographicCRS",
            "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
        },
    }
]


def coverage_covjson_doc(
    x_values: Sequence[float] = COVERAGE_AXIS_X_VALUES,
    y_values: Sequence[float] = COVERAGE_AXIS_Y_VALUES,
    with_crs: bool = True,
) -> Dict[str, Any]:
    """subset 响应 = **全网格的真子集**（真实服务行为：subset 取原像元）。

    kept 轴值在全轴常量中的索引决定取哪些行列 —— truth 与
    ``coverage_values()`` 的全网格逐点一致。"""
    full = coverage_values()
    x_idx = [COVERAGE_AXIS_X_VALUES.index(v) for v in x_values]
    y_idx = [COVERAGE_AXIS_Y_VALUES.index(v) for v in y_values]
    vals = [[full[r][c] for c in x_idx] for r in y_idx]
    flat = [v for row in vals for v in row]
    domain: Dict[str, Any] = {
        "type": "Domain",
        "domainType": "Grid",
        "axes": {
            "x": {"values": list(x_values)},
            "y": {"values": list(y_values)},
        },
    }
    if with_crs:
        domain["referencing"] = COVJSON_CRS84_REFERENCING
    return {
        "type": "Coverage",
        "domain": domain,
        "parameters": {
            "elevation": {
                "type": "Parameter",
                "observedProperty": {"label": {"en": "elevation"}},
            }
        },
        "ranges": {
            "elevation": {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": ["y", "x"],
                "shape": [len(y_values), len(x_values)],
                "values": flat,
            }
        },
    }


def coverage_geotiff_bytes(
    x_values: Sequence[float] = COVERAGE_AXIS_X_VALUES,
    y_values: Sequence[float] = COVERAGE_AXIS_Y_VALUES,
    with_crs: bool = True,
) -> bytes:
    """真 GeoTIFF 字节（内存构建）：值与 coverage_values() 同源真值。

    y 升序轴 → GeoTIFF north-up 需行翻转；CRS84 像元中心 → 角点 transform。
    """
    import io

    import rasterio
    from rasterio.transform import Affine

    vals = coverage_values()
    x_idx = [COVERAGE_AXIS_X_VALUES.index(v) for v in x_values]
    y_idx = [COVERAGE_AXIS_Y_VALUES.index(v) for v in y_values]
    grid = [[vals[r][c] for c in x_idx] for r in y_idx]
    arr = np_flip(grid)  # north-up 行翻转
    xres = (x_values[-1] - x_values[0]) / max(1, len(x_values) - 1)
    yres = (y_values[-1] - y_values[0]) / max(1, len(y_values) - 1)
    transform = Affine.translation(
        x_values[0] - xres / 2, y_values[-1] + yres / 2
    ) * Affine.scale(xres, -yres)
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="GTiff", width=len(x_values), height=len(y_values),
        count=1, dtype="float32", nodata=COVERAGE_NODATA,
        crs="OGC:CRS84" if with_crs else None, transform=transform,
    ) as dst:
        dst.write(arr.astype("float32"), 1)
    return buf.getvalue()


def _np_array(grid):
    import numpy as np

    return np.asarray(grid, dtype="float32")


def np_flip(grid):
    return _np_array(grid)[::-1, :]


def coverage_conformance_doc() -> Dict[str, Any]:
    return {
        "conformsTo": [
            "http://www.opengis.net/spec/ogcapi-coverages-1/1.0/conf/core",
            "http://www.opengis.net/spec/ogcapi-coverages-1/1.0/conf/coverage",
            "http://www.opengis.net/spec/ogcapi-coverages-1/1.0/conf/oiservice",
        ]
    }


# ── FakeSourceServer 扩展 ────────────────────────────────────────────────────


class OgcServiceFakeServer(FakeSourceServer):
    """adds: add_ogc_tiles / add_ogc_coverages / add_stac_cog。

    参数化失败注入（真实故障面，M7）：``status_overrides`` 把"路径子串 →
    HTTP 状态"叠加到默认 200（401/403/404/429/5xx）；``redirect_hops``
    返回 302 链；``oversize_tile_bytes`` 触发瓦片字节帽。
    """

    def add_ogc_tiles(
        self,
        *,
        collection_ids: Sequence[str] = ("admin",),
        media_type: str = MEDIA_MVT,
        tms_uri: str = TMS_WEBMERCATOR_URI,
        crs: Optional[str] = CRS_3857_URI,
        with_tile_links: bool = True,
        embed_tms: bool = False,
        include_tiles_conformance: bool = True,
        serve_tiles: bool = True,
        relative_hrefs: bool = False,
        status_overrides: Optional[Dict[str, int]] = None,
        redirect_hops: Optional[List[str]] = None,
        oversize_tile_bytes: bool = False,
        prefix: str = "/ogc",
    ) -> str:
        overrides = status_overrides or {}
        cids = list(collection_ids)
        tile_body = TILE_MVT_BYTES if media_type == MEDIA_MVT else TILE_PNG_BYTES

        def handler(request: requests.PreparedRequest) -> requests.Response:
            from urllib.parse import urlsplit

            self._record(request)
            path = urlsplit(request.url).path
            if redirect_hops and re.search(r"/tiles/[^/]+/\d+/\d+/\d+$", path):
                return make_response(request.url, status=302, headers={"Location": redirect_hops[0]})
            for needle, status in overrides.items():
                if needle in path:
                    return make_response(request.url, status=status, text=f"canned {status}")
            # tile bytes: /tiles/{tms}/{z}/{row}/{col}
            m = re.search(r"/tiles/([^/]+)/(\d+)/(\d+)/(\d+)$", path)
            if m and serve_tiles:
                tms, z, row, col = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
                if tms_uri not in TMS_WEBMERCATOR_URI and tms != "WebMercatorQuad":
                    return make_response(request.url, status=404, text="unknown tms")
                if (z, col, row) != VALID_TILE:
                    return make_response(request.url, status=404, text="tile not found")
                body = (b"x" * (8 * 1024 * 1024)) if oversize_tile_bytes else tile_body
                return make_response(
                    request.url,
                    content=body,
                    headers={"Content-Type": media_type},
                )
            if path.endswith("/tileMatrixSets/WebMercatorQuad"):
                return make_response(request.url, json_body=webmercator_tms_doc())
            # tileset metadata: /collections/{cid}/tiles/{tms}
            m2 = re.search(r"/collections/([^/]+)/tiles/([^/]+)$", path)
            if m2:
                cid = m2.group(1)
                return make_response(
                    request.url,
                    json_body=tiles_tileset_doc(
                        collection_id=cid, media_type=media_type, tms_uri=tms_uri,
                        crs=crs, with_tile_links=with_tile_links,
                        embed_tms=embed_tms, relative_hrefs=relative_hrefs,
                        prefix=prefix,
                    ),
                )
            # tilesets list: /collections/{cid}/tiles
            if re.search(r"/collections/([^/]+)/tiles$", path):
                cid = re.search(r"/collections/([^/]+)/tiles$", path).group(1)
                return make_response(request.url, json_body={
                    "tilesets": [{
                        "title": f"{cid} tiles",
                        "tileMatrixSetDefinition": tms_uri,
                        "links": [{
                            "rel": "tileset",
                            "type": "application/json",
                            "href": f"https://{FAKE_HOST}{prefix}/collections/{cid}/tiles/WebMercatorQuad",
                        }],
                    }]
                })
            if path.endswith("/conformance"):
                return make_response(request.url, json_body=tiles_conformance_doc(include_tiles_conformance))
            if re.search(r"/collections/[^/]+$", path):
                cid = path.rstrip("/").rsplit("/", 1)[-1]
                return make_response(request.url, json_body={
                    "id": cid,
                    "title": f"{cid} collection",
                    "itemType": "feature",
                    "extent": {"spatial": {"bbox": [[100.0, 20.0, 130.0, 50.0]]}},
                    "links": [{
                        "rel": "http://www.opengis.net/def/rel/ogc/1.0/tilesets-vector",
                        "type": "application/json",
                        "href": f"https://{FAKE_HOST}{prefix}/collections/{cid}/tiles",
                    }],
                })
            if path.endswith("/collections"):
                return make_response(request.url, json_body={"collections": [
                    {
                        "id": cid,
                        "title": f"{cid} collection",
                        "links": [{
                            "rel": "http://www.opengis.net/def/rel/ogc/1.0/tilesets-vector",
                            "type": "application/json",
                            "href": f"https://{FAKE_HOST}{prefix}/collections/{cid}/tiles",
                        }],
                    }
                    for cid in cids
                ]})
            return make_response(request.url, status=404, text=f"no ogc-tiles route for {path}")

        self._add(prefix, handler)
        return self.base_url + prefix

    def add_ogc_coverages(
        self,
        *,
        collection_ids: Sequence[str] = ("dem",),
        with_coverage_link: bool = True,
        with_domainset: bool = True,
        with_rangetype: bool = True,
        with_crs: bool = True,
        declared_formats: Optional[Sequence[str]] = (COV_FORMAT_JSON,),
        serve_format: str = COV_FORMAT_JSON,
        status_overrides: Optional[Dict[str, int]] = None,
        prefix: str = "/cov",
    ) -> str:
        overrides = status_overrides or {}
        cids = list(collection_ids)
        formats = list(declared_formats or [])

        def handler(request: requests.PreparedRequest) -> requests.Response:
            from urllib.parse import urlsplit

            self._record(request)
            path = urlsplit(request.url).path
            query = parse_qs(urlsplit(request.url).query)
            for needle, status in overrides.items():
                if needle in path:
                    return make_response(request.url, status=status, text=f"canned {status}")
            if path.endswith("/domainset"):
                if not with_domainset:
                    return make_response(request.url, status=404, text="no domainset")
                return make_response(request.url, json_body=coverage_domainset_doc())
            if path.endswith("/rangetype"):
                if not with_rangetype:
                    return make_response(request.url, status=404, text="no rangetype")
                return make_response(request.url, json_body=coverage_rangetype_doc())
            if re.search(r"/collections/[^/]+/coverage$", path):
                if not with_coverage_link:
                    return make_response(request.url, status=404, text="no coverage endpoint")
                x_vals = COVERAGE_AXIS_X_VALUES
                y_vals = COVERAGE_AXIS_Y_VALUES
                # subset=x(min:max),y(min:max) 服务端真过滤（轴名来自声明）。
                for param in query.get("subset", []):
                    for part in str(param).split(","):
                        m = re.match(r"^([A-Za-z0-9_]+)\(([-\d.eE+]+):([-\d.eE+]+)\)$", part.strip())
                        if not m:
                            continue
                        axis, lo, hi = m.group(1), float(m.group(2)), float(m.group(3))
                        vals = x_vals if axis == "x" else y_vals if axis == "y" else None
                        if vals is not None:
                            kept = [v for v in vals if lo <= v <= hi]
                            if axis == "x":
                                x_vals = kept
                            else:
                                y_vals = kept
                if serve_format == "image/tiff":
                    return make_response(
                        request.url,
                        content=coverage_geotiff_bytes(x_vals, y_vals, with_crs=with_crs),
                        headers={"Content-Type": "image/tiff; application=geotiff"},
                    )
                return make_response(
                    request.url,
                    json_body=coverage_covjson_doc(x_vals, y_vals, with_crs=with_crs),
                    headers={"Content-Type": COV_FORMAT_JSON},
                )
            if path.endswith("/conformance"):
                return make_response(request.url, json_body=coverage_conformance_doc())
            if re.search(r"/collections/[^/]+$", path):
                cid = path.rstrip("/").rsplit("/", 1)[-1]
                links = [
                    {"rel": "self", "href": f"https://{FAKE_HOST}{prefix}/collections/{cid}"},
                ]
                primary_type = formats[0] if formats else None
                if with_coverage_link:
                    cov_link = {
                        "rel": "http://www.opengis.net/def/rel/ogc/1.0/coverage",
                        "href": f"https://{FAKE_HOST}{prefix}/collections/{cid}/coverage",
                    }
                    if primary_type:
                        cov_link["type"] = primary_type
                    links.append(cov_link)
                    for extra in formats[1:]:
                        links.append({
                            "rel": "alternate",
                            "type": extra,
                            "href": f"https://{FAKE_HOST}{prefix}/collections/{cid}/coverage",
                        })
                return make_response(request.url, json_body={
                    "id": cid,
                    "title": f"{cid} coverage",
                    "itemType": "coverage",
                    "extent": {"spatial": {"bbox": [[100.0, 20.0, 100.04, 20.04]]}},
                    "links": links,
                })
            if path.endswith("/collections"):
                return make_response(request.url, json_body={"collections": [
                    {"id": cid, "title": f"{cid} coverage", "itemType": "coverage"} for cid in cids
                ]})
            return make_response(request.url, status=404, text=f"no coverage route for {path}")

        self._add(prefix, handler)
        return self.base_url + prefix

    def add_stac_cog(
        self,
        *,
        item_ids: Sequence[str] = ("scene-001",),
        asset_href: Optional[str] = None,
        prefix: str = "/stac",
    ) -> str:
        """STAC search：item assets 指向 COG href（默认 fake host；字节面
        物化测试用本地 HTTP 服务器，见 test_cog_materialization_v1.py）。"""

        def handler(request: requests.PreparedRequest) -> requests.Response:
            self._record(request)
            if request.method == "POST":
                features = []
                for iid in item_ids:
                    href = asset_href or f"https://{FAKE_HOST}/assets/{iid}.tif"
                    features.append({
                        "type": "Feature",
                        "stac_version": "1.0.0",
                        "id": iid,
                        "geometry": {"type": "Polygon", "coordinates": [[[100.0, 20.0], [100.04, 20.0], [100.04, 20.04], [100.0, 20.04], [100.0, 20.0]]]},
                        "properties": {"datetime": "2024-06-01T00:00:00Z"},
                        "assets": {"data": {"href": href, "roles": ["data"], "type": "image/tiff; application=geotiff; profile=cloud-optimized"}},
                        "links": [],
                        "bbox": [100.0, 20.0, 100.04, 20.04],
                    })
                return make_response(request.url, json_body={
                    "type": "FeatureCollection",
                    "stac_version": "1.0.0",
                    "context": {"returned": len(features), "matched": len(features)},
                    "features": features,
                    "links": [],
                })
            return make_response(request.url, json_body={
                "id": "ads-fixture-cog", "stac_version": "1.0.0",
                "links": [{"href": f"https://{FAKE_HOST}/stac/search", "rel": "search", "method": "POST"}],
            })

        self._add(prefix, handler)
        return self.base_url + prefix


@pytest.fixture
def ogc_fake_server() -> OgcServiceFakeServer:
    return OgcServiceFakeServer()
