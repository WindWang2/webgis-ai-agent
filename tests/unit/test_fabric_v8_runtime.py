"""FabricRuntime V8（ADR-0130）：生产单一解析路径的单元测试。

覆盖：
- registry 优先解析（连接即治理；同一 adapter 实例复用）；
- legacy 会话回退 + 首次命中即注册（治理视图统一）；
- profile-only 旧条目补建语义（原 inspect miss 路径）；
- record→adapter 重建（redacted profile 保真：options 不丢）；
- secret 永不进 record（redacted_profile/endpoint_ref/序列化面）；
- 作用域隔离（owner 互不可见；全局域对会话可见）；
- manager ``_governed_adapter``：registry 故障回退既有工厂构建。
"""
import pytest

from app.services.data_fabric.connection_manager import connection_manager
from app.services.data_fabric.fabric.connection_registry import (
    ConnectionRecord,
    TenantScope,
    get_connection_registry,
    reset_connection_registry,
)
from app.services.data_fabric.fabric.runtime import (
    get_fabric_runtime,
    reset_fabric_runtime,
)
from app.schemas.data_fabric_schema import ConnectionProfile


@pytest.fixture(autouse=True)
def _isolated_governance():
    reset_connection_registry()
    reset_fabric_runtime()
    connection_manager.clear()
    yield
    reset_connection_registry()
    reset_fabric_runtime()
    connection_manager.clear()


def _make_profile(pid="rt_src", source_type="generic", **kw):
    return ConnectionProfile(
        id=pid,
        name=f"Runtime {pid}",
        source_type=source_type,
        url=kw.pop("url", "https://gis.example.com/api"),
        password=kw.pop("password", None),
        options=kw.pop("options", {}),
        **kw,
    )


# ── resolve：registry 优先 ────────────────────────────────────────────


def test_resolve_after_attach_reuses_governed_adapter():
    registry = get_connection_registry()
    profile = _make_profile(password="s3cret!")
    record, adapter = registry.attach(profile, TenantScope(owner="sess-1"))

    resolved = get_fabric_runtime().resolve("rt_src", owner="sess-1")
    assert resolved is not None
    assert resolved.adapter is adapter  # 同一实例（不重复构建）
    assert resolved.governed
    assert resolved.revision == record.revision
    assert resolved.source_type == "generic"
    assert resolved.scope_key == TenantScope(owner="sess-1").scope_key()


def test_record_never_carries_plaintext_secret():
    registry = get_connection_registry()
    registry.attach(_make_profile(password="s3cret!"), TenantScope(owner="s"))

    record = registry.peek("rt_src", TenantScope(owner="s"))
    assert record is not None
    dumped = record.model_dump()
    assert "s3cret!" not in str(dumped)
    assert dumped.get("password") is None
    assert dumped["redacted_profile"].get("password") is None
    # endpoint_ref 是 redacted 视图（无 userinfo 凭证）。
    assert "s3cret!" not in (record.endpoint_ref or "")


# ── resolve：legacy 回退 + 治理注册 ───────────────────────────────────


def test_legacy_session_hit_registers_into_registry():
    profile = _make_profile("legacy_src")
    _legacy_adapter, legacy_adapter_inst = connection_manager.connect(
        profile, owner="sess-2"
    )

    resolved = get_fabric_runtime().resolve("legacy_src", owner="sess-2")
    assert resolved is not None
    assert resolved.adapter is legacy_adapter_inst  # 复用 legacy 实例（单构建）
    assert resolved.governed  # 首次命中即受治理
    record = get_connection_registry().peek("legacy_src", TenantScope(owner="sess-2"))
    assert record is not None
    assert record.redacted_profile.get("password") is None


def test_legacy_profile_only_entry_is_rebuilt_and_governed():
    # 旧 inspect miss 路径语义：会话里只有 profile 没有 adapter。
    profile = _make_profile("profile_only")
    connection_manager.connect(profile, owner="sess-3")
    connection_manager._adapters.pop(("sess-3", "profile_only"), None)

    resolved = get_fabric_runtime().resolve("profile_only", owner="sess-3")
    assert resolved is not None
    assert resolved.adapter is not None
    assert resolved.governed


def test_resolve_unknown_returns_none():
    assert get_fabric_runtime().resolve("never_connected", owner="sess-x") is None


def test_scope_isolation_and_global_visibility():
    runtime = get_fabric_runtime()
    registry = get_connection_registry()
    # owner 域连接：其他 owner 不可见。
    registry.attach(_make_profile("scoped"), TenantScope(owner="alice"))
    assert runtime.resolve("scoped", owner="bob") is None
    assert runtime.resolve("scoped", owner="alice") is not None
    # 全局域连接：对会话可见（V5 legacy 语义）。
    registry.attach(_make_profile("global_src"), TenantScope())
    assert runtime.resolve("global_src", owner="bob") is not None


# ── record → adapter 重建（redacted profile 保真）─────────────────────


def test_ensure_adapter_rebuilds_with_options_intact():
    registry = get_connection_registry()
    profile = _make_profile(
        "opts_src",
        options={"datasets": [{"id": "layer_a", "feature_count": 3}]},
        password="pw123",
    )
    registry.attach(profile, TenantScope(owner="s"))
    # 模拟 LRU 驱逐：条目在、adapter 引用被丢弃。
    key = (TenantScope(owner="s").scope_key(), "opts_src")
    record, _ = registry._entries[key]
    registry._entries[key] = (record, None)

    rebuilt = registry.ensure_adapter(record)
    assert rebuilt is not None
    datasets = rebuilt.list_datasets()
    assert [d["id"] for d in datasets] == ["layer_a"]
    # 回填后 resolve 直接命中重建实例。
    resolved = get_fabric_runtime().resolve("opts_src", owner="s")
    assert resolved.adapter is rebuilt


def test_ensure_adapter_stale_record_returns_none():
    registry = get_connection_registry()
    registry.attach(_make_profile("stale_src"), TenantScope(owner="s"))
    ghost = ConnectionRecord(
        profile_id="stale_src",
        scope_key=TenantScope(owner="s").scope_key(),
        source_type="generic",
        revision="deadbeef00000000",
    )
    assert registry.ensure_adapter(ghost) is None


# ── manager._governed_adapter：回退契约 ───────────────────────────────


def test_governed_adapter_falls_back_to_factory_build():
    from types import SimpleNamespace

    from app.services.data_fabric.manager import DataFabricManager

    ds_model = SimpleNamespace(
        id="fallback_src",
        owner_id="owner-1",
        name="Fallback Src",
        source_type="generic",
        endpoint_url="https://gis.example.com/api",
        connection_profile={
            "options": {},
            "allow_private": False,
        },
    )
    adapter = DataFabricManager._governed_adapter(ds_model)
    assert adapter is not None
    # 回退路径构建的 adapter 可用（generic demo 语义）。
    assert adapter.list_datasets() is not None


def test_governed_adapter_uses_registry_when_governed():
    from types import SimpleNamespace

    from app.services.data_fabric.manager import DataFabricManager

    profile = _make_profile("gov_src", password="pw")
    _record, adapter = get_connection_registry().attach(
        profile, TenantScope(owner="owner-9")
    )
    ds_model = SimpleNamespace(id="gov_src", owner_id="owner-9")
    assert DataFabricManager._governed_adapter(ds_model) is adapter


# ── describe_source：诊断视图无 secret ────────────────────────────────


def test_describe_source_is_secret_free():
    runtime = get_fabric_runtime()
    registry = get_connection_registry()
    registry.attach(
        _make_profile("diag_src", url="https://user:topsecret@example.com/api"),
        TenantScope(owner="s"),
    )
    resolved = runtime.resolve("diag_src", owner="s")
    view = runtime.describe_source(resolved)
    assert "topsecret" not in str(view)
    assert view["governed"] is True
    assert "@" not in str(view.get("endpoint_ref", ""))


# ── V8 自审修复回归：redacted_profile 凭证零明文 + 重建保真 ──────────


def test_dsn_userinfo_never_enters_record():
    registry = get_connection_registry()
    profile = _make_profile(
        "dsn_src",
        url="postgresql://pguser:pgpass123@db.example.com:5432/gisdb",
    )
    profile.model_post_init(None)  # DSN 凭证解析进结构化字段（url 仍含原文）
    registry.attach(profile, TenantScope(owner="s"))

    record = registry.peek("dsn_src", TenantScope(owner="s"))
    assert record is not None
    flat = str(record.model_dump())
    assert "pgpass123" not in flat
    assert "pguser:pgpass123" not in flat
    # URL 其余部分保真（redact_url 只摘 userinfo）。
    assert "db.example.com:5432/gisdb" in record.redacted_profile["url"]


def test_options_password_extracted_and_rehydrated_faithfully():
    registry = get_connection_registry()
    profile = _make_profile(
        "opt_src",
        options={"sslmode": "require", "password": "opt-secret-9",
                 "nested": {"api_key": "key-7"}},
    )
    registry.attach(profile, TenantScope(owner="s"))

    record = registry.peek("opt_src", TenantScope(owner="s"))
    flat = str(record.model_dump())
    assert "opt-secret-9" not in flat
    assert "key-7" not in flat

    # 重建：SecretStore 深合并回填 → adapter 构建拿到真实凭证（保真）。
    rebuilt_profile = ConnectionProfile(**registry.rehydrate_profile(record))
    assert rebuilt_profile.options["password"] == "opt-secret-9"
    assert rebuilt_profile.options["nested"]["api_key"] == "key-7"
    assert rebuilt_profile.options["sslmode"] == "require"


# ── V8 自审修复回归：manager 路径 DB-only 源首用即治理 ────────────────


def test_governed_adapter_attaches_db_source_on_first_use():
    from types import SimpleNamespace as _NS

    from app.services.data_fabric.manager import DataFabricManager

    ds = _NS(
        id="db_src", owner_id="owner-db", name="DB Src",
        source_type="generic", endpoint_url="https://gis.example.com/api",
        connection_profile={"options": {}, "allow_private": False},
    )
    adapter = DataFabricManager._governed_adapter(ds)
    assert adapter is not None
    # 首用即治理：registry 按**行归属域**作用域出现 record。
    record = get_connection_registry().peek("db_src", TenantScope(owner="owner-db"))
    assert record is not None and record.revision
    # 二次解析复用同一受治理 adapter（不再重复构建）。
    assert DataFabricManager._governed_adapter(ds) is adapter
