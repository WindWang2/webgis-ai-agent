"""Issue #470：会话磁盘状态（mapspec revisions / checkpoints / raster PNGs）
在 TTL 过期 / idle 淘汰时必须被清除。

之前唯一的清除路径是 `clear_session_files`，唯一调用方是
DELETE /sessions/{id}（chat.py）。4h Redis TTL 过期、cleanup_idle_sessions、
以及 main.py 的 600s 周期清理任务都只删 Redis 键 + 内存缓存，从不碰磁盘 ——
`.webgis-agent/<sid>/` 无限累积（每个活跃制图会话 ~0.5-5 MB）。

守卫（全部用 tmp_path 做会话目录，通过 monkeypatch BASE_STORAGE_DIR）：
  1. clear_session（内存与 Redis 两种后端）连带清除磁盘；
  2. cleanup_idle_sessions 淘汰溢出会话时同样清盘；
  3. 磁盘清除失败（IO 错误）不破坏 clear_session 的其余清理；
  4. 目录缺失时静默通过（幂等）；
  5. TTL 过期兜底：mtime 老于 TTL+slack 的目录被周期清扫回收，仍活跃的
     会话（store 里还有状态）即使磁盘 mtime 老 also 不清；
  6. 清扫有界且安全：跳过非常规名字/非目录条目。
"""
import os
import time
from pathlib import Path

import pytest

import app.services.mapspec.store as mapspec_store_module
from app.services.mapspec.store import MapSpecStore
from app.services.session_data import MemorySessionStore


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """把会话磁盘根指到 tmp_path。"""
    monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", tmp_path)
    return tmp_path


def seed_session_files(base: Path, session_id: str, age_seconds: float = 0) -> Path:
    """造一个带 mapspec.json / revisions / checkpoints / raster 的会话目录。"""
    d = base / session_id
    (d / "revisions").mkdir(parents=True, exist_ok=True)
    (d / "checkpoints" / "blobs").mkdir(parents=True, exist_ok=True)
    (d / "raster").mkdir(parents=True, exist_ok=True)
    (d / "mapspec.json").write_text("{}", encoding="utf-8")
    (d / "revisions" / "mapspec_rev_1.json").write_text("{}", encoding="utf-8")
    (d / "checkpoints" / "manifest.json").write_text("{}", encoding="utf-8")
    (d / "raster" / "abc.png").write_bytes(b"\x89PNG")
    if age_seconds:
        past = time.time() - age_seconds
        for root, _dirs, files in os.walk(d):
            for f in files:
                os.utime(Path(root) / f, (past, past))
            os.utime(root, (past, past))
    return d


# ── 1. clear_session 连带清盘 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_clear_session_purges_disk(storage):
    store = MemorySessionStore()
    sid = "sess-disk-a"
    await store.store(sid, {"v": 1})
    d = seed_session_files(storage, sid)
    assert d.exists()

    await store.clear_session(sid)
    assert not d.exists(), "clear_session 后会话磁盘目录必须被删除"
    # 内存也清了
    assert await store.get(sid, "ref:x") is None
    assert await store.get_map_state(sid) == {}


@pytest.mark.asyncio
async def test_redis_clear_session_purges_disk(storage):
    import fakeredis.aioredis

    from app.services.session_data_redis import RedisSessionStore

    raw = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisSessionStore(redis_url="redis://unused", redis=raw)
    sid = "sess-disk-b"
    await store.store(sid, {"v": 1})
    d = seed_session_files(storage, sid)

    await store.clear_session(sid)
    assert not d.exists(), "Redis 后端 clear_session 也必须清盘（与 Redis 键同语义）"


@pytest.mark.asyncio
async def test_clear_session_missing_dir_is_ok(storage):
    store = MemorySessionStore()
    await store.store("sess-no-disk", {"v": 1})
    await store.clear_session("sess-no-disk")  # 不抛 = 通过


@pytest.mark.asyncio
async def test_clear_session_survives_disk_purge_failure(storage, monkeypatch, caplog):
    store = MemorySessionStore()
    sid = "sess-iofail"
    await store.store(sid, {"v": 1})
    seed_session_files(storage, sid)

    async def _boom(session_id: str) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(
        "app.services.mapspec.store.purge_session_disk_state", _boom
    )
    with caplog.at_level("WARNING"):
        await store.clear_session(sid)  # 不得抛
    # 内存态仍被清理（磁盘清理失败不阻断其它清理）
    assert await store.get_map_state(sid) == {}
    assert any("disk on fire" in r.message for r in caplog.records), "失败必须留痕"


# ── 2. idle 淘汰连带清盘 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_cleanup_idle_sessions_purges_evicted_disk(storage):
    store = MemorySessionStore()
    dirs = {}
    for i in range(3):
        sid = f"sess-evict-{i}"
        await store.store(sid, {"v": i})
        dirs[sid] = seed_session_files(storage, sid)

    await store.cleanup_idle_sessions(max_sessions=1)
    # 最久未触碰的 2 个被淘汰 => 磁盘目录也被清除；保留的 1 个仍在
    assert sum(d.exists() for d in dirs.values()) == 1, (
        f"evicted 会话目录必须清除；仍存在: {[s for s, d in dirs.items() if d.exists()]}"
    )


@pytest.mark.asyncio
async def test_redis_cleanup_idle_sessions_purges_evicted_disk(storage):
    import fakeredis.aioredis

    from app.services.session_data_redis import RedisSessionStore

    raw = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisSessionStore(redis_url="redis://unused", redis=raw)
    dirs = {}
    for i in range(3):
        sid = f"sess-revict-{i}"
        await store.store(sid, {"v": i})
        dirs[sid] = seed_session_files(storage, sid)

    await store.cleanup_idle_sessions(max_sessions=1)
    assert sum(d.exists() for d in dirs.values()) == 1


# ── 3. TTL 过期兜底清扫（Redis 键静默过期后磁盘仍被回收）────────────────


@pytest.mark.asyncio
async def test_sweep_purges_expired_and_keeps_active(storage):
    from app.services.session_data_redis import SESSION_TTL

    store = MemorySessionStore()
    # 过期会话：磁盘 mtime 老于 TTL+slack，且 store 无状态（Redis 键已 TTL 消失）
    expired = seed_session_files(
        storage, "sess-expired", age_seconds=SESSION_TTL + 2 * 3600
    )
    # 活跃会话：磁盘同样老，但 store 里仍有状态（Redis TTL 被活动续期）
    live_old_disk = seed_session_files(
        storage, "sess-live-old-disk", age_seconds=SESSION_TTL + 2 * 3600
    )
    await store.store("sess-live-old-disk", {"v": 1})
    # 正常近期会话
    fresh = seed_session_files(storage, "sess-fresh")

    purged = await mapspec_store_module.sweep_expired_session_files(
        liveness=getattr(store, "is_session_active", None)
    )
    assert "sess-expired" in purged
    assert expired.exists() is False
    assert live_old_disk.exists(), "仍活跃（store 有状态）的会话磁盘不得清扫"
    assert fresh.exists(), "新会话目录不得清扫"


@pytest.mark.asyncio
async def test_sweep_skips_unsafe_entries(storage):
    # 非目录文件、不可作为会话 id 的名字（路径分隔符等）必须跳过，不得触碰
    (storage / "stray.txt").write_text("x", encoding="utf-8")
    weird = storage / "..%2Fetc"
    weird.mkdir(parents=True, exist_ok=True)
    (weird / "junk").write_text("x", encoding="utf-8")
    old = time.time() - 10 * 24 * 3600
    os.utime(weird, (old, old))

    purged = await mapspec_store_module.sweep_expired_session_files()
    assert "stray.txt" not in purged
    assert "stray.txt" and (storage / "stray.txt").exists()
    assert weird.exists(), "非常规会话目录名必须跳过（安全边界，不猜语义）"


# ── 4. 周期任务挂接（main.py 的 _periodic_session_cleanup）──────────────


def test_periodic_cleanup_task_invokes_sweep():
    """_periodic_session_cleanup 必须在 cleanup_idle_sessions 之外还调用
    sweep_expired_session_files（TTL 过期的磁盘兜底入口）。"""
    source = (
        Path(__file__).resolve().parents[1] / "app" / "main.py"
    ).read_text(encoding="utf-8")
    assert "sweep_expired_session_files" in source, (
        "周期清理任务必须调用 sweep_expired_session_files，否则 Redis TTL 静默"
        "过期的会话磁盘永远无人回收"
    )


def test_clear_session_files_is_idempotent_and_bounded():
    """clear_session_files 对缺失目录必须静默（不 mkdir 再删也不抛 FileNotFoundError）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        store = MapSpecStore()
        # 直接调（不经 monkeypatch 的 BASE_STORAGE_DIR 也应安全：目标不存在）
        import asyncio

        sid = "never-existed-sid"
        base = Path(td)
        # 用实例上的 BASE_STORAGE_DIR —— store 方法读模块全局；直接构造场景
        from unittest.mock import patch

        with patch.object(mapspec_store_module, "BASE_STORAGE_DIR", base):
            asyncio.run(store.clear_session_files(sid))  # 不抛即通过
        assert not (base / sid).exists(), "缺失目录不得被重新创建"
