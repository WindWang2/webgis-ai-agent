"""SecretProvider / credential lease 对抗测试(H05)。

覆盖:hermetic 适配器 / typed miss(不抛)/ 过期与吊销 / LRU 有界 /
伪造句柄拒绝 / **redaction 性质测试**(随机 material 绝不出现在 lease
的 repr/str/to_dict)。
"""
from __future__ import annotations

import random
import string
import time

import pytest

from app.lib.secret_lease import (
    DEFAULT_LEASE_TTL_S,
    LEASE_CREDENTIAL_MISSING,
    LEASE_PROVIDER_FAILED,
    MAX_ACTIVE_LEASES,
    InMemorySecretProvider,
    SecretLease,
    SecretLeaseManager,
    bind_secret_leases,
    current_secret_leases,
    get_secret_lease_manager,
    resolve_secret_lease,
    secret_digest,
    set_secret_provider,
)


def _rand_material(rng: random.Random, n: int) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(rng.choice(alphabet) for _ in range(n))


class TestProviders:
    def test_in_memory_roundtrip_and_revoke(self):
        p = InMemorySecretProvider({"smtp": "s3cret"})
        assert p.resolve_material("smtp") == "s3cret"
        p.remove("smtp")  # 凭证撤销故障注入
        assert p.resolve_material("smtp") is None

    def test_env_provider_reads_namespaced_var(self, monkeypatch):
        from app.lib.secret_lease import EnvSecretProvider

        monkeypatch.setenv("GIS_TOOL_SECRET_SMTP", "env-secret")
        monkeypatch.setenv("GIS_TOOL_SECRET_MY_KEY_2", "env-secret-2")
        p = EnvSecretProvider()
        assert p.resolve_material("smtp") == "env-secret"
        assert p.resolve_material("my.key/2") == "env-secret-2"
        assert p.resolve_material("absent") is None
        assert p.resolve_material("") is None

    def test_env_provider_injected_dict_hermetic(self):
        from app.lib.secret_lease import EnvSecretProvider

        p = EnvSecretProvider(env={"GIS_TOOL_SECRET_K": "v"})
        assert p.resolve_material("k") == "v"
        assert p.resolve_material("missing") is None


class TestLeaseManager:
    def test_acquire_resolve_revoke(self):
        m = SecretLeaseManager(InMemorySecretProvider({"db": "pw"}))
        r = m.acquire("db", scope="turn-1")
        assert r.ok and r.reason_code == "ok"
        assert m.resolve(r.lease) == "pw"
        assert m.revoke(r.lease.lease_id) is True
        assert m.resolve(r.lease) is None
        assert m.revoke(r.lease.lease_id) is False  # 幂等

    def test_missing_credential_typed_not_raised(self):
        m = SecretLeaseManager(InMemorySecretProvider({}))
        r = m.acquire("nope")
        assert r.ok is False
        assert r.reason_code == LEASE_CREDENTIAL_MISSING
        assert r.lease is None

    def test_empty_id_typed_miss(self):
        m = SecretLeaseManager(InMemorySecretProvider({"": "x"}))
        assert m.acquire("").reason_code == LEASE_CREDENTIAL_MISSING

    def test_provider_exception_is_typed_failure(self):
        class Boom:
            def resolve_material(self, cid):
                raise RuntimeError("kms unreachable")

        m = SecretLeaseManager(Boom())
        r = m.acquire("db")
        assert r.ok is False
        assert r.reason_code == LEASE_PROVIDER_FAILED

    def test_no_provider_presence_only_world_unchanged(self):
        """零配置部署:acquire typed miss,绝不抛 —— F06 直通纪律。"""
        m = SecretLeaseManager(None)
        assert m.acquire("db").reason_code == LEASE_CREDENTIAL_MISSING

    def test_expiry(self):
        clock = [1000.0]
        m = SecretLeaseManager(
            InMemorySecretProvider({"db": "pw"}), clock=lambda: clock[0])
        r = m.acquire("db", ttl_s=10)
        assert m.resolve(r.lease) == "pw"
        clock[0] += 10.001
        assert m.resolve(r.lease) is None  # 过期 → 调用方重新 acquire
        assert m.sweep_expired() == 1

    def test_ttl_clamped(self):
        clock = [0.0]
        m = SecretLeaseManager(
            InMemorySecretProvider({"db": "pw"}), clock=lambda: clock[0])
        r = m.acquire("db", ttl_s=10 ** 9)
        assert r.lease.ttl_s <= 3600.0

    def test_forged_handle_rejected(self):
        m = SecretLeaseManager(InMemorySecretProvider({"db": "pw"}))
        r = m.acquire("db")
        forged = SecretLease(
            lease_id=r.lease.lease_id,
            credential_id="other",
            digest=r.lease.digest,
        )
        assert m.resolve(forged) is None
        assert m.resolve("not-a-lease") is None  # type: ignore[argtype]

    def test_active_leases_lru_bound(self):
        m = SecretLeaseManager(InMemorySecretProvider(
            {f"k{i}": f"v{i}" for i in range(MAX_ACTIVE_LEASES + 50)}))
        leases = [m.acquire(f"k{i}").lease for i in range(MAX_ACTIVE_LEASES + 50)]
        active = m.active_leases()
        assert len(active) == MAX_ACTIVE_LEASES
        # 最旧被逐出:resolve → None;最新完好。
        assert m.resolve(leases[0]) is None
        assert m.resolve(leases[-1]) is not None

    def test_digest_stable_and_bounded(self):
        assert secret_digest("material") == secret_digest("material")
        assert secret_digest("material") != secret_digest("material2")
        assert len(secret_digest("x")) == 16


class TestRedactionProperty:
    def test_material_never_in_projection(self):
        """性质测试:随机 material 绝不出现在 lease 的任何投影面。"""
        rng = random.Random(20260929)
        materials = [_rand_material(rng, n) for n in (8, 16, 32, 64, 128)]
        m = SecretLeaseManager(InMemorySecretProvider(
            {f"k{i}": mat for i, mat in enumerate(materials)}))
        for i, mat in enumerate(materials):
            r = m.acquire(f"k{i}")
            assert r.ok
            lease = r.lease
            for projection in (
                repr(lease), str(lease), str(lease.to_dict()),
                str(sorted(lease.to_dict().items())),
            ):
                assert mat not in projection
            # digest 不是 material 的可逆投影(16 hex ≠ 原文任何片段)
            assert lease.digest not in mat
            assert lease.digest == secret_digest(mat)

    def test_active_leases_projection_clean(self):
        rng = random.Random(7)
        mat = _rand_material(rng, 64)
        m = SecretLeaseManager(InMemorySecretProvider({"k": mat}))
        m.acquire("k")
        blob = str(m.active_leases())
        assert mat not in blob


class TestScopedResolve:
    def test_scoped_resolve_roundtrip(self):
        set_secret_provider(InMemorySecretProvider({"smtp": "scope-secret"}))
        mgr = get_secret_lease_manager()
        r = mgr.acquire("smtp", scope="turn")
        with bind_secret_leases((r.lease,)):
            assert resolve_secret_lease("smtp") == "scope-secret"
            assert len(current_secret_leases()) == 1
        assert resolve_secret_lease("smtp") is None  # 作用域外
        assert resolve_secret_lease("absent") is None

    def test_expired_lease_not_resolvable_in_scope(self):
        clock = [0.0]
        mgr = SecretLeaseManager(
            InMemorySecretProvider({"smtp": "s"}), clock=lambda: clock[0])
        from app.lib.secret_lease import set_secret_lease_manager

        set_secret_lease_manager(mgr)
        r = mgr.acquire("smtp", ttl_s=5)
        with bind_secret_leases((r.lease,)):
            clock[0] = 100.0
            assert resolve_secret_lease("smtp") is None

    def test_default_manager_zero_config_typed_miss(self):
        """默认(无注入)= env provider;env 无声明 → typed miss,不抛。"""
        mgr = get_secret_lease_manager()
        assert mgr.acquire("never_configured").reason_code == \
            LEASE_CREDENTIAL_MISSING


class TestDefaultTtl:
    def test_default_ttl_short(self):
        assert DEFAULT_LEASE_TTL_S <= 600.0  # 短期租约语义
