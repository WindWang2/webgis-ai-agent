"""SSE 序列化与事件排序 property 测试（H03，无新依赖的确定性 property）。

不引入 hypothesis/fast-check（dependency-bounds 门禁纪律）：用 seeded
LCG 生成确定性伪随机事件序列，对每条序列断言**全称不变量** —— 覆盖面
来自序列空间的系统化采样，而非手写用例枚举：

INV-1  scope 内 ``id:`` 逐事件严格 +1，且与发射序一致；scope 外无 id。
INV-2  序列化永不抛（任意 Python 对象载荷 → 降级为 error JSON 帧），
       帧结构始终可被 sse_event_type/sse_event_id 防御解析。
INV-3  SSEBatcher：输出保持输入顺序；terminal 帧必是某次 flush 的最后一帧
       且立即触发 flush（不被窗口延迟）；批边界任意切分不改变事件序列
       （合并/重拆一致性 —— resume replay 的前提）。
INV-4  TurnEventBuffer.replay_after：对任意 last_event_id，回放 = 原序列
       中 id > last_event_id 的子序列（保序、去 commentary），ring 裁剪时
       以 resume_gap 开头且不撒谎（gap 范围与缺失 id 一致）。
INV-5  TurnEventEmitter 载荷 JSON 安全且 session_id 恒在场。
"""
from __future__ import annotations

import json

from app.services.chat.event_resume import (
    RESUME_GAP_EVENT_TYPE,
    RESUME_MAX_EVENTS,
    TurnEventBuffer,
)
from app.services.chat.sse_contracts import TurnEventEmitter
from app.utils.sse import (
    SSEBatcher,
    TERMINAL_EVENTS,
    sse_event,
    sse_event_id,
    sse_event_id_scope,
    sse_event_type,
)


class LCG:
    """确定性伪随机（mulberry 风格 32bit LCG）—— property 可重复。"""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFF

    def next(self, modulus: int) -> int:
        self.state = (self.state * 1664525 + 1013904223) & 0xFFFFFFFF
        return (self.state >> 8) % modulus


def _split_frames(chunk: str) -> list[str]:
    return [b for b in chunk.split("\n\n") if b]


# ── INV-1/2: serializer ────────────────────────────────────────────────


def test_ids_strictly_monotonic_across_random_event_mix():
    for seed in range(40):
        rng = LCG(seed)
        em = TurnEventEmitter(session_id="s", turn_id="t")
        kinds = ["token", "content", "keep_alive", "step_result", "done"]
        ids = []
        with sse_event_id_scope():
            for _ in range(60):
                kind = kinds[rng.next(len(kinds))]
                if kind == "token":
                    frame = em.token("x" * rng.next(20))
                elif kind == "content":
                    frame = em.content("y")
                elif kind == "keep_alive":
                    frame = em.keep_alive()
                elif kind == "step_result":
                    frame = em.step_result("task", "s", "tool", {"i": rng.next(10)})
                else:
                    frame = em.done()
                eid = sse_event_id(frame)
                assert eid is not None, "scope 内事件必须带 id"
                ids.append(eid)
        assert ids == list(range(1, len(ids) + 1)), f"seed={seed} id 非严格单调"


def test_serializer_never_raises_on_hostile_payloads():
    hostile = [
        object(),  # 不可 JSON 序列化
        {"bad": {1, 2}},  # set
        {"cycle": None, "nan": float("nan")},
        {"deep": [{"k": (b"bytes",)}]},
        {"self": object()},
    ]
    for payload in hostile:
        frame = sse_event("token", payload)
        assert frame.startswith("event: token\ndata: ")
        assert frame.endswith("\n\n")
        # 防御解析不抛
        assert sse_event_type(frame) == "token"
        data_line = next(ln for ln in frame.split("\n") if ln.startswith("data: "))
        json.loads(data_line[len("data: "):])  # 降级载荷仍是合法 JSON


def test_pydantic_like_objects_serialize_via_model_dump():
    class FakeV2:
        def model_dump(self):
            return {"ok": True}

    class FakeV1:
        def dict(self):
            return {"ok": False}

    assert '"ok": true' in sse_event("t", FakeV2())
    assert '"ok": false' in sse_event("t", FakeV1())


# ── INV-3: SSEBatcher ─────────────────────────────────────────────────


def _random_stream_frames(rng: LCG, n: int) -> list[str]:
    em = TurnEventEmitter(session_id="s")
    terminal = list(TERMINAL_EVENTS)
    frames = []
    for i in range(n):
        roll = rng.next(10)
        if roll < 5:
            frames.append(em.token(f"tok-{i}"))
        elif roll < 7:
            frames.append(sse_event("step_result", {"i": i, "session_id": "s"}))
        elif roll < 8:
            frames.append(sse_event("keep_alive", {"message": "ping"}))
        else:
            frames.append(
                sse_event(terminal[rng.next(len(terminal))], {"session_id": "s"})
            )
    return frames


def test_batcher_preserves_order_and_partition_invariance():
    for seed in range(40):
        rng = LCG(seed)
        frames = _random_stream_frames(rng, 80)
        # 逐帧直出的规范序列
        expected = [
            b for f in frames for b in _split_frames(f)
        ]
        # 以随机阈值 k 真实合批（k 帧一 flush + 尾 flush），重拆后必须得到
        # 同一事件序列 —— 合并/重拆一致性是 resume replay 的前提。
        k = 1 + rng.next(16)
        batcher = SSEBatcher(max_events=k, max_delay_s=3600.0)
        got: list[str] = []
        for f in frames:
            batcher.push(f)
            if len(batcher) >= k:
                for chunk in batcher.flush():
                    got.extend(_split_frames(chunk))
        for chunk in batcher.flush():
            got.extend(_split_frames(chunk))
        assert got == expected, f"seed={seed} 批处理改变事件序列"


def test_batcher_terminal_flushes_immediately_and_is_last():
    for seed in range(40):
        rng = LCG(seed)
        em = TurnEventEmitter(session_id="s")
        batcher = SSEBatcher(max_events=10_000, max_delay_s=3600.0)
        for i in range(1 + rng.next(20)):
            batcher.push(em.token("t"))
        assert len(batcher) > 0
        # terminal 入队后：任何一次 drain 必须立即产出，且 terminal 是末帧
        terminal_frame = em.done()
        batcher.push(terminal_frame)
        flushed = "".join(batcher.flush())
        blocks = _split_frames(flushed)
        assert blocks, "terminal 必须立即触发 flush"
        assert sse_event_type(blocks[-1]) == "done"
        assert all(sse_event_type(b) != "done" for b in blocks[:-1])


# ── INV-4: resume replay ──────────────────────────────────────────────


def test_replay_after_is_order_preserving_subsequence():
    for seed in range(40):
        rng = LCG(seed)
        em = TurnEventEmitter(session_id="s")
        buffer = TurnEventBuffer("s", "msg", max_events=RESUME_MAX_EVENTS)
        sent_ids: list[int] = []
        with sse_event_id_scope():
            for i in range(60):
                if rng.next(3) == 0:
                    buffer.record(": keepalive\n\n")  # 注释帧不占 id
                    continue
                frame = em.token(f"c{i}") if rng.next(2) else em.step_result(
                    "task", f"s{i}", "tool", {}
                )
                buffer.record(frame)
                eid = sse_event_id(frame)
                assert eid is not None
                sent_ids.append(eid)
        for cut in {0, len(sent_ids) // 3, len(sent_ids) // 2, max(0, len(sent_ids) - 1)}:
            last_seen = sent_ids[cut] if cut < len(sent_ids) else 0
            replayed = buffer.replay_after(last_seen)
            got_ids = [sse_event_id(b) for b in _split_frames("".join(replayed))]
            got_ids = [i for i in got_ids if i is not None]
            want = [i for i in sent_ids if i > last_seen]
            # ring 裁剪：首保留 id > last_seen+1 → 回放以 resume_gap 开头
            if want and sent_ids[0] > last_seen + 1:
                assert replayed, "裁剪时必须有 gap 标记"
                first_type = sse_event_type(replayed[0].split("\n\n")[0])
                assert first_type == RESUME_GAP_EVENT_TYPE
                want = [i for i in want if i >= sent_ids[0]]
            assert got_ids == want, f"seed={seed} cut={cut} 回放子序列不一致"


def test_replay_gap_reports_missing_range_honestly():
    buffer = TurnEventBuffer("s", "m", max_events=2)
    with sse_event_id_scope():
        em = TurnEventEmitter(session_id="s")
        for i in range(5):
            buffer.record(em.token(str(i)))
    # id 1..5 记录，ring 只剩 4..5 → 从 0 重放必须先报 gap(1..3)
    replayed = buffer.replay_after(0)
    first_block = replayed[0].split("\n\n")[0]
    assert sse_event_type(first_block) == RESUME_GAP_EVENT_TYPE
    data = json.loads(
        next(ln for ln in first_block.split("\n") if ln.startswith("data: "))[len("data: "):]
    )
    assert data["missing_from"] == 1 and data["missing_to"] == 3
    got_ids = [sse_event_id(b) for b in _split_frames("".join(replayed))]
    assert [i for i in got_ids if i is not None] == [4, 5]


# ── INV-5: emitter payloads JSON-safe ─────────────────────────────────


def test_emitter_payloads_always_json_safe():
    for seed in range(20):
        rng = LCG(seed)
        em = TurnEventEmitter(session_id="s", turn_id="t")
        frame = em.step_result(
            "task", "s1", "tool",
            {"payload": "x" * rng.next(50), "n": rng.next(1000)},
            geojson_ref="ref:x" if rng.next(2) else None,
        )
        data_line = next(ln for ln in frame.split("\n") if ln.startswith("data: "))
        data = json.loads(data_line[len("data: "):])
        assert data["session_id"] == "s"
