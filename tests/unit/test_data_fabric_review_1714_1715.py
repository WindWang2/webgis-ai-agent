"""Review-fix regressions for issues #1714 / #1715 (data_fabric).

#1714 (reliability):
  - half-open 唯一 trial 名额在非瞬时错误路径释放（record_failure 早退前）；
  - 物化路径与查询/describe 路径同一道熔断器门（materialize_dataset /
    execute_query 经 ``get_breaker_registry().call``）。

#1715 (performance):
  - stats_api 适配器经公共读取通道流式 + 有界（源声明的
    ``max_bytes_per_request`` 经 source_registry 下传并强制执行）；
  - connection_manager / spatial_catalog 进程级 dict 有界（LRU + idle TTL），
    断连/逐出时级联清理目录足迹。

All deterministic: injectable clock/registry seams, no network, no real sleeps.
"""
import pytest
import requests

from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DatasetDescriptor,
    QueryResult,
    QuerySpec,
)
from app.services.data_fabric import connection_manager as cm_mod
from app.services.data_fabric import materialization_service as mat_svc
from app.services.data_fabric.adapters.stats_api_adapter import StatsApiAdapter
from app.services.data_fabric.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitState,
    set_breaker_registry,
)
from app.services.data_fabric.connection_manager import DataFabricConnectionManager
from app.services.data_fabric.errors import (
    InvalidQueryError,
    SourceBadResponseError,
    SourceUnreachableError,
)
from app.services.data_fabric.source_registry import QuotaDecl, SourceDefinition
from app.services.data_fabric.spatial_catalog import SpatialCatalogService


class _FakeClock:
    def __init__(self, t0: float = 0.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture()
def clean_breaker():
    """隔离进程级 breaker registry（低阈值，确定性 fail-fast）。"""
    reg = CircuitBreakerRegistry(breaker=CircuitBreaker(failure_threshold=1, cool_down=30.0))
    set_breaker_registry(reg)
    yield reg
    set_breaker_registry(CircuitBreakerRegistry())


# ── #1714-1: half-open trial 名额在非瞬时错误路径释放 ──────────────────────


def test_halfopen_permanent_error_releases_trial_slot():
    """half-open 里一次 4xx/校验类试探失败后，trial 名额必须恢复放行。

    修复前：``record_failure`` 对非瞬态错误早退，不释放名额 → 条目永久卡在
    HALF_OPEN fail-fast（_state 只在 OPEN→HALF_OPEN 跃迁时重置名额）。
    """
    clk = _FakeClock()
    reg = CircuitBreakerRegistry(breaker=CircuitBreaker(failure_threshold=2, cool_down=30.0, clock=clk))
    reg.record_failure("src", SourceUnreachableError("conn refused"))
    reg.record_failure("src", SourceUnreachableError("conn refused"))
    clk.advance(31.0)
    assert reg.state("src") == CircuitState.HALF_OPEN
    assert reg.allow("src") is True  # 占用唯一 trial 名额

    # 非瞬态试探失败（坏请求/认证/校验类）——不开门，但必须归还名额
    reg.record_failure("src", InvalidQueryError("400 bad request"))
    assert reg.state("src") == CircuitState.HALF_OPEN, "非瞬态错误不得开门"
    assert reg.allow("src") is True, "非瞬态试探失败后名额必须恢复放行（#1714）"
    # 名额唯一性保持：放行一次后再次 allow 仍被拒
    assert reg.allow("src") is False


def test_halfopen_permanent_error_via_call_recovers():
    """生产路径（registry.call）下同一语义：永久错误试探不卡死熔断器。"""
    clk = _FakeClock()
    reg = CircuitBreakerRegistry(breaker=CircuitBreaker(failure_threshold=1, cool_down=30.0, clock=clk))
    reg.record_failure("src", SourceUnreachableError("down"))
    clk.advance(31.0)
    assert reg.state("src") == CircuitState.HALF_OPEN

    def bad_request():
        raise InvalidQueryError("400")

    with pytest.raises(InvalidQueryError):
        reg.call("src", bad_request)
    # 下一次调用必须真正透传到 fn（不是 fail-fast）
    assert reg.call("src", lambda: "ok") == "ok"


# ── #1714-2: 物化路径接入熔断器 ─────────────────────────────────────────────


class _ProfileAdapter:
    """带 profile.id 的假适配器（query 行为由构造参数决定）。"""

    def __init__(self, source_id: str, result=None, error=None):
        self.profile = ConnectionProfile(id=source_id, source_type="postgis")
        self._result = result
        self._error = error
        self.calls = 0

    def query(self, dataset_id, query_spec):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


def _ok_result(dataset_id: str = "ds1") -> QueryResult:
    return QueryResult(
        dataset_id=dataset_id,
        features=[
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
                "properties": {"id": 1},
            }
        ],
        total_count=1,
    )


@pytest.mark.asyncio
async def test_materialize_path_fails_fast_when_breaker_open(clean_breaker):
    """源故障期间 materialize 走满 adapter 超时的反面：熔断打开后 fail-fast。"""
    adapter = _ProfileAdapter("src1714a", error=SourceUnreachableError("connection refused"))

    res1 = await mat_svc.materialization_service.materialize_dataset(
        adapter, "ds1", QuerySpec(limit=5), session_id="s1"
    )
    assert res1["success"] is False
    assert res1["error_type"] == "SOURCE_UNREACHABLE"
    assert adapter.calls == 1

    res2 = await mat_svc.materialization_service.materialize_dataset(
        adapter, "ds1", QuerySpec(limit=5), session_id="s1"
    )
    assert res2["success"] is False
    assert res2["error_type"] == "SOURCE_UNREACHABLE"
    assert "circuit breaker" in res2["error"]
    assert adapter.calls == 1, "熔断打开后不得再碰 adapter（fail-fast）"


@pytest.mark.asyncio
async def test_materialize_path_counts_in_band_failure(clean_breaker):
    """in-band 失败标记（#766 形状）在物化路径同样计入熔断。"""
    adapter = _ProfileAdapter(
        "src1714b",
        result=QueryResult(
            dataset_id="ds1",
            features=[],
            total_count=0,
            metadata={"error_type": "SOURCE_BAD_RESPONSE", "error": "HTTP 503"},
        ),
    )

    res = await mat_svc.materialization_service.materialize_dataset(
        adapter, "ds1", QuerySpec(limit=5), session_id="s1"
    )
    assert res["success"] is False
    assert res["error_type"] == "SOURCE_BAD_RESPONSE"
    assert clean_breaker.state("src1714b") == CircuitState.OPEN


@pytest.mark.asyncio
async def test_materialize_path_success_stays_under_breaker(monkeypatch):
    """正常物化不受熔断器影响（success → CLOSED，ref 正常产出）。"""
    reg = CircuitBreakerRegistry(breaker=CircuitBreaker(failure_threshold=2, cool_down=30.0))
    set_breaker_registry(reg)
    try:
        adapter = _ProfileAdapter("src1714c", result=_ok_result())

        async def fake_store(session_id, data, prefix="data"):
            return "ref:data-fabric-ok1714"

        async def fake_set_alias(session_id, ref_id, alias):
            return None

        monkeypatch.setattr(mat_svc.session_data_manager, "store", fake_store)
        monkeypatch.setattr(mat_svc.session_data_manager, "set_alias", fake_set_alias)

        res = await mat_svc.materialization_service.materialize_dataset(
            adapter, "ds1", QuerySpec(limit=5), session_id="s1"
        )
        assert res["success"] is True
        assert res["ref_id"] == "ref:data-fabric-ok1714"
        assert reg.state("src1714c") == CircuitState.CLOSED
    finally:
        set_breaker_registry(CircuitBreakerRegistry())


# ── #1715-1: stats_api 有界读取 + 声明配额强制执行 ─────────────────────────


class _FakeStreamResponse:
    """最小 requests.Response 替身（bounded_get 只用到这几个面）。"""

    def __init__(self, body: bytes, status_code: int = 200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    def get(self, url, params=None, timeout=None, stream=False, headers=None,
            allow_redirects=True):
        self.calls.append(url)
        if self._exc is not None:
            raise self._exc
        return self._response


def _stats_adapter(options) -> StatsApiAdapter:
    profile = ConnectionProfile(
        id="ovp", source_type="stats_api",
        endpoint_url="https://overpass-api.de/api", options=options,
    )
    return StatsApiAdapter(profile)


_DECL = {"datasets": [{"dataset_id": "osm", "path": "x", "items_path": "rows"}]}


def test_stats_api_enforces_declared_byte_cap():
    """源声明的 max_bytes_per_request 必须强制执行（超限 → typed error）。"""
    adapter = _stats_adapter({**_DECL, "max_bytes_per_request": 8})
    adapter._session = _FakeSession(_FakeStreamResponse(b'{"rows":[{"id":1}]}'))

    with pytest.raises(SourceBadResponseError) as ei:
        adapter._fetch_json("https://overpass-api.de/api/x")
    assert "cap" in str(ei.value)


def test_stats_api_reads_within_declared_cap():
    """配额内响应正常解析（流式通道不改变正常语义）。"""
    adapter = _stats_adapter({**_DECL, "max_bytes_per_request": 4096})
    adapter._session = _FakeSession(_FakeStreamResponse(b'{"rows":[{"id":1}]}'))

    assert adapter._fetch_json("https://overpass-api.de/api/x") == {"rows": [{"id": 1}]}


def test_stats_api_http_error_stays_typed():
    """非 200 → typed SourceBadResponseError（状态码进 details，契约不变）。"""
    adapter = _stats_adapter({**_DECL, "max_bytes_per_request": 4096})
    resp = _FakeStreamResponse(b"", status_code=503)
    adapter._session = _FakeSession(exc=requests.HTTPError("503", response=resp))

    with pytest.raises(SourceBadResponseError) as ei:
        adapter._fetch_json("https://overpass-api.de/api/x")
    assert "503" in str(ei.value)


def test_stats_api_undeclared_quota_falls_back_to_default_cap():
    """未声明配额 → None = 走公共读取通道的默认硬界（绝非无上限）。"""
    adapter = _stats_adapter(dict(_DECL))
    assert adapter._max_bytes_per_request() is None
    # 非法声明值同样回落默认界（绝不因配置错误放开上限）
    assert _stats_adapter({**_DECL, "max_bytes_per_request": "abc"})._max_bytes_per_request() is None
    assert _stats_adapter({**_DECL, "max_bytes_per_request": 0})._max_bytes_per_request() is None


def test_source_registry_profile_carries_declared_byte_quota():
    """quota.max_bytes_per_request 经 fabric_profile 下传进适配器 options。"""
    src = SourceDefinition(
        source_id="abc_def", name="x", protocol="stats_api",
        endpoint="https://example.com/api",
        quota=QuotaDecl(max_bytes_per_request=536870912),
    )
    assert src.fabric_profile()["options"]["max_bytes_per_request"] == 536870912

    # 显式 options 值优先（setdefault 语义）
    src_explicit = SourceDefinition(
        source_id="abc_def", name="x", protocol="stats_api",
        endpoint="https://example.com/api",
        quota=QuotaDecl(max_bytes_per_request=536870912),
        options={"max_bytes_per_request": 1024},
    )
    assert src_explicit.fabric_profile()["options"]["max_bytes_per_request"] == 1024

    # 未声明 → 不下传（适配器走默认硬界）
    src_none = SourceDefinition(
        source_id="abc_def", name="x", protocol="stats_api",
        endpoint="https://example.com/api",
    )
    assert "max_bytes_per_request" not in src_none.fabric_profile()["options"]


def test_stats_api_query_end_to_end_under_cap():
    """端到端：声明配额内 query() 正常产出 features（通道替换不破坏语义）。"""
    adapter = _stats_adapter({
        **_DECL,
        "max_bytes_per_request": 4096,
        "datasets": [
            {"dataset_id": "osm", "path": "x", "items_path": "rows",
             "lat_field": "lat", "lon_field": "lon"},
        ],
    })
    body = b'{"rows":[{"id":7,"lat":1.5,"lon":2.5}]}'
    adapter._session = _FakeSession(_FakeStreamResponse(body))

    result = adapter.query("osm", QuerySpec(limit=10))
    assert len(result.features) == 1
    assert result.features[0]["geometry"] == {"type": "Point", "coordinates": [2.5, 1.5]}


# ── #1715-2: 连接/目录有界 + 级联清理 ──────────────────────────────────────


class _CatalogRegisteringAdapter:
    """connect() 期间把描述符注册进目录（对齐真实 adapter.sync 语义）。"""

    def __init__(self, profile: ConnectionProfile):
        self.profile = profile

    def sync(self, owner=None):
        cm_mod.spatial_catalog_service.register_dataset(
            DatasetDescriptor(id=f"{self.profile.id}:{owner or 'global'}", source_type="postgis"),
            profile_id=self.profile.id, owner=owner,
        )
        return {"count": 1}


@pytest.fixture()
def isolated_catalog(monkeypatch):
    svc = SpatialCatalogService()
    monkeypatch.setattr(cm_mod, "spatial_catalog_service", svc)
    monkeypatch.setattr(cm_mod, "create_adapter_for_profile", _CatalogRegisteringAdapter)
    return svc


def _profile(pid: str) -> ConnectionProfile:
    return ConnectionProfile(id=pid, source_type="postgis", url="")


def test_connection_manager_lru_evicts_and_cleans_catalog(isolated_catalog):
    """超出容量的最旧连接被 LRU 逐出，其目录足迹级联回收。"""
    mgr = DataFabricConnectionManager(max_entries=2, idle_ttl_s=0.0)
    for pid in ("p1", "p2", "p3"):
        mgr.connect(_profile(pid), owner="sess")

    assert mgr.stats()["entries"] == 2
    assert mgr.get_adapter("p1", owner="sess") is None, "最旧连接应被 LRU 逐出"
    assert mgr.get_adapter("p2", owner="sess") is not None
    assert mgr.get_adapter("p3", owner="sess") is not None
    # 逐出的 p1 目录足迹被回收；存活连接的不动
    assert isolated_catalog.get_profile_id("p1:sess", owner="sess") is None
    assert isolated_catalog.get_profile_id("p3:sess", owner="sess") is not None


def test_connection_manager_idle_ttl_expires_connection(isolated_catalog):
    """idle 超限的连接在访问路径被清扫（可注入时钟，确定性）。"""
    clk = _FakeClock()
    mgr = DataFabricConnectionManager(max_entries=8, idle_ttl_s=30.0, clock=clk)
    mgr.connect(_profile("p1"), owner="sess")
    assert mgr.get_adapter("p1", owner="sess") is not None

    clk.advance(31.0)
    assert mgr.get_adapter("p1", owner="sess") is None
    assert mgr.stats()["entries"] == 0
    assert isolated_catalog.get_profile_id("p1:sess", owner="sess") is None, "过期连接目录足迹应回收"


def test_connection_manager_disconnect_cascades_catalog_cleanup(isolated_catalog):
    """disconnect 级联清理目录足迹；同 profile 的其它会话连接不受误删。"""
    mgr = DataFabricConnectionManager(max_entries=8, idle_ttl_s=0.0)
    mgr.connect(_profile("shared"), owner="alice")
    mgr.connect(_profile("shared"), owner="bob")
    assert isolated_catalog.get_dataset("shared:alice") is not None
    assert isolated_catalog.get_dataset("shared:bob") is not None

    # alice 断开时 bob 仍持有该 profile → 只清 alice 注册的条目
    assert mgr.disconnect("shared", owner="alice") is True
    assert isolated_catalog.get_dataset("shared:alice") is None
    assert isolated_catalog.get_dataset("shared:bob") is not None
    assert mgr.get_adapter("shared", owner="bob") is not None

    # 最后一个连接断开 → 该 profile 的目录足迹全部回收
    assert mgr.disconnect("shared", owner="bob") is True
    assert isolated_catalog.get_dataset("shared:bob") is None
    assert mgr.stats()["entries"] == 0
    assert mgr.disconnect("shared", owner="bob") is False


def test_connection_manager_clear_reclaims_only_own_profiles(isolated_catalog):
    """clear() 回收本管理器连接过的 profile 足迹（未连接过的 profile 不动）。"""
    isolated_catalog.register_dataset(
        DatasetDescriptor(id="foreign:ds", source_type="postgis"),
        profile_id="foreign", owner="sess",
    )
    mgr = DataFabricConnectionManager(max_entries=8, idle_ttl_s=0.0)
    mgr.connect(_profile("mine"), owner="sess")

    mgr.clear()
    assert mgr.stats()["entries"] == 0
    assert isolated_catalog.get_dataset("mine:sess") is None
    assert isolated_catalog.get_dataset("foreign:ds") is not None


def test_spatial_catalog_lru_evicts_oldest():
    """目录超容按 LRU 逐出；重复注册刷新 LRU 序。"""
    svc = SpatialCatalogService(max_entries=2)
    for did in ("a", "b", "c"):
        svc.register_dataset(DatasetDescriptor(id=did, source_type="postgis"), profile_id="p")
    assert svc.get_dataset("a") is None
    assert svc.get_dataset("b") is not None and svc.get_dataset("c") is not None

    svc.register_dataset(DatasetDescriptor(id="b", source_type="postgis"), profile_id="p")  # 刷新
    svc.register_dataset(DatasetDescriptor(id="d", source_type="postgis"), profile_id="p")
    assert svc.get_dataset("b") is not None, "重复注册刷新 LRU 序"
    assert svc.get_dataset("c") is None, "最旧未刷新条目被逐出"


def test_catalog_unregister_by_profile_is_owner_precise():
    """unregister_by_profile：显式 owner 精确清理；缺省不限 owner。"""
    svc = SpatialCatalogService()
    svc.register_dataset(DatasetDescriptor(id="g", source_type="postgis"), profile_id="p", owner=None)
    svc.register_dataset(DatasetDescriptor(id="a", source_type="postgis"), profile_id="p", owner="alice")
    svc.register_dataset(DatasetDescriptor(id="b", source_type="postgis"), profile_id="p", owner="bob")
    svc.register_dataset(DatasetDescriptor(id="other", source_type="postgis"), profile_id="q", owner="alice")

    assert svc.unregister_by_profile("p", owner="alice") == 1
    assert svc.get_dataset("a") is None
    assert svc.get_dataset("g") is not None and svc.get_dataset("b") is not None
    assert svc.get_dataset("other") is not None, "别的 profile 不受影响"

    assert svc.unregister_by_profile("p") == 2  # 不限 owner：g + b
    assert svc.get_dataset("g") is None and svc.get_dataset("b") is None
    assert svc.get_dataset("other") is not None
    assert svc.unregister_by_profile("p") == 0  # 幂等
