"""Chaos：磁盘制品缓存（app/lib/artifact_cache.py，审计 05 Top-25 #1-#3）。

审计结论：publish 失败路径、LRU 驱逐、坏 .meta 处理此前完全无测试
（只有 3 个 happy-path 用例），而它垫在所有昂贵栅格算子下面 —— 出错
即是静默错误输出 / 静默缓存丢失。全部注入走 tests/fixtures/chaos.py
的既有接缝；每个用例同时断言「故障真的开火」与「系统诚实恢复」。

注意：ARTIFACT_DIR = data/artifacts（相对 cwd），与既有
tests/unit/test_artifact_cache.py 同一约定 —— autouse fixture 前后清扫。
"""
import os

import pytest

from app.lib.artifact_cache import (
    _artifact_path,
    _meta_path,
    clear_artifact_cache,
    get_artifact,
    make_artifact_key,
    publish_artifact,
    sweep_orphan_disk_artifacts,
)
from tests.fixtures.chaos import chaos, reset_journal

# 审计 05 #1-#3（确定性 sweep 用的合成时钟：远早于真实 mtime，且落在
# 宽限期/保留期之外 —— 不赌文件系统时钟）
_SWEEP_NOW = 10**9
_SWEEP_OLD = _SWEEP_NOW - 10 * 86400  # 10 天前：超出 1h 宽限、30d 保留以内


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_artifact_cache()
    reset_journal()
    yield
    clear_artifact_cache()
    reset_journal()


def _source_file(tmp_path, name: str, content: bytes) -> str:
    p = tmp_path / f"src-{name}.tif"
    p.write_bytes(content)
    return str(p)


def _compute_to(tmp_path, name: str, content: bytes):
    def _compute() -> str:
        out = tmp_path / f"out-{name}.tif"
        out.write_bytes(content)
        return str(out)

    return _compute


# ── 1. LRU 驱逐（审计 #1：静默删除别人还握着路径的条目 = 数据丢失级）──────


def test_lru_evicts_oldest_under_pressure(tmp_path):
    """上限骤减 → 最老条目（.tif+.meta 成对）被驱逐、recency 最新者存活。"""
    keys = []
    outputs = [b"OUT-0-PAYLOAD!", b"OUT-1-PAYLOAD!", b"OUT-2-PAYLOAD"]  # 各 14/14/13B
    for i, payload in enumerate(outputs):
        src = _source_file(tmp_path, f"s{i}", f"SRC-{i}".encode())
        key = make_artifact_key(src, "op", {"i": i})
        keys.append(key)
        publish_artifact(key, src, _compute_to(tmp_path, f"o{i}", payload))
        assert get_artifact(key) == _artifact_path(key)

    # 显式 recency 序（os.utime 钉 mtime —— 不赌 FS 时钟分辨率）
    os.utime(_meta_path(keys[0]), (_SWEEP_NOW, _SWEEP_NOW))          # 最老
    os.utime(_meta_path(keys[1]), (_SWEEP_NOW + 1000, _SWEEP_NOW + 1000))
    os.utime(_meta_path(keys[2]), (_SWEEP_NOW + 2000, _SWEEP_NOW + 2000))  # 最新

    # 上限 = 最新两条之和 → 驱逐恰好吃掉最老两条后收敛
    cap = len(outputs[1]) + len(outputs[2])
    with chaos("CACHE_CAP_SHRINK", max_bytes=cap) as fault:
        src_new = _source_file(tmp_path, "s-new", b"SRC-NEW")
        new_key = make_artifact_key(src_new, "op", {"new": 1})
        publish_artifact(new_key, src_new, _compute_to(tmp_path, "o-new", b"OUT-NEW!!!"))

    assert fault.fired
    for victim in keys[:2]:
        assert not os.path.exists(_artifact_path(victim)), f"{victim} 应被 LRU 驱逐"
        assert not os.path.exists(_meta_path(victim)), "驱逐必须 .tif+.meta 成对删除"
    assert os.path.exists(_artifact_path(keys[2])), "recency 最新的条目不得被误删"
    assert get_artifact(keys[2]) == _artifact_path(keys[2])
    assert os.path.exists(_artifact_path(new_key))


def test_lru_eviction_leaves_clean_directory_family(tmp_path):
    """压力驱逐后的目录族干净：只剩成对条目或全空，无孤儿/临时件。"""
    for i in range(2):
        src = _source_file(tmp_path, f"c{i}", f"SC-{i}".encode())
        publish_artifact(
            make_artifact_key(src, "op", {"i": i}),
            src,
            _compute_to(tmp_path, f"oc{i}", f"OC-{i}-PAYLOAD".encode()),
        )
    with chaos("CACHE_CAP_SHRINK", max_bytes=1) as fault:  # 上限 1B → 全部驱逐
        src = _source_file(tmp_path, "c-new", b"SC-NEW")
        publish_artifact(
            make_artifact_key(src, "op", {"new": 1}),
            src,
            _compute_to(tmp_path, "oc-new", b"OC-NEW-PAYLOAD"),
        )

    assert fault.fired
    result = sweep_orphan_disk_artifacts(now=_SWEEP_NOW)
    assert result["temp_leftovers"] == 0, "驱逐不得遗留 mkstemp 临时件"
    assert result["orphan_tif"] == 0 and result["orphan_meta"] == 0, (
        "驱逐必须成对删除，不留半边孤儿"
    )
    names = os.listdir(os.path.dirname(_artifact_path("0" * 16)))
    assert all(name.endswith((".tif", ".meta")) for name in names)


# ── 2. publish 复制失败（审计 #2：幻影 .tif / 记账腐蚀）───────────────────


def test_publish_replace_failure_leaves_no_partial_artifact(tmp_path):
    """os.replace 中途 ENOSPC → tmp 清理、无半截发布、调用方拿直出 fallback。"""
    src = _source_file(tmp_path, "s-fail", b"SOURCE-RASTER")
    key = make_artifact_key(src, "op", {})
    fallback_marker = {}

    def _compute() -> str:
        out = tmp_path / "direct-out.tif"
        out.write_bytes(b"DIRECT-OUTPUT")
        fallback_marker["path"] = str(out)
        return str(out)

    with chaos("CACHE_COPY_FAIL") as fault:
        result = publish_artifact(key, src, _compute)

    assert fault.fired, "os.replace 必须真的被打中"
    assert result == fallback_marker["path"], "失败后必须回退到直出输出"
    assert get_artifact(key) is None, "失败发布绝不可表现为命中"
    assert not os.path.exists(_artifact_path(key))
    # mkstemp 临时件必须被清理：目录里不允许任何非本 key 命名的遗留
    leftovers = [
        n
        for n in os.listdir(os.path.dirname(_artifact_path(key)))
        if n not in (f"{key}.tif", f"{key}.meta")
    ]
    assert leftovers == [], f"publish 失败遗留临时件: {leftovers}"


# ── 3. .meta 写失败 / 坏 .meta（审计 #3：诚实 miss，不是崩溃）─────────────


def test_meta_write_failure_publishes_tif_but_reads_as_honest_miss(tmp_path):
    """.meta 落盘失败 → publish 不崩溃；读取侧诚实 miss（孤儿清扫兜底）。"""
    src = _source_file(tmp_path, "s-meta", b"SOURCE-META-FAIL")
    key = make_artifact_key(src, "op", {})

    with chaos("CACHE_META_WRITE_FAIL") as fault:
        result = publish_artifact(key, src, _compute_to(tmp_path, "o-meta", b"OUT-META"))

    assert fault.fired
    assert result == _artifact_path(key)
    assert os.path.exists(_artifact_path(key)), ".tif 本体必须已发布"
    # publish-vs-meta 间隙（审计 :277-280）：meta 缺失 → 读取必须诚实 miss
    assert get_artifact(key) is None
    # 孤儿清扫器是该间隙的兜底：宽限期外回收无 meta 的 .tif 半边
    os.utime(_artifact_path(key), (_SWEEP_OLD, _SWEEP_OLD))
    swept = sweep_orphan_disk_artifacts(now=_SWEEP_NOW)
    assert swept["orphan_tif"] == 1
    assert not os.path.exists(_artifact_path(key))


def test_corrupt_meta_is_typed_miss_not_crash(tmp_path):
    """坏 JSON 字节的 .meta → 诚实 miss，绝不崩溃、绝不返回无 meta 的 .tif。"""
    src = _source_file(tmp_path, "s-corrupt", b"SOURCE-CORRUPT-META")
    key = make_artifact_key(src, "op", {})
    published = publish_artifact(key, src, _compute_to(tmp_path, "o-corrupt", b"OUT"))
    assert get_artifact(key) == published  # 前提：健康时命中

    with chaos("CACHE_META_CORRUPT", key=key) as fault:
        pass

    assert fault.fired
    assert os.path.exists(_artifact_path(key))
    assert get_artifact(key) is None, "坏 meta 必须表现为 miss（typed，不 crash）"
