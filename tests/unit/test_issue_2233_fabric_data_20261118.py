"""#2233 回归（data fabric 汇总项）。

- STAC：全量可推过滤（planner 不落 filter_split）的 CQL2 死代码复活；
  _probe_stac 读不存在的 adapter.url 且 filter_encoding 与 capabilities_v2 不一致。
- GeoPackage：>50k 层配小 limit 不再被 50,001 行整幅扫描预算误杀。
- ArcGIS/WFS：bbox 的 inSR / BBOX CRS 后缀随 spatial.crs（此前恒 4326/CRS84）。
- FlatGeobuf/GeoJSON-URL：并发下载不互踩、失败不留 .partial；流式响应
  全路径关闭；错误 details 只带脱敏 URL。
- STRtree：hit/miss 两路返回同形（indexed_features），query 下标严格对齐。
"""
import json
import os
import threading
import time

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.errors import (
    InvalidQueryError,
    QueryBudgetExceededError,
    SourceUnreachableError,
)
from app.services.data_fabric.query.models import QuerySpecV2
from app.services.data_fabric.query.predicates import BBox, Eq

from tests.unit.test_ogc_adapters_753 import (
    ARCGIS_FEATURES,
    ARCGIS_LAYER,
    ARCGIS_SERVICE,
    FakeResponse,
    FakeSession,
    WFS_CAPABILITIES,
    WFS_FEATURES,
)


# ── STAC：_probe_stac endpoint + cql2-json ────────────────────────────────


class _ProbeResp:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def content(self):
        return self._raw

    # bounded_get 协议：session.get(...) → resp
    def raise_for_status(self):
        return None

    @property
    def headers(self):
        return {"Content-Length": str(len(self._raw))}

    def iter_content(self, chunk_size=65536):
        yield self._raw

    def close(self):
        pass


def test_probe_stac_uses_endpoint_attr_and_cql2_json():
    """#2233：STACAdapter 只有 .endpoint（无 .url）——探测曾恒空返回；
    编码与 capabilities_v2 一致固定 cql2-json（曾写 cql2-text）。"""
    from app.services.data_fabric.fabric.probing import ProbeCost, _probe_stac

    class _StacStub:
        endpoint = "https://stac.example.com"

        class session:
            @staticmethod
            def get(url, timeout=None, **kw):
                assert url == "https://stac.example.com/conformance"
                return _ProbeResp({
                    "conformsTo": [
                        "https://api.stacspec.org/v1.0.0-rc.3/item-search#filter",
                    ],
                })

    cost = ProbeCost()
    overrides = _probe_stac(_StacStub(), cost)
    assert overrides == {"filter_pushdown": True, "filter_encoding": "cql2-json"}
    assert cost.requests == 1


def test_probe_stac_without_endpoint_returns_empty():
    from app.services.data_fabric.fabric.probing import ProbeCost, _probe_stac

    class _NoEndpoint:
        session = None

    assert _probe_stac(_NoEndpoint(), ProbeCost()) == {}


# ── STAC：全量可推过滤的 CQL2 下推（死代码复活）──────────────────────────


class _StacSearchResp:
    def __init__(self):
        self._raw = json.dumps({
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"cloud_cover": 10},
                 "geometry": None},
            ],
        }).encode()
        self.status_code = 200
        self.headers = {"Content-Length": str(len(self._raw))}
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        yield self._raw

    def close(self):
        self.closed = True


def _stac_adapter_with_filter_push(monkeypatch) -> "object":
    from app.services.data_fabric.adapters.stac_adapter import STACAdapter

    adapter = STACAdapter(ConnectionProfile(
        source_type="stac", endpoint_url="https://stac.example.com", name="t",
    ))
    caps = adapter.capabilities_v2()
    assert caps.filter_pushdown is False
    monkeypatch.setattr(
        adapter, "capabilities_v2",
        lambda: caps.model_copy(update={
            "filter_pushdown": True, "filter_encoding": "cql2-json",
        }),
    )
    return adapter


def _stub_describe(adapter, monkeypatch):
    """describe 用无端点 adapter 的诚实 stub（无 IO），计划照常生成。"""
    from app.services.data_fabric.adapters.stac_adapter import STACAdapter

    demo = STACAdapter(ConnectionProfile(
        source_type="stac", endpoint_url="", name="demo",
    ))
    monkeypatch.setattr(adapter, "describe", demo.describe)


def test_stac_whole_pushable_filter_compiled_to_cql2(monkeypatch):
    """#2233：全量可推（split.partial=False）时 planner 走历史整过滤
    下推、不落 filter_split —— adapter 曾因此跳过 CQL2 编译（死代码），
    过滤被静默改本地求值。断言 filter 以 cql2-json 随 POST 下发。"""
    adapter = _stac_adapter_with_filter_push(monkeypatch)
    posts: list = []

    def _post(url, json=None, timeout=None, **kw):
        posts.append({"url": url, "json": dict(json or {})})
        return _StacSearchResp()

    monkeypatch.setattr(adapter.session, "post", _post)
    _stub_describe(adapter, monkeypatch)

    from app.schemas.data_fabric_schema import QuerySpec

    res = adapter.query("col1", QuerySpec(
        limit=5, filter_expr={"op": "lt", "field": "cloud_cover", "value": 20},
    ))
    assert posts, "search POST must be issued"
    payload = posts[0]["json"]
    assert payload.get("filter-lang") == "cql2-json", (
        "whole-pushable filter must compile to CQL2-JSON (was dead code)"
    )
    assert isinstance(payload.get("filter"), dict)
    assert res.features, "feature returned for the matched item"


def test_stac_guard_path_never_compiles_filter(monkeypatch):
    """守卫路径（filter_ops_local 声明局部 op）→ 计划带
    filter_split={'pushed': None} → 绝不向远端编译过滤。"""
    adapter = _stac_adapter_with_filter_push(monkeypatch)
    # 声明 like 为本地 op：拆分产生 pushed/local 混合 —— 无统计时走守卫。
    caps = adapter.capabilities_v2()
    monkeypatch.setattr(
        adapter, "capabilities_v2",
        lambda: caps.model_copy(update={
            "filter_pushdown": True, "filter_encoding": "cql2-json",
            "filter_ops_local": ["like"],
        }),
    )
    posts: list = []

    def _post(url, json=None, timeout=None, **kw):
        posts.append(dict(json or {}))
        return _StacSearchResp()

    monkeypatch.setattr(adapter.session, "post", _post)
    _stub_describe(adapter, monkeypatch)
    # 与前序用例隔离：V5 统计收割是进程级注册表，同 fp 的测量会改变
    # 计划门的决策 —— 本用例显式置空统计以固定走无统计守卫路径。
    from app.services.data_fabric.query import statistics as _stats_mod

    monkeypatch.setattr(_stats_mod, "statistics_for_request", lambda descriptor, fp: None)

    from app.schemas.data_fabric_schema import QuerySpec

    adapter.query("col1", QuerySpec(limit=5, filter_expr={
        "op": "and",
        "args": [
            {"op": "lt", "field": "cloud_cover", "value": 20},
            {"op": "like", "field": "name", "pattern": "a%"},
        ],
    }))
    assert posts and "filter" not in posts[0], "guard path must not compile remote filter"


# ── GeoPackage：页窗口直读 ────────────────────────────────────────────────


@pytest.fixture
def big_gpkg(tmp_path, monkeypatch):
    geopandas = pytest.importorskip("geopandas")
    import numpy as np
    from shapely.geometry import Point

    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_FABRIC_LOCAL_FILE_ROOTS", str(tmp_path))

    n = 60_001  # > _MAX_QUERY_FEATURES(50,000)
    gdf = geopandas.GeoDataFrame(
        {"v": np.arange(n, dtype="int64")},
        geometry=[Point(100.0 + (i % 100) * 0.001, 20.0 + (i % 50) * 0.001) for i in range(n)],
        crs="EPSG:4326",
    )
    path = tmp_path / "big.gpkg"
    gdf.to_file(str(path), driver="GPKG", layer="pts")
    return str(path)


def _gpkg_adapter(path: str):
    from app.services.data_fabric.adapters.geopackage_adapter import GeoPackageAdapter

    profile = ConnectionProfile(
        source_type="geopackage", name="gpkg", endpoint_url=path,
    )
    adapter = GeoPackageAdapter(profile)
    return adapter


def test_geopackage_small_limit_on_large_layer_succeeds(big_gpkg):
    """#2233：>50k 层 + limit=10 → 只读页窗口，不再抛 QueryBudgetExceededError。"""
    from app.schemas.data_fabric_schema import QuerySpec

    adapter = _gpkg_adapter(big_gpkg)
    res = adapter.query("pts", QuerySpec(limit=10))
    assert res.returned_count == 10
    assert res.total_count == 60_001
    assert res.has_more is True


def test_geopackage_unbounded_scan_budget_unchanged(big_gpkg):
    """无 limit 的有界扫描语义保持：>50k 层仍 typed 拒绝（诚实预算）。"""
    from app.schemas.data_fabric_schema import QuerySpec

    adapter = _gpkg_adapter(big_gpkg)
    with pytest.raises(QueryBudgetExceededError):
        adapter.query("pts", QuerySpec(limit=0))


def test_geopackage_offset_paging_within_budget(big_gpkg):
    from app.schemas.data_fabric_schema import QuerySpec

    adapter = _gpkg_adapter(big_gpkg)
    p1 = adapter.query("pts", QuerySpec(limit=10, offset=5))
    p2 = adapter.query("pts", QuerySpec(limit=10, offset=6))
    assert p1.features[1]["properties"]["v"] == p2.features[0]["properties"]["v"]


# ── ArcGIS / WFS：bbox CRS 随 spatial.crs ────────────────────────────────


def _arcgis_adapter() -> "object":
    from app.services.data_fabric.adapters.arcgis_adapter import ArcGISAdapter

    profile = ConnectionProfile(provider_type="arcgis", endpoint="")
    profile.url = "https://example.com/arcgis/rest/services/Hosted/FS/FeatureServer"
    adapter = ArcGISAdapter(profile)
    s = FakeSession()

    def route(url, params=None, timeout=None, **kwargs):
        s.calls.append({"url": url, "params": dict(params or {})})
        if url.rstrip("/").endswith("FeatureServer"):
            return FakeResponse(json_data=ARCGIS_SERVICE)
        if url.rstrip("/").endswith("/0"):
            return FakeResponse(json_data=dict(ARCGIS_LAYER))
        if url.rstrip("/").endswith("/query"):
            return FakeResponse(json_data=dict(ARCGIS_FEATURES))
        return FakeResponse(json_data={})

    s.get = route
    adapter.session = s
    return adapter


def test_arcgis_bbox_crs_sent_as_insr():
    """#2233：非 4326 bbox 的 inSR 必须随 spatial.crs（此前恒 4326 →
    服务端按错误 CRS 解释 envelope）。"""
    adapter = _arcgis_adapter()
    from app.services.data_fabric.query.models import OffsetPage

    v2 = QuerySpecV2(
        page=OffsetPage(limit=5),
        spatial=BBox(bbox=[11131949.0, 2273030.0, 11813704.0, 2628045.0], crs="EPSG:3857"),
    )
    adapter._execute_v2("0", v2, time.monotonic())
    call = [c for c in adapter.session.calls if c["url"].rstrip("/").endswith("/query")][-1]
    assert call["params"]["inSR"] == "3857"
    assert call["params"]["outSR"] == "4326"


def test_arcgis_default_bbox_crs_still_4326():
    adapter = _arcgis_adapter()
    from app.services.data_fabric.query.models import OffsetPage

    v2 = QuerySpecV2(page=OffsetPage(limit=5), spatial=BBox(bbox=[100.0, 20.0, 110.0, 30.0]))
    adapter._execute_v2("0", v2, time.monotonic())
    call = [c for c in adapter.session.calls if c["url"].rstrip("/").endswith("/query")][-1]
    assert call["params"]["inSR"] == "4326"


def _wfs_adapter() -> "object":
    from app.services.data_fabric.adapters.wfs_adapter import WFSAdapter

    profile = ConnectionProfile(provider_type="wfs", endpoint="")
    profile.url = "https://example.com/wfs"
    adapter = WFSAdapter(profile)
    adapter.session = FakeSession()
    adapter.session.routes = {
        "GetCapabilities": FakeResponse(content=WFS_CAPABILITIES),
        "/wfs": FakeResponse(json_data=WFS_FEATURES),
    }
    return adapter


def test_wfs_bbox_crs_in_kvp_suffix():
    """#2233：WFS BBOX 的 CRS 后缀随 spatial.crs（此前恒 CRS84）。"""
    adapter = _wfs_adapter()
    from app.services.data_fabric.query.models import OffsetPage

    v2 = QuerySpecV2(
        page=OffsetPage(limit=5),
        spatial=BBox(bbox=[11131949.0, 2273030.0, 11813704.0, 2628045.0], crs="EPSG:3857"),
    )
    adapter._execute_v2("roads", v2, time.monotonic())
    bbox_params = [c["params"]["BBOX"] for c in adapter.session.calls if "BBOX" in c["params"]]
    assert bbox_params, "BBOX KVP must be sent"
    assert bbox_params[-1].endswith(",urn:ogc:def:crs:EPSG::3857"), bbox_params[-1]


def test_wfs_default_bbox_crs_unchanged():
    adapter = _wfs_adapter()
    from app.services.data_fabric.query.models import OffsetPage

    v2 = QuerySpecV2(page=OffsetPage(limit=5), spatial=BBox(bbox=[100.0, 20.0, 110.0, 30.0]))
    adapter._execute_v2("roads", v2, time.monotonic())
    bbox_params = [c["params"]["BBOX"] for c in adapter.session.calls if "BBOX" in c["params"]]
    assert bbox_params[-1].endswith(",urn:ogc:def:crs:OGC:1.3:CRS84")


# ── FlatGeobuf：并发下载 / 半成品清理；GeoJSON-URL：流关闭 + URL 脱敏 ──────


class _FgbResp:
    def __init__(self, body: bytes):
        self._body = body
        self.status_code = 200
        self.closed = False

    def raise_for_status(self):
        return None

    @property
    def headers(self):
        return {}

    def iter_content(self, chunk_size=65536):
        body = self._body
        for i in range(0, len(body), chunk_size):
            yield body[i:i + chunk_size]

    def close(self):
        self.closed = True


class _FgbSession:
    def __init__(self, body: bytes):
        self._body = body
        self.head_calls = 0
        self.get_calls = 0

    def head(self, url, timeout=None, allow_redirects=True):
        self.head_calls += 1
        return _FgbResp(b"")

    def get(self, url, stream=None, timeout=None, **kw):
        self.get_calls += 1
        return _FgbResp(self._body)


def test_flatgeobuf_concurrent_downloads_no_corruption():
    """#2233：共享确定性 .partial 曾让并发下载互踩 —— 现每下载唯一
    partial + 每 URL 锁；所有线程完成后文件内容完整且唯一。"""
    from app.services.data_fabric.adapters.flatgeobuf_adapter import (
        _cached_remote_flatgeobuf,
    )

    body = os.urandom(256 * 1024)
    session = _FgbSession(body)
    errors: list = []

    def _dl(out, idx):
        try:
            out[idx] = _cached_remote_flatgeobuf(session, "https://fgb.example.com/a.fgb")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    out: dict = {}
    threads = [threading.Thread(target=_dl, args=(out, i)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    paths = set(out.values())
    assert len(paths) == 1, "all threads must converge on the same cached file"
    with open(paths.pop(), "rb") as fh:
        assert fh.read() == body
    cache_dir = os.path.join(__import__("tempfile").gettempdir(), "webgis-fgb-cache")
    leftovers = [f for f in os.listdir(cache_dir) if f.endswith(".partial")]
    assert not leftovers, f"no .partial leftovers: {leftovers}"


def test_flatgeobuf_budget_error_leaves_no_partial():
    from app.services.data_fabric.adapters import flatgeobuf_adapter
    from app.services.data_fabric.adapters.flatgeobuf_adapter import (
        _cached_remote_flatgeobuf,
    )

    body = os.urandom(4 * 1024 * 1024)
    session = _FgbSession(body)
    url = "https://fgb.example.com/budget.fgb"
    monkey_max = lambda: 1024  # noqa: E731
    orig = flatgeobuf_adapter._local_file_max_bytes_from_settings
    flatgeobuf_adapter._local_file_max_bytes_from_settings = monkey_max
    try:
        with pytest.raises(QueryBudgetExceededError):
            _cached_remote_flatgeobuf(session, url)
    finally:
        flatgeobuf_adapter._local_file_max_bytes_from_settings = orig
    cache_dir = os.path.join(__import__("tempfile").gettempdir(), "webgis-fgb-cache")
    leftovers = [f for f in os.listdir(cache_dir) if f.endswith(".partial")]
    assert not leftovers, f"budget abort must clean its partial: {leftovers}"


class _TrackedResp(FakeResponse):
    def __init__(self, *a, status_code=200, **kw):
        super().__init__(*a, **kw)
        self.status_code = status_code
        self.closed = False

    def close(self):
        self.closed = True


def _geojson_adapter_with(resp, monkeypatch):
    from app.services.data_fabric.adapters.geojson_url_adapter import GeoJSONURLAdapter

    profile = ConnectionProfile(
        source_type="geojson_url", name="gj",
        endpoint_url="https://data.example.com/fc.geojson?token=SECRET123",
    )
    adapter = GeoJSONURLAdapter(profile)
    monkeypatch.setattr(adapter.session, "get", lambda *a, **kw: resp)
    return adapter


def test_geojson_url_http_error_closes_response_and_redacts_url(monkeypatch):
    """#2233：>=400 提前退出曾泄漏流；details 曾带未脱敏 URL（含 token）。"""
    from app.services.data_fabric.adapters.geojson_url_adapter import GeoJSONURLAdapter

    resp = _TrackedResp(json_data={}, status_code=500)
    adapter = _geojson_adapter_with(resp, monkeypatch)
    with pytest.raises(SourceUnreachableError) as ei:
        adapter._fetch_fc()
    assert resp.closed, "error path must close the streaming response"
    details = getattr(ei.value, "details", None) or {}
    assert "SECRET123" not in json.dumps(details), "error details must carry redacted URL"


def test_geojson_url_content_type_error_closes_response(monkeypatch):
    from app.services.data_fabric.adapters.geojson_url_adapter import GeoJSONURLAdapter
    from app.services.data_fabric.errors import SourceBadResponseError

    resp = _TrackedResp(content=b"<html></html>")
    resp.headers["Content-Type"] = "text/html"
    adapter = _geojson_adapter_with(resp, monkeypatch)
    with pytest.raises(SourceBadResponseError):
        adapter._fetch_fc()
    assert resp.closed


# ── STRtree：两路同形 + 下标对齐 ──────────────────────────────────────────


def _feats_with_gap():
    return [
        {"type": "Feature", "properties": {}, "geometry": None},  # 无几何：不进树
        {"type": "Feature", "properties": {"n": 1},
         "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
        {"type": "Feature", "properties": {"n": 2},
         "geometry": {"type": "Point", "coordinates": [120.0, 40.0]}},
    ]


def test_strtree_hit_miss_same_shape_and_index_alignment():
    """#2233：geometry=None 要素曾使 miss 路 geoms 与 hit 路 features 两形
    不一致、tree.query 下标错位 —— 现两路均返回与下标严格对齐的
    indexed_features。"""
    shapely = pytest.importorskip("shapely")
    from app.services.data_fabric.spatial_index_runtime import (
        SpatialIndexRuntime,
        build_strtree_index,
    )

    feats = _feats_with_gap()
    rt = SpatialIndexRuntime()
    tree1, idx_feats1 = build_strtree_index(feats, content_fingerprint="fp-2233", runtime=rt)
    tree2, idx_feats2 = build_strtree_index(feats, content_fingerprint="fp-2233", runtime=rt)
    assert tree1 is tree2, "cache hit must not rebuild"
    assert [f["properties"]["n"] for f in idx_feats1] == [1, 2]
    assert [f["properties"]["n"] for f in idx_feats2] == [1, 2]
    hits = tree2.query(shapely.box(116.0, 39.0, 117.0, 40.5))
    assert len(hits) == 1
    assert idx_feats2[int(hits[0])]["properties"]["n"] == 1, (
        "query index must align with indexed_features (None-geometry gap shifted it)"
    )


def test_strtree_without_runtime_consistent():
    pytest.importorskip("shapely")
    from app.services.data_fabric.spatial_index_runtime import build_strtree_index

    feats = _feats_with_gap()
    tree, idx_feats = build_strtree_index(feats, content_fingerprint="fp-x")
    assert [f["properties"]["n"] for f in idx_feats] == [1, 2]
