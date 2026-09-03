"""Wave K 安全/可靠性回归（ADR-0094 §10）。

- C4：工具面 connection_manager / spatial_catalog 的 owner（会话）作用域——
  跨会话不可见、全局条目可见、并发安全。
- M1：breaker half-open trial 不再被健康缓存命中泄漏。
- M7：ref_payload_cache 的 invalidate-during-build 防复活。
- F-2：profile 脱敏覆盖 Authorization/x-api-key/passwd/pwd/private_key。
- F-5：s3/minio scheme 的 endpoint 主机过 SSRF 门。
"""
import threading

import pytest

from app.schemas.data_fabric_schema import DatasetDescriptor
from app.services.data_fabric.spatial_catalog import SpatialCatalogService


# ── C4：catalog owner 作用域 ────────────────────────────────────────────────


def test_catalog_owner_isolation():
    svc = SpatialCatalogService()
    svc.register_dataset(DatasetDescriptor(id="pub", source_type="postgis"), owner=None)
    svc.register_dataset(DatasetDescriptor(id="alice_ds", source_type="postgis"), owner="alice")
    svc.register_dataset(DatasetDescriptor(id="bob_ds", source_type="postgis"), owner="bob")

    assert svc.get_dataset("alice_ds", owner="alice") is not None
    assert svc.get_dataset("bob_ds", owner="alice") is None, "跨会话数据集不可见"
    assert svc.get_dataset("bob_ds", owner="bob") is not None
    assert svc.get_dataset("pub", owner="alice") is not None, "全局条目对会话可见"
    assert svc.get_profile_id("bob_ds", owner="alice") is None

    alice_view = {d.id for d in svc.list_datasets(owner="alice")}
    assert alice_view == {"pub", "alice_ds"}

    res_alice = svc.search(owner="alice")
    assert {i["id"] for i in res_alice["items"]} == {"pub", "alice_ds"}
    res_legacy = svc.search()
    assert len(res_legacy["items"]) == 3, "legacy（无 owner）视图保持全部"


def test_catalog_concurrent_register_search():
    """并发注册/搜索不再触发 dict-mutation race（审计 C4）。"""
    svc = SpatialCatalogService()
    errors = []

    def _writer(n: int):
        try:
            for i in range(200):
                svc.register_dataset(
                    DatasetDescriptor(id=f"w{n}_{i}", source_type="postgis"),
                    owner=f"sess{n}",
                )
        except Exception as e:  # pragma: no cover
            errors.append(e)

    def _reader():
        try:
            for _ in range(200):
                svc.search(owner="sess0")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=_writer, args=(n,)) for n in range(4)]
    threads.append(threading.Thread(target=_reader))
    threads.append(threading.Thread(target=_reader))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


# ── C4：connection_manager owner 作用域 ─────────────────────────────────────


def test_connection_manager_owner_isolation(monkeypatch):
    import app.services.data_fabric.connection_manager as cm_mod
    from app.services.data_fabric.connection_manager import DataFabricConnectionManager

    class _FakeAdapter:
        def sync(self, owner=None):
            return {"count": 0}

    monkeypatch.setattr(cm_mod, "create_adapter_for_profile", lambda profile: _FakeAdapter())
    from app.schemas.data_fabric_schema import ConnectionProfile

    mgr2 = DataFabricConnectionManager()
    p_alice = ConnectionProfile(id="pg1", source_type="postgis", url="")
    p_bob = ConnectionProfile(id="pg1", source_type="postgis", url="")
    mgr2.connect(p_alice, owner="alice")
    mgr2.connect(p_bob, owner="bob")

    assert mgr2.get_adapter("pg1", owner="alice") is not None
    assert mgr2.get_adapter("pg1", owner="bob") is not None
    # 会话之间：同 profile_id 不互相覆盖（各自实例）
    a = mgr2.get_adapter("pg1", owner="alice")
    b = mgr2.get_adapter("pg1", owner="bob")
    assert a is not b
    # carol 看不到 alice/bob 的连接（也无可回退的全局 pg1）
    assert mgr2.get_adapter("pg1", owner="carol") is None
    # 全局（legacy）连接对会话可见
    mgr2.connect(ConnectionProfile(id="shared", source_type="postgis", url=""), owner=None)
    assert mgr2.get_adapter("shared", owner="anyone") is not None


# ── M1：half-open trial 不泄漏 ──────────────────────────────────────────────


def test_health_cache_hit_does_not_leak_halfopen_trial(monkeypatch):
    """allow() 在缓存命中路径之后才请求 → 缓存命中不占用 trial 名额。"""
    from app.services.data_fabric.circuit_breaker import (
        CircuitBreaker,
        CircuitBreakerRegistry,
    )
    from app.services.data_fabric.health import DataFabricHealthCheck
    from app.services.data_fabric.query.predicates import PredicateError  # noqa: F401

    reg = CircuitBreakerRegistry(breaker=CircuitBreaker(failure_threshold=2, cool_down=0.0))
    monkeypatch.setattr(
        "app.services.data_fabric.circuit_breaker.get_breaker_registry", lambda: reg
    )
    # 手动把 breaker 置 HALF_OPEN（opened_at 已过 cool_down=0）
    entry = reg._entry("src_x")
    from app.services.data_fabric.circuit_breaker import CircuitState

    entry.state = CircuitState.OPEN
    entry.opened_at = -1.0

    from app.schemas.data_fabric_schema import DataFabricHealth
    from app.services.data_fabric.health import _CachedHealth

    check = DataFabricHealthCheck()
    healthy = DataFabricHealth(status="healthy", message="cached")
    # 预置缓存命中（真实 _CachedHealth 形态）
    check._cache["src_x"] = _CachedHealth(health=healthy, cached_at=check._clock())

    class _Adapter:
        class profile:
            id = "src_x"
            url = ""
            allow_private = False

        def health(self):  # pragma: no cover - 不应被调用
            raise AssertionError("cache hit must not probe")

    # 缓存命中路径：不得占用 trial（此后真实请求仍能通过 half-open）
    check.check_health(_Adapter(), use_cache=True, source_key="src_x")
    assert reg.allow("src_x") is True, "缓存命中不得泄漏 half-open trial 名额"


def test_breaker_concurrent_failure_counting(monkeypatch):
    """并发 record_failure 不丢失更新（阈值=2，8 线程各记 1 次必须打开）。"""
    from app.services.data_fabric.circuit_breaker import (
        CircuitBreaker,
        CircuitBreakerRegistry,
        CircuitState,
    )
    from app.services.data_fabric.errors import SourceUnreachableError

    reg = CircuitBreakerRegistry(breaker=CircuitBreaker(failure_threshold=2, cool_down=30.0))
    barrier = threading.Barrier(8)

    def _fail():
        barrier.wait()
        reg.record_failure("src_y", SourceUnreachableError("down"))

    threads = [threading.Thread(target=_fail) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert reg.state("src_y") == CircuitState.OPEN


# ── M7：ref_payload_cache 防复活 ────────────────────────────────────────────


def test_ref_payload_cache_put_if_current_epoch_guard():
    from app.services.ref_payload_cache import RefPayloadCache

    cache = RefPayloadCache(ttl=60.0)
    epoch = cache.current_epoch("s", "r1")
    cache.invalidate("s", "r1")  # 构建期间发生 overwrite/delete
    assert cache.put_if_current("s", "r1", {"stale": True}, 100, epoch) is False
    assert cache.get("s", "r1") is None, "被失效的构建结果不得复活"

    epoch2 = cache.current_epoch("s", "r1")
    assert cache.put_if_current("s", "r1", {"fresh": True}, 100, epoch2) is True
    assert cache.get("s", "r1") == {"fresh": True}


# ── F-2 / F-5：脱敏与 s3 SSRF ──────────────────────────────────────────────


def test_sanitize_covers_auth_header_variants():
    from app.services.data_fabric.security import DataFabricSecurity

    out = DataFabricSecurity.sanitize_profile_dict({
        "options": {"headers": {"Authorization": "Bearer x", "x-api-key": "k"}},
        "passwd": "p", "pwd": "p", "private_key": "k", "apikey": "k",
        "normal": "v",
    })
    assert out["options"]["headers"]["Authorization"] == "********"
    assert out["options"]["headers"]["x-api-key"] == "********"
    assert out["passwd"] == "********" and out["pwd"] == "********"
    assert out["private_key"] == "********" and out["apikey"] == "********"
    assert out["normal"] == "v"


def test_s3_scheme_endpoint_ssrf_gate():
    from app.services.data_fabric.security import (
        DataFabricSecurity,
        DataFabricSecurityError,
    )

    with pytest.raises(DataFabricSecurityError):
        DataFabricSecurity.validate_url("s3://169.254.169.254/bucket")
    with pytest.raises(DataFabricSecurityError):
        DataFabricSecurity.validate_url("minio://127.0.0.1:9000/bucket")
    # bucket 名不受影响
    DataFabricSecurity.validate_url("s3://my-geo-bucket/key.parquet")
