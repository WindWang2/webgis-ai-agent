"""Trace Store V6 —— 分段 / 增量读 / 压缩契约（ADR-0119 决策 D6）。

验收锚点（Epic V6）：
- reader 不做全量解析：``read_chains_since(after_seq)`` 跳过旧整段
  （零解析语义由文件访问计数证明）；
- 段滚动压缩真实发生（.gz 落盘、读侧透明解压）；
- ``last_seq`` 走 manifest O(1)；
- 跨进程汇聚接口 ``iter_session_chains``；
- V5 全部契约（多进程零丢行 / 幂等 / trim 保护 / V4 兼容）保持 ——
  见 test_trace_store_v5.py（同一实现对 V5 套件全绿）。
"""
from __future__ import annotations

import gzip
import json

import pytest

from app.services.gis_harness import trace_store


def _rec(tid: str, seq_note: str = "", final: bool = False) -> dict:
    stages = [{"stage": "USER_INTENT", "stage_id": 1, "ts": 1.0}]
    if final:
        stages.append({"stage": "FINAL_VERDICT", "stage_id": 17, "ts": 2.0})
    return {"turn_id": tid, "session_id": "s", "total_records": 3,
            "completeness": 0.1, "note": seq_note, "stages": stages}


@pytest.fixture()
def store_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(d))
    return d


def test_segment_roll_and_compression(store_dir):
    """>16 条 → 滚动 + 上一段 gzip 化；读侧透明解压内容一致。"""
    for i in range(20):
        assert trace_store.persist_chain(_rec(f"t{i}"), session_id="roll")
    v6_dir = (store_dir / ".webgis-agent" / "roll" / "trace_v6")
    manifest = json.loads((v6_dir / "manifest.json").read_text())
    segs = manifest["segments"]
    assert len(segs) >= 2
    gz_files = [s["file"] for s in segs if s["file"].endswith(".gz")]
    assert gz_files, "滚动段未压缩"
    for f in gz_files:
        with gzip.open(v6_dir / f, "rt", encoding="utf-8") as fh:
            assert fh.read().strip()
    recs = trace_store.read_chains("roll")
    assert [r["seq"] for r in recs] == list(range(1, 21))
    assert trace_store.last_seq("roll") == 20


def test_read_chains_since_skips_old_segments(store_dir, monkeypatch):
    """增量读：旧段零解析（读文件次数计数证明）。"""
    calls = {"n": 0}
    real_read = trace_store._read_segment_text

    def counting_read(path):
        calls["n"] += 1
        return real_read(path)

    monkeypatch.setattr(trace_store, "_read_segment_text", counting_read)
    for i in range(20):  # 2 段
        trace_store.persist_chain(_rec(f"t{i}"), session_id="incr")
    assert trace_store.last_seq("incr") == 20
    calls["n"] = 0
    since = trace_store.read_chains_since("incr", 16)
    assert [r["seq"] for r in since] == [17, 18, 19, 20]
    # 段1（seq 1-16）整段跳过：只有段2 被解析（<3 次访问 = manifest 驱动）
    assert calls["n"] <= 2, f"旧段被重复解析（{calls['n']} 次段读取）"
    # 游标 0 = 全量（read_chains 等价）
    full = trace_store.read_chains_since("incr", 0)
    assert len(full) == 20


def test_last_seq_manifest_short_circuit(store_dir):
    """last_seq 读 manifest（不开 legacy 文件；缺文件会话 = 0）。"""
    for i in range(3):
        trace_store.persist_chain(_rec(f"t{i}"), session_id="ls")
    assert trace_store.last_seq("ls") == 3
    assert trace_store.last_seq("no-such") == 0


def test_iter_session_chains_bridging(store_dir):
    """跨进程汇聚接口：多 session 有界迭代 + seq 过滤。"""
    for i in range(2):
        trace_store.persist_chain(_rec(f"a{i}"), session_id="iter-a")
    trace_store.persist_chain(_rec("b0"), session_id="iter-b")
    got = dict()
    for sid, rec in trace_store.iter_session_chains(["iter-a", "iter-b"]):
        got.setdefault(sid, []).append(rec["turn_id"])
    assert sorted(got["iter-a"]) == ["a0", "a1"]
    assert got["iter-b"] == ["b0"]
    since = list(trace_store.iter_session_chains(["iter-a"], after_seq=1))
    assert [r["seq"] for _, r in since] == [2]


def test_persist_disabled_switch(store_dir, monkeypatch):
    monkeypatch.setenv("GIS_TRACE_PERSIST", "0")
    assert trace_store.persist_chain(_rec("x"), session_id="off") is False
    assert trace_store.read_chains("off") == []


def test_corrupt_segment_tolerated(store_dir):
    """损坏行（截断 JSON）读取容忍 —— 不撕裂读面。"""
    assert trace_store.persist_chain(_rec("good-1"), session_id="corrupt")
    v6_dir = store_dir / ".webgis-agent" / "corrupt" / "trace_v6"
    manifest = json.loads((v6_dir / "manifest.json").read_text())
    seg_file = v6_dir / manifest["segments"][0]["file"]
    with seg_file.open("a", encoding="utf-8") as f:
        f.write('{"trunc')  # 追加截断行（写中断 chaos 形态）
    trace_store.persist_chain(_rec("good-2"), session_id="corrupt")
    recs = trace_store.read_chains("corrupt")
    assert {r["turn_id"] for r in recs} >= {"good-1", "good-2"}
    assert trace_store.last_seq("corrupt") == 2
