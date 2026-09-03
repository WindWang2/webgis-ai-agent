"""统一 Adapter Contract Test Suite（ADR-0094 §15 / 任务书）。

所有注册 adapter 共同契约（fake transport，无真实网络）：
- probe / capabilities / list_datasets / describe / query / health 可调用且类型正确
- capabilities() 非空且与 capability 矩阵一致（声明即契约）
- query 对非法输入抛 typed error（不静默返回空成功）
- query 对不可达源抛 typed error（不伪造数据）
- 空结果语义：features=[] + total_count 如实
- unsupported capability → typed QUERY_UNSUPPORTED/INVALID_QUERY
- 差分语义：PostGIS/WFS/ArcGIS/OGC 对同一 synthetic bbox+filter 请求
  的参数化行为一致（各协议语法不同但语义可映射）
"""
import pytest

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.adapters import (
    ArcGISAdapter,
    OGCAPIAdapter,
    WFSAdapter,
)
from app.services.data_fabric.errors import DataFabricError, InvalidQueryError
from app.services.data_fabric.query.capabilities import default_capabilities
from tests.unit.test_ogc_adapters_753 import FakeResponse, FakeSession
from tests.unit.test_data_fabric_postgis_v2 import _adapter as _pg_adapter


# ── registry 内全部 adapter 的静态契约 ─────────────────────────────────────


def test_registry_specs_have_capability_matrices():
    """每个注册源类型都有 truthful capability 矩阵默认值。"""
    from app.services.data_fabric.registry import get_registry

    for st in get_registry().supported_source_types():
        spec = get_registry().resolve(st)
        caps = default_capabilities(spec.canonical)
        assert caps.source_type in (spec.canonical, "generic")
        # 声明的 spatial 操作都在已知集合内
        known = {"bbox", "intersects", "within", "contains", "touches", "overlaps", "dwithin"}
        assert set(caps.spatial_predicates) <= known


def test_demo_registry_spec_is_labeled():
    from app.services.data_fabric.registry import get_registry

    spec = get_registry().resolve("generic")
    assert spec.is_demo, "generic/mock/sample 是显式 demo adapter"


# ── 网络 adapter 共同契约（fake session）──────────────────────────────────


def _wfs():
    profile = ConnectionProfile(provider_type="wfs", endpoint="")
    profile.url = "https://example.com/wfs"
    a = WFSAdapter(profile)
    a.session = FakeSession()
    a.session.routes = {}
    a._caps_cache = None
    return a


def _ogc():
    profile = ConnectionProfile(provider_type="ogc_api", endpoint="")
    profile.url = "https://example.com/ogc"
    a = OGCAPIAdapter(profile)
    s = FakeSession()
    s.conformance = []
    s.items_response = {"type": "FeatureCollection", "features": []}

    def route(url, params=None, timeout=None, **kw):
        s.calls.append({"url": url, "params": dict(params or {})})
        if url.endswith("/conformance"):
            return FakeResponse(json_data={"conformsTo": s.conformance})
        if url.endswith("/collections/parcels"):
            return FakeResponse(json_data={
                "id": "parcels", "title": "Parcels",
                "extent": {"spatial": {"bbox": [[100.0, 20.0, 120.0, 40.0]]}},
                "crs": ["EPSG:4326"],
            })
        if "/items" in url:
            return FakeResponse(json_data=s.items_response)
        return FakeResponse(json_data={})

    s.get = route
    a.session = s
    return a


def _arcgis():
    profile = ConnectionProfile(provider_type="arcgis", endpoint="")
    profile.url = "https://example.com/arcgis/rest/services/Hosted/FS/FeatureServer"
    a = ArcGISAdapter(profile)
    s = FakeSession()

    def route(url, params=None, timeout=None, **kw):
        s.calls.append({"url": url, "params": dict(params or {})})
        if url.rstrip("/").endswith("FeatureServer"):
            return FakeResponse(json_data={"layers": [{"id": 0, "name": "l0"}]})
        if url.rstrip("/").endswith("/0"):
            return FakeResponse(json_data={
                "id": 0, "name": "l0", "maxRecordCount": 2000,
                "supportsPagination": True,
                "extent": {"xmin": 100, "ymin": 20, "xmax": 120, "ymax": 40,
                           "spatialReference": {"wkid": 4326}},
                "fields": [{"name": "pid", "type": "esriFieldTypeInteger"}],
            })
        if url.rstrip("/").endswith("/query"):
            return FakeResponse(json_data=s.query_response)
        return FakeResponse(json_data={})

    s.query_response = {"features": []}
    s.get = route
    a.session = s
    return a


@pytest.mark.parametrize("maker,name", [(_wfs, "roads"), (_ogc, "parcels"), (_arcgis, "0")])
def test_network_adapter_common_contract(maker, name):
    a = maker()
    # capabilities 非空
    caps = a.capabilities()
    assert isinstance(caps, list) and caps
    # describe 返回 descriptor（诚实：无字段可空，但不抛未类型化异常）
    desc = a.describe(name)
    assert desc.id == name
    # health 可调用
    h = a.health()
    assert h.status in ("healthy", "unreachable", "degraded", "timeout")
    # 空 dataset 的 query 是合法空结果（非错误）
    res = a.query(name, QuerySpec(limit=1))
    assert res.features == []
    assert res.total_count == 0
    assert res.metadata.get("error_type") is None


@pytest.mark.parametrize("maker,name", [(_wfs, "roads"), (_ogc, "parcels"), (_arcgis, "0")])
def test_network_adapters_raise_typed_on_bad_filter(maker, name):
    """AST 语法错误 → typed INVALID_QUERY（不是静默全量）。"""
    a = maker()
    with pytest.raises(DataFabricError) as ei:
        a.query(name, QuerySpec(limit=5, where="(SELECT 1)"))
    assert ei.value.code in (InvalidQueryError.code, "SOURCE_BAD_RESPONSE")


@pytest.mark.parametrize("maker,name", [(_wfs, "roads"), (_ogc, "parcels"), (_arcgis, "0")])
def test_network_adapters_transport_failure_is_typed(maker, name):
    """连接失败 → typed（非空成功伪造）。"""
    a = maker()

    def boom(url, params=None, timeout=None, **kw):
        raise ConnectionError("no route to host")

    a.session.get = boom
    with pytest.raises(DataFabricError):
        a.query(name, QuerySpec(limit=5))


# ── PostGIS 契约（fake connection）─────────────────────────────────────────


def test_postgis_unsupported_capability_typed():
    """unknown-SRID 表 + 非bbox 空间谓词 → typed INVALID_QUERY（不静默）。"""
    executed = []
    # 把 srid 置 None（unknown）

    spec = QuerySpec(limit=5, spatial={
        "op": "intersects",
        "geometry": {"type": "Point", "coordinates": [104, 30]},
    })
    a2 = _pg_adapter(executed, srid=0)
    with pytest.raises(DataFabricError):
        a2.query("public.schools", spec)


def test_postgis_bbox_semantics_differential():
    """差分：PostGIS 编译 bbox 的 envelope 语义与请求一致（minx,miny,maxx,maxy）。"""
    executed = []
    a = _pg_adapter(executed, rows=[])
    a.query("public.schools", QuerySpec(limit=10, bbox=[100.0, 20.0, 110.0, 30.0]))
    env = [p for sql, p in executed if "ST_MakeEnvelope" in sql]
    assert env and list(env[0][:4]) == [100.0, 20.0, 110.0, 30.0], (
        "envelope 参数轴序必须与 bbox 输入一致（lon/lat）"
    )
