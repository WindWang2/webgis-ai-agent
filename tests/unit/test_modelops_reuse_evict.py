"""ReuseStore 驱逐回归测试（#1198）。

_evict 此前的 glob 少一层（`*/*/entry.json` vs 实际
root/<scope>/<key[:2]>/<key>/entry.json 三层），TTL / max_entries /
max_bytes 三个上限全部失效。本测试从布局层锁定驱逐语义。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.modelops.reuse import ReuseStore

_OWNER = {"tenant": "acme"}


def _store_entry(root: Path, key: str, *, created_at: float, total_bytes: int = 8) -> None:
    entry_dir = root / "tenant-acme" / key[:2] / key
    entry_dir.mkdir(parents=True, exist_ok=True)
    (entry_dir / "entry.json").write_text(
        json.dumps({"created_at": created_at, "total_bytes": total_bytes}),
        encoding="utf-8",
    )


def _entry_count(root: Path) -> int:
    return len(list(root.glob("*/*/*/entry.json")))


def test_evict_enforces_max_entries(tmp_path: Path) -> None:
    store = ReuseStore(tmp_path, max_entries=3)
    for i in range(6):
        _store_entry(tmp_path, f"reuse-key-{i:02d}", created_at=1000.0 + i)
    store._evict()
    assert _entry_count(tmp_path) <= 3


def test_evict_enforces_ttl(tmp_path: Path) -> None:
    _store_entry(tmp_path, "stale-key-00", created_at=0.0)
    _store_entry(tmp_path, "fresh-key-00", created_at=1000.0)
    # clock 默认 time.time()：created_at=0 必过期，1000 也过期 —— 用注入 clock
    store2 = ReuseStore(tmp_path, max_entries=128, ttl_s=10.0, clock=lambda: 1005.0)
    _store_entry(tmp_path, "stale2-key-0", created_at=0.0)
    _store_entry(tmp_path, "fresh2-key-0", created_at=1000.0)
    store2._evict()
    remaining = {p.parent.name for p in tmp_path.glob("*/*/*/entry.json")}
    assert "stale2-key-0" not in remaining
    assert "fresh2-key-0" in remaining


def test_evict_enforces_max_bytes(tmp_path: Path) -> None:
    store = ReuseStore(tmp_path, max_entries=128, max_bytes=16, clock=lambda: 1000.0)
    _store_entry(tmp_path, "big1-key-00", created_at=100.0, total_bytes=10)
    _store_entry(tmp_path, "big2-key-00", created_at=200.0, total_bytes=10)
    _store_entry(tmp_path, "big3-key-00", created_at=300.0, total_bytes=10)
    store._evict()
    # 预算 16：新在前累计 —— big3(10≤16 保留)、big2(累计 20>16 删除)、big1 删除
    names = {p.parent.name for p in tmp_path.glob("*/*/*/entry.json")}
    assert names == {"big3-key-00"}


def test_store_then_lookup_roundtrip(tmp_path: Path) -> None:
    """布局三层化的端到端确认：store 后 lookup 能命中且 stats 能看见。"""
    import time

    src = tmp_path / "artifact.tif"
    src.write_bytes(b"data")
    store = ReuseStore(
        tmp_path / "reuse", max_entries=8, clock=lambda: time.time() + 0
    )
    ok = store.store(
        "abcdef1234567890",
        owner_scope=_OWNER,
        manifest={"model": "m", "version": "1"},
        artifacts=[{"role": "classes", "file": str(src)}],
    )
    assert ok
    hit = store.lookup("abcdef1234567890", owner_scope=_OWNER)
    assert hit is not None
    stats = store.stats(owner_scope=_OWNER)
    assert stats["entries"] == 1
