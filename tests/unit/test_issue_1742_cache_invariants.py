"""#1742：缓存层三处不变量违反的回归测试。

1) ``cached_tool(singleflight=False)`` 分支补错误形态守卫（模块头不变式
   「错误形态结果从不写缓存」此前只覆盖单飞路径）；
2) artifact/chunk 的 ``.meta`` sidecar 写入不再只捕 OSError —— src_path 为
   pathlib.Path 等不可 JSON 序列化对象时 TypeError/ValueError 也必须被吞掉，
   否则产物已原子落盘却把成功计算翻成工具失败；
3) ``get_artifact``/``get_chunk`` 的 LRU ``os.utime`` bump 容错 —— 与周期
   孤儿清扫竞态时一次有效命中不得变成抛给调用方的异常。
"""
import json
import os
from pathlib import Path

import pytest

from app.lib import artifact_cache as ac


# ── 1. cached_tool：singleflight=False 分支的错误形态守卫 ────────────────

class _FakeRedis:
    """最小 Redis 替身（对齐 test_694_sweep 的 mock 模式）。"""

    def __init__(self):
        self.kv = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, ex=None, nx=False, px=None):
        if nx:
            if key in self.kv:
                return False
            self.kv[key] = value
            return True
        self.kv[key] = value
        return True

    def setex(self, key, ttl, value):
        self.kv[key] = value
        return True

    def eval(self, script, numkeys=0, *keys_args):
        for k in keys_args:
            k = k.decode() if isinstance(k, bytes) else k
            self.kv.pop(k, None)
        return 1

    def exists(self, key):
        return 1 if key in self.kv else 0


@pytest.fixture()
def _fake_redis(monkeypatch):
    import app.lib.tool_cache as tc

    fake = _FakeRedis()
    monkeypatch.setattr(tc, "_get_redis_client", lambda: fake)
    monkeypatch.setattr(tc, "_redis_client", fake, raising=False)
    return fake


@pytest.mark.asyncio
async def test_singleflight_off_async_error_shape_not_cached(_fake_redis):
    """#1742-1：singleflight=False 的 async 分支漏了 _is_error_shaped 守卫。"""
    from app.lib.tool_cache import cached_tool

    calls = {"n": 0}

    @cached_tool(ttl=3600, singleflight=False)
    async def flaky_tool(x: int):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"success": False, "code": "TOOL_ERROR", "message": "boom"}
        return {"success": True, "v": x}

    r1 = await flaky_tool(x=1)
    r2 = await flaky_tool(x=1)
    assert r1.get("success") is False
    # 错误形态没进缓存 → 第二次真实重算（不是命中缓存错误）
    assert r2.get("success") is True and calls["n"] == 2


def test_singleflight_off_sync_error_shape_not_cached(_fake_redis):
    """#1742-1：sync wrapper 的 singleflight=False 分支同样守不变式。"""
    from app.lib.tool_cache import cached_tool

    calls = {"n": 0}

    @cached_tool(ttl=3600, singleflight=False)
    def flaky_tool(x: int):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"error": "upstream timeout"}
        return {"ok": True, "v": x}

    r1 = flaky_tool(x=1)
    r2 = flaky_tool(x=1)
    assert r1 == {"error": "upstream timeout"}
    assert r2 == {"ok": True, "v": 1} and calls["n"] == 2


@pytest.mark.asyncio
async def test_singleflight_off_async_success_still_cached(_fake_redis):
    """守卫只挡错误形态 —— 成功结果照旧写穿缓存。"""
    from app.lib.tool_cache import cached_tool

    calls = {"n": 0}

    @cached_tool(ttl=3600, singleflight=False)
    async def ok_tool(x: int):
        calls["n"] += 1
        return {"success": True, "v": x}

    assert await ok_tool(x=5) == {"success": True, "v": 5}
    assert await ok_tool(x=5) == {"success": True, "v": 5}
    assert calls["n"] == 1


# ── 2. .meta sidecar 写入：非 str src_path / 不可序列化对象不得外抛 ───────

@pytest.fixture()
def _isolated_cache(tmp_path, monkeypatch):
    """把 artifact/chunk 目录指向 tmp_path，并复位 advisory 计数器。"""
    art = tmp_path / "artifacts"
    chunks = art / "chunks"
    art.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ac, "ARTIFACT_DIR", str(art))
    monkeypatch.setattr(ac, "CHUNK_DIR", str(chunks))
    ac._invalidate_advisory_total(ac.ARTIFACT_DIR)
    ac._invalidate_advisory_total(ac.CHUNK_DIR)
    yield art, chunks
    ac._invalidate_advisory_total(ac.ARTIFACT_DIR)
    ac._invalidate_advisory_total(ac.CHUNK_DIR)


def test_publish_artifact_path_src_path_does_not_raise(_isolated_cache):
    """#1742-2：src_path 传 pathlib.Path —— 此前 json.dump 抛 TypeError，
    把已 os.replace 落盘的成功产物翻成工具失败，并留下无 meta 的孤儿 .tif。"""
    art, _ = _isolated_cache
    src = art / "src.tif"
    src.write_bytes(b"SOURCE_RASTER")

    def _compute():
        out = art / "out.tif"
        out.write_bytes(b"PROCESSED_OUTPUT")
        return str(out)

    published = ac.publish_artifact("a" * 16, Path(src), _compute)

    # 产物成功发布（不是 compute 的原始输出回退）
    assert published == ac._artifact_path("a" * 16)
    assert os.path.exists(published)
    # meta 落盘且 src_path 已 str() 规整 → 后续命中不因身份不符而 miss
    with open(ac._meta_path("a" * 16), encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["src_path"] == str(src)
    assert isinstance(meta["src_path"], str)
    assert ac.get_artifact("a" * 16) == published


def test_publish_artifact_unserializable_meta_payload_does_not_raise(_isolated_cache):
    """#1742-2：meta 内容出现 json 不可序列化对象（TypeError/ValueError）时
    发布必须仍然成功返回 .tif 路径 —— sidecar 是记账附属物，写不上只降级
    为后续 miss，绝不把已落盘的产物算成失败。"""
    art, _ = _isolated_cache

    class _Weird:
        def __repr__(self):
            return "<weird>"

    def _compute():
        out = art / "out2.tif"
        out.write_bytes(b"OUT2")
        return str(out)

    # src_path 是不可 JSON 序列化/不可 stat 的自定义对象
    published = ac.publish_artifact("b" * 16, _Weird(), _compute)
    assert published == ac._artifact_path("b" * 16)
    assert os.path.exists(published)
    # meta 没写成有效 JSON（或为空）→ 后续查询降级为 miss，且不抛
    assert ac.get_artifact("b" * 16) is None


def test_publish_chunk_path_source_path_does_not_raise(_isolated_cache):
    """#1742-2：publish_chunk 同 chassis —— Path source_path 不得外抛。"""
    art, _ = _isolated_cache
    src = Path(art) / "src_chunk.tif"
    src.write_bytes(b"CHUNK_SRC")

    stored = ac.publish_chunk("c" * 16, b"PAYLOAD", source_path=Path(src))
    assert stored == ac._chunk_path("c" * 16)
    assert os.path.exists(stored)
    with open(ac._chunk_meta_path("c" * 16), encoding="utf-8") as f:
        assert json.load(f)["src_path"] == str(src)
    assert ac.get_chunk("c" * 16) == stored


# ── 3. LRU bump 容错：命中不得因 utime 竞态变异常 ────────────────────────

def test_get_artifact_survives_utime_failure(_isolated_cache, monkeypatch):
    """#1742-3：命中路径的 os.utime 与周期孤儿清扫竞态（meta 被 unlink）——
    此前裸调把一次有效命中变成 FileNotFoundError 抛给调用方。"""
    art, _ = _isolated_cache
    src = art / "src3.tif"
    src.write_bytes(b"SRC3")

    def _compute():
        out = art / "out3.tif"
        out.write_bytes(b"OUT3")
        return str(out)

    key = "d" * 16
    assert ac.publish_artifact(key, str(src), _compute) == ac._artifact_path(key)

    def _boom(*a, **kw):
        raise FileNotFoundError("meta vanished mid-bump (orphan sweep race)")

    monkeypatch.setattr(os, "utime", _boom)
    assert ac.get_artifact(key) == ac._artifact_path(key)


def test_get_chunk_survives_utime_failure(_isolated_cache, monkeypatch):
    """#1742-3：chunk 命中路径同纪律（其 docstring 自称 never raise）。"""
    art, _ = _isolated_cache
    src = art / "src4.tif"
    src.write_bytes(b"SRC4")
    key = "e" * 16
    assert ac.publish_chunk(key, b"P", source_path=str(src)) == ac._chunk_path(key)

    def _boom(*a, **kw):
        raise PermissionError("read-only fs")

    monkeypatch.setattr(os, "utime", _boom)
    assert ac.get_chunk(key) == ac._chunk_path(key)
