"""Connection Registry V7 测试（ADR-0119 W1-2）。

覆盖 Epic 03 Must-have A 的验收面：
- owner/org/project 作用域隔离 + 显式全局域回退可见性；
- content-addressed revision 稳定性 + CAS 更新冲突；
- secret 分离（record 无明文；SecretStore 注回；幂等 attach 不泄漏重复条目）；
- 过期语义（typed ConnectionExpiredError + adapter 引用丢弃）；
- 生命周期（idle TTL 驱逐、容量上限、sweep）；
- 健康状态机；
- P1 回归：从 DB 行重建 ConnectionProfile 恢复凭证字段。
"""

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.fabric.connection_registry import (
    ConnectionExpiredError,
    ConnectionRegistry,
    InMemorySecretStore,
    ScopeViolationError,
    TenantScope,
    extract_profile_secrets,
    get_connection_registry,
    profile_revision,
    reset_connection_registry,
)
from app.services.data_fabric.manager import _profile_from_model


def _profile(pid="ds_test1", with_secret=True, source_type="generic"):
    return ConnectionProfile(
        id=pid,
        name="test source",
        source_type=source_type,
        url="https://example.com/geoserver",
        **({"password": "s3cret!", "credentials": {"token": "t"}} if with_secret else {}),
    )


def _scoped_registry(**kw):
    return ConnectionRegistry(secret_store=InMemorySecretStore(ttl_s=3600), **kw)


# ── 作用域 ──────────────────────────────────────────────────────────────


def test_scope_key_components_are_unambiguous():
    a = TenantScope(org_id=1, owner="u_a", project_id="p")
    b = TenantScope(org_id=1, owner="u_a_p")
    assert a.scope_key() != b.scope_key()
    assert TenantScope().is_global
    with pytest.raises(ValueError):
        TenantScope(owner="_")  # 哨兵碰撞防御


def test_cross_owner_isolation():
    reg = _scoped_registry()
    reg.attach(_profile(), TenantScope(owner="alice"))
    assert reg.resolve("ds_test1", TenantScope(owner="alice")) is not None
    assert reg.resolve("ds_test1", TenantScope(owner="bob")) is None
    assert reg.resolve("ds_test1", TenantScope(owner="bob"), allow_global_fallback=False) is None


def test_global_fallback_visible_but_scoped_take_precedence():
    reg = _scoped_registry()
    reg.attach(_profile(source_type="generic"), TenantScope())  # 全局域
    adapter_global = reg.resolve("ds_test1", TenantScope(owner="alice"))
    assert adapter_global is not None  # legacy 可见性保留
    reg.attach(_profile(with_secret=False), TenantScope(owner="alice"))
    adapter_scoped = reg.resolve("ds_test1", TenantScope(owner="alice"))
    assert adapter_scoped is not adapter_global


# ── revision / CAS ──────────────────────────────────────────────────────


def test_revision_stable_and_sensitive_to_change():
    red = {"id": "x", "url": "https://example.com"}
    r1 = profile_revision(red, None)
    assert r1 == profile_revision(dict(red), None)
    assert r1 != profile_revision({**red, "url": "https://other.example.com"}, None)
    assert r1 != profile_revision(red, "sec_abc")


def test_attach_idempotent_same_revision():
    reg = _scoped_registry()
    scope = TenantScope(owner="alice")
    rec1, _ = reg.attach(_profile(), scope)
    n_secrets = len(reg._secret_store)
    rec2, _ = reg.attach(_profile(), scope)
    assert rec1.revision == rec2.revision
    assert len(reg._secret_store) == n_secrets  # 幂等：不重复存 secret


def test_update_cas_conflict_and_success():
    reg = _scoped_registry()
    scope = TenantScope(owner="alice")
    rec, _ = reg.attach(_profile(), scope)
    with pytest.raises(ScopeViolationError):
        reg.update(_profile(with_secret=False), scope, expected_revision="bogus")
    rec2, _ = reg.update(_profile(with_secret=False), scope, expected_revision=rec.revision)
    assert rec2.revision != rec.revision


# ── secret 分离 ─────────────────────────────────────────────────────────


def test_record_carries_no_plaintext_secret():
    reg = _scoped_registry()
    scope = TenantScope(owner="alice")
    rec, _adapter = reg.attach(_profile(), scope)
    dumped = rec.model_dump()
    assert "s3cret!" not in str(dumped)
    assert rec.secret_ref and rec.secret_ref.startswith("sec_")
    # 注回路径
    restored = reg.rehydrate_profile(rec)
    assert restored["password"] == "s3cret!"


def test_extract_profile_secrets_splits_known_keys():
    rest, secret = extract_profile_secrets(
        {"id": "x", "password": "p", "access_key": "a", "credentials": {"t": 1}, "name": "n"}
    )
    assert secret == {"password": "p", "access_key": "a", "credentials": {"t": 1}}
    assert rest["name"] == "n" and "password" not in rest


def test_revoke_evicts_secret():
    reg = _scoped_registry()
    scope = TenantScope(owner="alice")
    rec, _ = reg.attach(_profile(), scope)
    assert reg.revoke("ds_test1", scope)
    assert reg._secret_store.get(rec.secret_ref) is None
    assert reg.resolve("ds_test1", scope) is None


# ── 过期 / 生命周期 ──────────────────────────────────────────────────────


def test_expired_connection_raises_typed_and_drops_adapter():
    reg = _scoped_registry()
    scope = TenantScope(owner="alice")
    reg.attach(_profile(), scope, ttl_s=0.05)
    import time

    time.sleep(0.06)
    with pytest.raises(ConnectionExpiredError):
        reg.resolve("ds_test1", scope)
    rec = reg.peek("ds_test1", scope)
    assert rec.health == "expired"
    assert rec.is_expired()


def test_capacity_bound_evicts_lru():
    reg = _scoped_registry(max_entries=2)
    s = TenantScope(owner="alice")
    reg.attach(_profile(pid="ds_a"), s)
    reg.attach(_profile(pid="ds_b"), s)
    reg.resolve("ds_a", s)  # 刷新 a 的 LRU
    reg.attach(_profile(pid="ds_c"), s)  # b 被逐出
    assert reg.resolve("ds_b", s) is None
    assert reg.resolve("ds_a", s) is not None
    assert reg.resolve("ds_c", s) is not None


def test_sweep_evicts_idle():
    reg = _scoped_registry(idle_ttl_s=0.05)
    s = TenantScope(owner="alice")
    rec, _ = reg.attach(_profile(), s)
    import time

    time.sleep(0.06)
    removed = reg.sweep()
    assert removed == 1
    assert reg.resolve("ds_test1", s) is None


# ── 健康状态机 ──────────────────────────────────────────────────────────


def test_health_state_machine():
    reg = _scoped_registry()
    s = TenantScope(owner="alice")
    reg.attach(_profile(), s)
    assert reg.record_health("ds_test1", s, "healthy")
    assert reg.peek("ds_test1", s).health == "healthy"
    with pytest.raises(ValueError):
        reg.record_health("ds_test1", s, "banana")
    assert reg.record_health("ghost", s, "healthy") is False


# ── P1 回归：DB 行重建恢复凭证 ──────────────────────────────────────────


class _FakeModel:
    """模拟 DataSourceModel 行（不依赖 DB）。"""

    def __init__(self, stored):
        self.id = "ds_x"
        self.name = "x"
        self.source_type = "postgis"
        self.endpoint_url = "postgresql://u:p@db.example.com/gis"
        self.connection_profile = stored


def test_profile_from_model_restores_credentials():
    stored = {
        "id": "ds_x",
        "name": "x",
        "source_type": "postgis",
        "url": "postgresql://u:p@db.example.com/gis",
        "options": {"schema": "public"},
        "allow_private": False,
        "username": "gis_user",
        "password": "gis_pass",
        "credentials": {"token": "tk"},
    }
    profile = _profile_from_model(_FakeModel(stored))
    assert profile.username == "gis_user"
    assert profile.password == "gis_pass"
    assert profile.credentials == {"token": "tk"}
    assert profile.options == {"schema": "public"}


def test_profile_from_model_tolerates_sparse_rows():
    profile = _profile_from_model(_FakeModel({"options": {}, "allow_private": False}))
    # 无结构化凭证字段：DSN 内嵌凭证经 model_post_init 解析（既有语义）。
    assert profile.username == "u"
    assert profile.password == "p"
    assert profile.url == "postgresql://u:p@db.example.com/gis"


# ── 单例 ────────────────────────────────────────────────────────────────


def test_process_singleton_reset():
    reset_connection_registry()
    r1 = get_connection_registry()
    assert get_connection_registry() is r1
    reset_connection_registry()
    assert get_connection_registry() is not r1
