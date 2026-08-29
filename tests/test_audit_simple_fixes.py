"""Regression tests for the batch of simple audit fixes.

Covers:
- #378 RedisSessionStore.get_map_state RedisError isolation
- #382 EVI DN→reflectance rescaling
- #383 calculate_central_feature self-distance exclusion
- #388 env-summary timestamp frozen per session (prefix-cache stability)
- #397 distributed-lock renewal is token-checked and stops after ownership loss
"""
import asyncio
import os

import numpy as np
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-simple-fixes-secret-32chars-min")
os.environ.setdefault("ENV", "development")


# ---------------------------------------------------------------- #378
class TestGetMapStateRedisErrorIsolation:
    @pytest.mark.asyncio
    async def test_redis_blip_returns_empty_not_raise(self):
        import fakeredis.aioredis
        from app.services.session_data_redis import RedisSessionDataManager

        sdm = RedisSessionDataManager("redis://localhost:6379", capacity=200)
        sdm._r = fakeredis.aioredis.FakeRedis()
        sdm._bound_loop = asyncio.get_running_loop()

        # Make the hot read fail the way a real outage does (redis's
        # ConnectionError is an aioredis.RedisError subclass).
        import redis.exceptions

        async def _boom(*a, **k):
            raise redis.exceptions.ConnectionError("redis down")

        sdm._r.hgetall = _boom
        assert await sdm.get_map_state("sess-378") == {}


# ---------------------------------------------------------------- #382
class TestEviDnScaling:
    def test_dn_inputs_rescaled_to_reflectance(self):
        from app.services.rs.band_math import compute_index_array

        # Sentinel-2 L2A DN for reflectance 0.40 / 0.10 / 0.08 (nir/red/blue).
        evi = compute_index_array(
            "evi", nir=np.array([4000.0]), red=np.array([1000.0]), blue=np.array([800.0])
        )
        assert evi[0] == pytest.approx(0.536, abs=1e-3)

    def test_reflectance_inputs_unchanged(self):
        from app.services.rs.band_math import compute_index_array

        evi = compute_index_array(
            "evi", nir=np.array([0.40]), red=np.array([0.10]), blue=np.array([0.08])
        )
        assert evi[0] == pytest.approx(0.536, abs=1e-3)

    def test_ratio_indices_unaffected_by_dn(self):
        from app.services.rs.band_math import compute_index_array

        ndvi_dn = compute_index_array("ndvi", nir=np.array([4000.0]), red=np.array([1000.0]))
        ndvi_ref = compute_index_array("ndvi", nir=np.array([0.40]), red=np.array([0.10]))
        assert ndvi_dn[0] == pytest.approx(ndvi_ref[0])


# ---------------------------------------------------------------- #383
class TestCentralFeatureSelfDistance:
    def test_matches_brute_force(self):
        """Property check vs O(n²) brute force on a random cloud (#383)."""
        from app.lib.geo_analysis.statistics import calculate_central_feature

        rng = np.random.default_rng(42)
        n = 60
        lon = 116.3 + rng.uniform(0, 0.02, n)
        lat = 39.9 + rng.uniform(0, 0.02, n)
        features = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon[i], lat[i]]}, "properties": {}}
            for i in range(n)
        ]
        res = calculate_central_feature({"type": "FeatureCollection", "features": features}, "central_feature")
        assert res.success

        # Brute force in a local equirectangular frame (same as UTM locally).
        k = np.degrees(1) * 111320.0
        xs, ys = (lon - lon.mean()) * k, (lat - lat.mean()) * k * np.cos(np.radians(39.9))
        d = np.hypot(xs[:, None] - xs[None, :], ys[:, None] - ys[None, :])
        expect = int(np.argmin(d.sum(axis=1)))
        got = res.data["properties"]["summary"]
        assert f"index {expect}" in got, f"expected central feature {expect}, got: {got}"


# ---------------------------------------------------------------- #388
class TestEnvTimestampStable:
    def test_frozen_within_ttl_and_refreshes_after(self):
        from app.services.chat.context_builder import _env_timestamp, _env_ts_cache

        _env_ts_cache.clear()
        a = _env_timestamp("sess-388")
        b = _env_timestamp("sess-388")
        assert a == b

        # Force expiry by rewinding the recorded monotonic stamp.
        ts, stamp = _env_ts_cache["sess-388"]
        _env_ts_cache["sess-388"] = (ts, stamp - 10_000)
        c = _env_timestamp("sess-388")
        assert c >= a

        # Different sessions may hold different frozen values.
        _ = _env_timestamp("sess-388-other")
        assert "sess-388-other" in _env_ts_cache
        _env_ts_cache.clear()


# ---------------------------------------------------------------- #397
class TestLockRenewTokenChecked:
    @pytest.mark.asyncio
    async def test_renew_loop_stops_when_ownership_lost(self):
        from app.services.distributed_lock import _InProcessLock, _ResilientSessionLock
        import app.services.distributed_lock as dl

        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def set(self, *a, **k):
                return True

            async def eval(self, script, numkeys, key, *args):
                self.calls += 1
                # Second renewal reports the key now belongs to someone else.
                return 1 if self.calls == 1 else 0

        client = FakeClient()
        lock = _ResilientSessionLock(client, "k397", _InProcessLock())
        # v2(gate)：不再 importlib.reload —— 重载会原地替换 distributed_lock
        # 模块的全部类对象，晚绑定方法随后抛出「新」LockDegradedError，与
        # 其它测试模块在收集期 from-import 的「旧」类身份不再匹配（全量
        # 套件中 session_lock_resilience / runtime_v2 降级锁用例因此假败）。
        # 手动恢复常量即可满足原本的清理意图。
        original_interval = dl._RENEW_INTERVAL_S
        async with lock:
            assert lock._mode == "redis"
            # Shorten the renew interval for the test.
            dl._RENEW_INTERVAL_S = 0.01
            try:
                await asyncio.sleep(0.08)
            finally:
                dl._RENEW_INTERVAL_S = original_interval
            assert client.calls >= 2, "renewal should have run at least twice"
            # Loop must have exited after the lost-ownership response.
            assert lock._renewer.done() or lock._renewer is None
