"""Test GIS-01: RedisSessionStore cross-loop lock safety.

Verifies that RedisSessionStore uses threading.Lock for _client_lock,
allowing worker threads with distinct event loops to invoke _ensure_connected
without triggering RuntimeError: <Lock> is bound to a different event loop.
"""
import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest
import fakeredis.aioredis

from app.services.session_data_redis import RedisSessionStore


@pytest.mark.asyncio
async def test_redis_session_store_cross_loop_thread_access():
    """Worker threads running distinct event loops should not encounter lock affinity RuntimeError."""
    store = RedisSessionStore(redis_url="redis://mock-cross-loop:6379/0")
    assert isinstance(store._client_lock, type(threading.Lock()))

    fake_clients = []

    def mock_from_url(*args, **kwargs):
        c = fakeredis.aioredis.FakeRedis(decode_responses=False)
        fake_clients.append(c)
        return c

    with patch("redis.asyncio.Redis.from_url", side_effect=mock_from_url):
        # 1. Acquire client on main event loop
        main_client = await store._ensure_connected()
        main_loop = asyncio.get_running_loop()
        assert store._loop_clients[main_loop] is main_client

        # 2. Acquire client on a separate worker thread with its own event loop
        def worker_task():
            async def run_in_worker():
                return await store._ensure_connected()

            worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(worker_loop)
            try:
                return worker_loop.run_until_complete(run_in_worker())
            finally:
                worker_loop.close()

        worker_client = await asyncio.to_thread(worker_task)
        assert worker_client is not None
        assert worker_client is not main_client
        assert len(fake_clients) == 2


@pytest.mark.asyncio
async def test_redis_session_store_concurrent_cross_threads():
    """Concurrent worker threads calling _ensure_connected simultaneously succeed cleanly."""
    store = RedisSessionStore(redis_url="redis://mock-cross-loop:6379/0")

    def mock_from_url(*args, **kwargs):
        return MagicMock()

    with patch("redis.asyncio.Redis.from_url", side_effect=mock_from_url):
        def thread_worker(barrier, results, idx):
            async def runner():
                return await store._ensure_connected()

            barrier.wait()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                client = loop.run_until_complete(runner())
                results[idx] = client
            finally:
                loop.close()

        num_threads = 5
        barrier = threading.Barrier(num_threads)
        results = [None] * num_threads
        threads = [
            threading.Thread(target=thread_worker, args=(barrier, results, i))
            for i in range(num_threads)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(res is not None for res in results)
