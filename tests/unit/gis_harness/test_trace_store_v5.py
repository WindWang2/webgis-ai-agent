"""Durable Trace V5 —— 多 worker 安全持久化契约（ADR-0118 D1）。

验收锚点（Epic V5）：
- multi-worker trace 不丢事件：多进程并发 append 同一会话文件 →
  全部记录落盘、seq 单调且无重复；
- settle 幂等：同链重复 persist 不产生重复行；
- trim 不丢 FINAL_VERDICT 关键证据；
- V4 旧文件（无 seq）向后兼容读取与续写；
- registry LRU 驱逐后 settle 持久化仍成功（pinned 区）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pytest

from app.lib.runtime.gis_trace import Stage, get_gis_trace_registry
from app.services.gis_harness import trace_store


def _make_chain_dict(turn_id: str, total: int = 3,
                     final: bool = False) -> dict:
    stages = [{"stage": "USER_INTENT", "stage_id": 1, "ts": 1.0}]
    if final:
        stages.append({"stage": "FINAL_VERDICT", "stage_id": 17, "ts": 2.0,
                       "payload": {"verdict": "READY"}})
    return {"turn_id": turn_id, "session_id": "s", "total_records": total,
            "completeness": 0.1, "stages": stages}


@pytest.fixture()
def store_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(d))
    return d


# ---------------------------------------------------------------- 多进程零丢行

_SUBPROG = """
import json, os, sys
sys.path.insert(0, {repo!r})
os.environ["MAPSPEC_STORAGE_DIR"] = {store!r}
from app.services.gis_harness import trace_store

worker, count = int(sys.argv[1]), int(sys.argv[2])
for i in range(count):
    rec = {{"turn_id": f"w{{worker}}-t{{i}}", "session_id": "multi",
           "total_records": 1, "completeness": 0.05, "stages": []}}
    ok = trace_store.persist_chain(rec, session_id="multi")
    if not ok:
        print(json.dumps({{"ok": False}})); sys.exit(1)
print(json.dumps({{"ok": True}}))
"""


@pytest.mark.skipif(not trace_store._HAS_FCNTL,
                    reason="flock 不可用（非 POSIX）—— 降级路径无跨进程契约")
def test_multi_worker_concurrent_append_zero_loss(store_dir):
    """8 进程 × 6 记录并发写同一会话 → 48 行全部落盘，seq 唯一且连续。"""
    procs = [
        subprocess.Popen(
            [sys.executable, "-c",
             _SUBPROG.format(repo=os.getcwd(), store=str(store_dir)),
             str(w), "6"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for w in range(8)
    ]
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, f"worker failed: {err}"
        assert json.loads(out)["ok"] is True

    records = trace_store.read_chains("multi")
    assert len(records) == 48, f"row loss: {len(records)}/48"
    seqs = sorted(r["seq"] for r in records)
    assert len(set(seqs)) == 48, "seq 重复"
    assert seqs == list(range(1, 49)), "seq 非连续（丢行窗口）"
    turn_ids = {r["turn_id"] for r in records}
    assert len(turn_ids) == 48


def test_seq_monotonic_and_last_seq(store_dir):
    for i in range(5):
        assert trace_store.persist_chain(
            _make_chain_dict(f"t{i}"), session_id="seq-s")
    records = trace_store.read_chains("seq-s")
    assert [r["seq"] for r in records] == [1, 2, 3, 4, 5]
    assert trace_store.last_seq("seq-s") == 5
    assert trace_store.last_seq("no-such-session") == 0


# ---------------------------------------------------------------- 幂等

def test_persist_idempotent_on_settle_retry(store_dir):
    rec = _make_chain_dict("turn-dup", total=7)
    assert trace_store.persist_chain(rec, session_id="dup") is True
    assert trace_store.persist_chain(rec, session_id="dup") is True
    # total_records 变化（同 turn 新证据）→ 追加，不覆盖
    rec2 = _make_chain_dict("turn-dup", total=9)
    assert trace_store.persist_chain(rec2, session_id="dup") is True
    records = trace_store.read_chains("dup")
    assert len(records) == 2
    assert [r["seq"] for r in records] == [1, 2]


# ---------------------------------------------------------------- trim 保护

def test_trim_preserves_final_verdict_records(store_dir):
    verdict = _make_chain_dict("turn-verdict", final=True)
    assert trace_store.persist_chain(verdict, session_id="trim")
    # 写满窗口（全部普通记录）
    for i in range(trace_store.MAX_RECORDS_PER_SESSION + 10):
        assert trace_store.persist_chain(
            _make_chain_dict(f"fill-{i}"), session_id="trim")
    records = trace_store.read_chains("trim")
    assert len(records) <= trace_store.MAX_RECORDS_PER_SESSION
    assert any(r["turn_id"] == "turn-verdict" for r in records), \
        "FINAL_VERDICT 记录被 trim 丢弃"
    seqs = [r["seq"] for r in records]
    assert len(set(seqs)) == len(seqs)


# ---------------------------------------------------------------- V4 兼容

def test_v4_file_without_seq_is_readable_and_extends(store_dir, monkeypatch):
    sid = "legacy"
    d = store_dir / ".webgis-agent" / sid
    d.mkdir(parents=True)
    legacy = [{"turn_id": "old-1", "session_id": sid, "total_records": 2,
               "completeness": 0.1, "stages": []}]
    (d / "trace_chains.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in legacy),
        encoding="utf-8")
    assert trace_store.read_chains(sid)[0]["turn_id"] == "old-1"
    assert trace_store.last_seq(sid) == 0  # 旧记录无 seq
    assert trace_store.persist_chain(
        _make_chain_dict("new-1"), session_id=sid) is True
    records = trace_store.read_chains(sid)
    assert len(records) == 2
    assert records[1]["seq"] == 1  # 新记录 seq 从 1 起（历史缺 seq 不伪造）


# ---------------------------------------------------------------- pinned 防驱逐

def test_pinned_chain_survives_lru_eviction():
    reg = get_gis_trace_registry()
    first = reg.start("pin-turn")
    first.record(Stage.USER_INTENT, q="x")
    # 挤压 LRU（max_chains=128 默认；新 registry 实例隔离性不足 —— 用量压满）
    for i in range(reg.max_chains + 5):
        reg.start(f"flood-{i}")
    assert reg.get("pin-turn") is not None, "pinned 链被 LRU 驱逐丢失"


def test_unpin_after_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "d2"))
    (tmp_path / "d2").mkdir()
    reg = get_gis_trace_registry()
    tid = f"unpin-{uuid.uuid4().hex[:8]}"
    chain = reg.start(tid)
    chain.record(Stage.USER_INTENT, q="x")
    assert trace_store.persist_turn_chain(tid, session_id="unpin-s") is True
    records = trace_store.read_chains("unpin-s")
    assert len(records) == 1 and records[0]["turn_id"] == tid
