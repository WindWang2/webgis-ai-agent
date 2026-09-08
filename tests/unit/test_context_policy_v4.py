"""ADR-0104 决策 #6（Wave 5）：Context Policy 六操作执行器测试。

锁定语义：
- 六操作 KEEP / CONDENSE / OFFLOAD_REF / SUMMARIZE / DROP_OLDEST / RELOAD_REF
  的行为（advisor 仍是唯一建议源；policy 是执行器，不是第二预算模型）；
- 确定性：同输入 → 同决策/同输出（含 32K/64K/128K/256K 合成规模）；
- KEEP pin：安全相关内容（Tier-3 确认回执 / 自愈错误 / 制图裁决）在压力下
  不被历史截断与轮内折叠丢失；
- RELOAD_REF：session store 逐出 → 持久副本 → resolver 回退（ref→durable）+
  诚实 tombstone；
- kill switch（GIS_CONTEXT_POLICY=0）恢复 advice-only 旧行为；
- 窗口未知诚实性（window_unknown 代替慢性假 over_budget）；
- 估算器错误防护（CJK/ASCII 混排、怪异输入）；
- provider CONTEXT_TOO_LARGE 的确定性重裁阶梯（恰好 1 次，绝不静默）。

估算精度口径（documented tolerance）：``_estimate_tokens`` 是有意偏高 ~30% 的
粗估（CJK 1 字 ≈ 1.5 tok、ASCII 4 字 ≈ 1 tok，#729 口径）；本文件的
「不超预算」断言均在**同一估算器**语义下度量（决策与度量同源，自洽精确），
对 provider 真实 token 的换算偏差由估算器本身的偏高偏置覆盖。
"""
from __future__ import annotations

import json
import time
import uuid

import httpx
import pytest

from app.services.chat import context_policy as cp
from app.services.chat.context import history_compression as hc
from app.services.chat.context.history_compression import (
    _estimate_tokens,
    find_safety_pinned_indexes,
    fold_intra_turn_tool_results,
    truncate_history_by_budget,
)
from app.services.chat.context_budget import (
    Category,
    GisBudgetAdvisor,
    BudgetItem,
    measure_assembled_context,
    measure_components,
    plan_budget,
)
from app.services.chat.context_policy import (
    ContextOp,
    apply_condense_actions,
    build_evicted_refs_tombstone,
    build_policy_items,
    condense_text_to_budget,
    execute_advice,
    offload_pass,
    policy_enabled,
    retrim_messages_for_retry,
    run_history_ops,
    summarize_dropped_turns,
)


# ---------------------------------------------------------------------------
# 测试基座：确定性 fake store + 合成轮次
# ---------------------------------------------------------------------------


class FakePolicyStore:
    """deterministic ref store（ref id 单调，无随机性）+ metadata 面。"""

    def __init__(self, metadata: dict | None = None):
        self._metadata = metadata or {}
        self.refs: dict[str, object] = {}
        self.counter = 0

    async def get_session_metadata(self, session_id):
        return self._metadata

    async def ref_exists(self, session_id, ref_id):
        return ref_id in self.refs

    async def store(self, session_id, data, prefix="data"):
        self.counter += 1
        ref = f"ref:data-{self.counter:016x}"
        self.refs[ref] = data
        return ref


def _turn(i: int, payload_chars: int = 600, cjk_ratio: float = 0.0, marker: str | None = None) -> list[dict]:
    """一个用户轮：user + assistant(tool_call) + tool 结果。"""
    ascii_chars = int(payload_chars * (1 - cjk_ratio))
    cjk_chars = payload_chars - ascii_chars
    payload = "x" * ascii_chars + "字" * cjk_chars
    content = json.dumps({"seq": i, "result": payload}, ensure_ascii=False)
    if marker:
        content = f"{marker}\n{content}"
    return [
        {"role": "user", "content": f"第{i}问：请处理数据 字" * 2},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call_{i}", "type": "function",
                "function": {"name": f"tool_{i}", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": f"call_{i}", "content": content},
    ]


def _history(n_turns: int, **kw) -> list[dict]:
    msgs: list[dict] = []
    for i in range(n_turns):
        msgs.extend(_turn(i, **kw))
    return msgs


@pytest.fixture
def kill_switch_off(monkeypatch):
    monkeypatch.setenv("GIS_CONTEXT_POLICY", "0")


@pytest.fixture
def kill_switch_on(monkeypatch):
    monkeypatch.setenv("GIS_CONTEXT_POLICY", "1")


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


def test_kill_switch_env_parsing(monkeypatch):
    monkeypatch.setenv("GIS_CONTEXT_POLICY", "0")
    assert policy_enabled() is False
    monkeypatch.setenv("GIS_CONTEXT_POLICY", "false")
    assert policy_enabled() is False
    monkeypatch.setenv("GIS_CONTEXT_POLICY", "1")
    assert policy_enabled() is True
    monkeypatch.delenv("GIS_CONTEXT_POLICY", raising=False)
    assert policy_enabled() is True  # 默认开


def test_kill_switch_restores_legacy_history_ops(kill_switch_off):
    msgs = _history(24, payload_chars=1200)
    # 无安全标记时：策略管线的裁剪决策与 legacy 路径完全一致（pin=∅）
    hist = run_history_ops(msgs)
    legacy_folded = fold_intra_turn_tool_results(msgs)
    legacy_kept, legacy_dropped = truncate_history_by_budget(legacy_folded)
    assert hist["dropped_turns"] == legacy_dropped
    assert hist["pinned_count"] == 0
    assert ContextOp.SUMMARIZE.value not in hist["ops"]
    # 策略开（同输入、无标记）→ 裁剪决策不变，只是 notice 多了摘要段
    monkey_on = pytest.MonkeyPatch()
    monkey_on.setenv("GIS_CONTEXT_POLICY", "1")
    try:
        hist_on = run_history_ops(msgs)
    finally:
        monkey_on.undo()
    assert hist_on["dropped_turns"] == legacy_dropped
    assert hist_on["notice"].startswith(hc._build_truncation_notice(legacy_dropped))
    assert ContextOp.SUMMARIZE.value in hist_on["ops"]


@pytest.mark.asyncio
async def test_kill_switch_assemble_byte_identical_legacy(monkeypatch, kill_switch_off):
    """策略关闭时 assemble 的行为 = 旧路径（notice 文案 + 无策略产物）。"""
    from app.services.chat.context_assembler import ChatContextAssembler

    store = FakePolicyStore()
    assembler = ChatContextAssembler(store=store)
    msgs = [{"role": "system", "content": "SYS"}]
    msgs.extend(_history(24, payload_chars=1200))
    msgs[-1]["content"] += "\nref:data-deadbeef0001"  # 历史里带 ref → 关闭时无 tombstone

    result = await assembler.assemble("ks-off-session", msgs)

    joined = "\n".join(str(m.get("content")) for m in result.messages)
    assert "[引用状态]" not in joined
    assert "context_policy" not in (result.budget_report or {})
    # 截断 notice 与 legacy 静态文案完全一致
    notices = [m["content"] for m in result.messages
               if m.get("role") == "system" and "历史折叠" in str(m.get("content"))]
    assert len(notices) == 1
    assert notices[0].startswith("[历史折叠] 已省略最早")


# ---------------------------------------------------------------------------
# DROP_OLDEST + SUMMARIZE
# ---------------------------------------------------------------------------


def test_drop_oldest_and_fingerprinted_summary(kill_switch_on):
    msgs = _history(24, payload_chars=1200)
    hist = run_history_ops(msgs)
    assert hist["dropped_turns"] > 0
    assert len(hist["kept"]) < len(msgs)
    # 最新轮保留
    assert hist["kept"][-1] == msgs[-1]
    # SUMMARIZE：notice 携带指纹化确定性摘要（逐轮「用户:」行）
    assert "[历史折叠]" in hist["notice"]
    assert "用户:" in hist["notice"]
    assert ContextOp.DROP_OLDEST.value in hist["ops"]
    assert ContextOp.SUMMARIZE.value in hist["ops"]


def test_summarize_deterministic_and_cached(kill_switch_on):
    dropped = _history(6, payload_chars=300)
    s1 = summarize_dropped_turns(dropped)
    s2 = summarize_dropped_turns(dropped)
    assert s1 == s2
    assert s1 != ""
    # 指纹缓存命中：同内容 → 同摘要（ProjectionCache stats 命中数增长）
    stats_before = cp._turn_summary_cache.stats()["hits"]
    summarize_dropped_turns(dropped)
    assert cp._turn_summary_cache.stats()["hits"] == stats_before + 1
    # 指纹按规范形内容计算：载荷长度变化 → 指纹变化（摘要本身是有损投影，
    # 不保证不同指纹必然不同文本 —— 锁定的是指纹敏感性，不是摘要唯一性）
    from app.services.chat.context_projections import content_fingerprint
    fp_a = content_fingerprint([cp._canonical_turn_content(m) for m in dropped])
    other = _history(6, payload_chars=301)
    fp_b = content_fingerprint([cp._canonical_turn_content(m) for m in other])
    assert fp_a != fp_b


def test_summarize_bounded(kill_switch_on):
    dropped = _history(40, payload_chars=2000)
    summary = summarize_dropped_turns(dropped)
    assert len(summary) <= cp._SUMMARY_TOTAL_MAX_CHARS


# ---------------------------------------------------------------------------
# KEEP pin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("marker", [
    "[工具执行失败] tool_x | 校验: bad arg",
    "[CARTOGRAPHY_VERDICT] {\"verdict\": \"fail\"}",
    "计划包含破坏性/高危步骤 (Tier 3: delete_layer)，需要 confirm_destructive",
])
def test_keep_pin_survives_truncation_under_pressure(kill_switch_on, marker):
    msgs = _history(24, payload_chars=1200)
    # 第 2 轮（最老区域）的工具结果带安全标记
    pinned_turn = _turn(99, payload_chars=100, marker=marker)
    msgs = msgs[:3] + pinned_turn + msgs[3:]

    pinned = find_safety_pinned_indexes(msgs)
    assert pinned, "安全标记必须被探测到"
    hist = run_history_ops(msgs)
    joined = "\n".join(str(m.get("content")) for m in hist["kept"])
    assert marker in joined, "pinned 安全事实必须存活于截断后视图"
    # 同时非 pinned 的最老轮次确实被丢了（有压力存在）
    assert hist["dropped_turns"] > 0


def test_keep_pin_survives_intra_turn_fold(kill_switch_on):
    # 当前回合内 16 条 tool 结果；其中一条带自愈错误标记
    tail: list[dict] = [{"role": "user", "content": "当前回合问题"}]
    for i in range(16):
        tail.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": f"t{i}", "arguments": "{}"}}],
        })
        content = f"[工具执行失败] t{i} | x: y" if i == 2 else json.dumps({"i": i, "d": "z" * 100})
        tail.append({"role": "tool", "tool_call_id": f"c{i}", "content": content})

    # 无 pin：第 2 条被折叠
    folded_plain = fold_intra_turn_tool_results(list(tail))
    plain_texts = [m["content"] for m in folded_plain if m.get("role") == "tool"]
    assert "已折叠" in plain_texts[2]

    # 有 pin：第 2 条（下标 5 = user 后第 2 个 tool）原文保留
    tool_indexes = [i for i, m in enumerate(tail) if m.get("role") == "tool"]
    pinned = {tool_indexes[2]}
    folded_pinned = fold_intra_turn_tool_results(list(tail), pinned=pinned)
    pinned_texts = [m["content"] for m in folded_pinned if m.get("role") == "tool"]
    assert "已折叠" not in pinned_texts[2]
    assert "工具执行失败" in pinned_texts[2]
    # 其余早期 tool 结果仍被折叠
    assert "已折叠" in pinned_texts[3]


def test_pin_bound_max_pinned_turns(kill_switch_on):
    msgs = _history(40, payload_chars=800)
    # 全部轮次都带安全标记 → pin 有界（最多 MAX_PINNED_TURNS 轮受保护）
    for i in range(0, len(msgs), 3):
        c = msgs[i].get("content") or ""
        msgs[i] = {**msgs[i], "content": c + " [CARTOGRAPHY_VERDICT]"}
    pinned = find_safety_pinned_indexes(msgs)
    hist = run_history_ops(msgs)
    assert pinned
    assert len(hist["kept"]) < len(msgs), "pin 有界：过量 pinned 轮次仍受预算约束"


def test_no_pin_when_policy_disabled(kill_switch_off):
    msgs = _history(24, payload_chars=1200)
    pinned_turn = _turn(99, payload_chars=100, marker="[工具执行失败] x | y: z")
    msgs = msgs[:3] + pinned_turn + msgs[3:]
    hist = run_history_ops(msgs)
    # 关闭时 pin 探测完全不参与（advice-only 语义）
    assert hist["pinned_count"] == 0
    assert ContextOp.KEEP.value not in hist["ops"]


# ---------------------------------------------------------------------------
# CONDENSE
# ---------------------------------------------------------------------------


def test_condense_text_to_budget_deterministic():
    text = "字" * 5000  # ≈7500 tokens
    capped, changed = condense_text_to_budget(text, 1000)
    assert changed
    assert _estimate_tokens(capped) <= 1000
    assert "(truncated)" in capped
    again, _ = condense_text_to_budget(text, 1000)
    assert again == capped
    # 无需压缩时原样返回
    same, changed2 = condense_text_to_budget("短文本", 1000)
    assert same == "短文本" and changed2 is False


def test_apply_condense_actions_executes_advisor_flags(kill_switch_on):
    head = [
        {"role": "system", "content": "CORE " + "s" * 100},
        {"role": "system", "content": "[环境感知]\n" + "字" * 8000},
        {"role": "system", "content": "PLAN " + "p" * 9000},
    ]
    head_meta = [
        {"name": "map_state_env", "category": "MAP_STATE", "index": 1, "base_chars": 6},
        {"name": "session_plan", "category": "SESSION_PLAN", "index": 2},
    ]
    plan = plan_budget(context_window=32_000, max_output_tokens=4_096)
    items = build_policy_items(head, head_meta, "")
    advice = GisBudgetAdvisor(plan).advise(items)
    condensable = [a for a in advice.actions if a["action"] == "condense"]
    assert condensable, "超分区 head 块必须被 advisor 建议 condense"

    before_tokens = {m["name"]: _estimate_tokens(head[m["index"]]["content"]) for m in head_meta}
    head_backup = [dict(m) for m in head]  # 先留原始副本再落刀（确定性对照）
    decisions = apply_condense_actions(head, head_meta, condensable)
    assert {d["target"] for d in decisions} <= {"map_state_env", "session_plan"}
    assert decisions, "建议必须被真实执行"
    for d in decisions:
        assert d["op"] == ContextOp.CONDENSE.value
        assert d["after_tokens"] <= d["section_cap"]
        assert d["after_tokens"] < before_tokens[d["target"]]
    # 未登记 meta 的 head 块（CORE，index 0）永不落刀；env 块的 core 前缀
    # （base_chars 之前）同样保留，只压缩登记的尾部。
    assert head[0]["content"] == "CORE " + "s" * 100
    assert head[1]["content"].startswith("[环境感知]")
    # 确定性：同一原始输入 + 同一 actions → 逐字节同输出
    decisions2 = apply_condense_actions(head_backup, head_meta, condensable)
    assert [d["target"] for d in decisions2] == [d["target"] for d in decisions]
    assert [m["content"] for m in head_backup] == [m["content"] for m in head]


def test_condense_ordering_follows_category_priority(kill_switch_on):
    """执行顺序遵循 advisor 的建议序（同输入确定性排序：类别值大者先被裁）。"""
    head = [
        {"role": "system", "content": "TRACE " + "t" * 20000},   # TRACE_SUMMARIES (11)
        {"role": "system", "content": "PROFILE " + "p" * 20000}, # DATA_PROFILE (12)
    ]
    head_meta = [
        {"name": "last_analysis", "category": "TRACE_SUMMARIES", "index": 0},
        {"name": "project_memory", "category": "DATA_PROFILE", "index": 1},
    ]
    plan = plan_budget(context_window=32_000, max_output_tokens=4_096)
    items = build_policy_items(head, head_meta, "")
    advice = GisBudgetAdvisor(plan).advise(items)
    actions = [a for a in advice.actions if a["action"] == "condense"]
    assert len(actions) == 2
    # advisor 建议序按 (category.value asc, est desc)：值大者（裁剪优先级高）先建议
    assert actions[0]["category"] == "TRACE_SUMMARIES"
    assert actions[1]["category"] == "DATA_PROFILE"
    decisions = apply_condense_actions(head, head_meta, actions)
    assert [d["category"] for d in decisions] == ["TRACE_SUMMARIES", "DATA_PROFILE"]


# ---------------------------------------------------------------------------
# OFFLOAD_REF
# ---------------------------------------------------------------------------


async def test_offload_pass_converts_inline_results(kill_switch_on):
    store = FakePolicyStore()
    big_tool = {
        "role": "tool", "tool_call_id": "c0",
        "content": json.dumps({"result": "y" * 20_000, "ref_id": "ref:geojson-abc123456789"}),
    }
    head = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c0", "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
        big_tool,
    ]
    session_view = list(head)  # 模拟会话库视图共享同一 dict
    decisions = await offload_pass(
        "s1", head, store, over_tokens=5_000, history_start=1,
    )
    assert len(decisions) == 1
    assert decisions[0]["op"] == ContextOp.OFFLOAD_REF.value
    new_ref = decisions[0]["ref"]
    assert new_ref and new_ref.startswith("ref:data-")
    assert store.refs[new_ref]["result"] == "y" * 20_000
    new_content = head[3]["content"]
    assert new_ref in new_content
    # 既有 ref 游标永不丢失（可解析性只增不减）
    assert "ref:geojson-abc123456789" in new_content
    assert len(new_content) < len(big_tool["content"])
    # 视图替换，绝不改会话库内消息
    assert session_view[3]["content"] == big_tool["content"]
    assert head[3] is not session_view[3]


async def test_offload_pass_bounded_and_noop_when_under_budget(kill_switch_on):
    store = FakePolicyStore()
    head = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "q"},
    ]
    for i in range(12):
        head.extend(_turn(i, payload_chars=3000))
    decisions = await offload_pass(
        "s1", head, store, over_tokens=10_000, history_start=2,
    )
    assert len(decisions) == cp.OFFLOAD_MAX_ITEMS, "单次卸载有界"
    # 无超预算压力 → 不落刀
    decisions0 = await offload_pass("s1", list(head), store, over_tokens=0, history_start=2)
    assert decisions0 == []


async def test_offload_without_store_still_bounds_honestly(kill_switch_on):
    """store 不可用（如 fake 无 store 方法）→ 只做有界化，不破坏原文引用。"""
    head = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c0", "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c0",
         "content": json.dumps({"d": "z" * 30_000, "ref_id": "ref:geojson-keeper000001"})},
    ]
    decisions = await offload_pass("s1", head, object(), over_tokens=9_000, history_start=1)
    assert decisions and decisions[0]["ref"] is None
    assert "ref:geojson-keeper000001" in head[3]["content"]
    assert len(head[3]["content"]) < 30_000


# ---------------------------------------------------------------------------
# RELOAD_REF：逐出 → 持久副本 → resolver 回退 + tombstone
# ---------------------------------------------------------------------------


class _SpillResolverStore:
    """永远 miss 的内存 store（ref_exists + get_ref_descriptor + get）。"""

    async def ref_exists(self, session_id, ref_id):
        return False

    async def get_ref_descriptor(self, session_id, ref_id):
        return None

    async def get(self, session_id, ref_id):
        return None


def test_spill_store_roundtrip_and_ttl(tmp_path, monkeypatch):
    from app.services.session_data import RefSpillStore

    spill = RefSpillStore(str(tmp_path), ttl_s=60, max_session_bytes=1_000_000,
                          max_single_bytes=100_000)
    payload = {"type": "FeatureCollection", "features": [{"i": 1}]}
    assert spill.spill("s1", "ref:geojson-abc", payload) is True
    assert spill.has("s1", "ref:geojson-abc")
    assert spill.load("s1", "ref:geojson-abc") == payload
    assert not spill.has("s1", "ref:geojson-missing")
    assert spill.load("s1", "ref:geojson-missing") is None

    # TTL 过期 → miss（mtime 老化）
    import os
    path = spill._ref_path("s1", "ref:geojson-abc")
    old = time.time() - 3600
    os.utime(path, (old, old))
    assert spill.has("s1", "ref:geojson-abc") is False
    assert spill.load("s1", "ref:geojson-abc") is None


def test_spill_store_per_session_cap_evicts_oldest(tmp_path):
    from app.services.session_data import RefSpillStore

    spill = RefSpillStore(str(tmp_path), ttl_s=3600, max_session_bytes=2_000,
                          max_single_bytes=1_000)
    assert spill.spill("s1", "ref:data-a1", {"d": "x" * 800})
    assert spill.spill("s1", "ref:data-a2", {"d": "x" * 800})
    # 第三次写触发 cap 淘汰（最旧先走）
    assert spill.spill("s1", "ref:data-a3", {"d": "x" * 800})
    remaining = [r for r in ("ref:data-a1", "ref:data-a2", "ref:data-a3") if spill.has("s1", r)]
    assert len(remaining) == 2
    assert "ref:data-a1" not in remaining  # 最旧被淘汰

    # 单 payload 超上限 → 拒绝落盘
    assert spill.spill("s1", "ref:data-big", {"d": "x" * 5000}) is False

    # 会话清除 → purge
    spill.purge_session("s1")
    assert not spill.has("s1", "ref:data-a2")


def test_spill_disabled_by_env(tmp_path, monkeypatch):
    from app.services.session_data import RefSpillStore

    monkeypatch.setenv("GIS_REF_SPILL", "0")
    spill = RefSpillStore(str(tmp_path))
    assert spill.spill("s1", "ref:data-x", {"a": 1}) is False
    assert spill.has("s1", "ref:data-x") is False


async def test_evicted_ref_spills_and_resolver_reloads(tmp_path, monkeypatch):
    """淘汰 → spill → resolver ref→durable 回退（RELOAD_REF 全链路）。"""
    from app.services.session_data import MemorySessionStore
    from app.lib.harness.ref_resolver import make_session_store_resolver
    from app.lib.harness.evidence import RefResolutionStatus

    monkeypatch.setenv("GIS_REF_SPILL_DIR", str(tmp_path / "spill"))
    store = MemorySessionStore(capacity=2)
    r1 = await store.store("s1", {"type": "FeatureCollection", "features": [{"a": 1}]}, prefix="geojson")
    await store.store("s1", {"type": "FeatureCollection", "features": [{"b": 2}]}, prefix="geojson")
    await store.store("s1", {"type": "FeatureCollection", "features": [{"c": 3}]}, prefix="geojson")

    assert not await store.ref_exists("s1", r1), "最老 ref 必然被 LRU 淘汰"
    # 内存已无，但 resolver 经持久副本回退 → RESOLVED + reload 溯源
    resolve = make_session_store_resolver(store)
    res = await resolve("s1", r1)
    assert res.status is RefResolutionStatus.RESOLVED
    assert res.detail == "reloaded_from_durable_spill"

    # 会话清除 → 持久副本 purge → 诚实 NOT_FOUND（"gone"）
    await store.clear_session("s1")
    res2 = await resolve("s1", r1)
    assert res2.status is RefResolutionStatus.NOT_FOUND


async def test_tombstone_distinguishes_reloadable_vs_gone(tmp_path, monkeypatch, kill_switch_on):
    monkeypatch.setenv("GIS_REF_SPILL_DIR", str(tmp_path / "spill"))
    from app.services.session_data import ref_spill_store

    # ref:geojson-reload0001：内存无、持久副本在 → 可重载
    ref_spill_store.spill("s1", "ref:geojson-reload0001", {"type": "FeatureCollection"})
    # ref:geojson-gone000001：内存无、副本无 → 已失效
    # ref:geojson-alive00001：内存仍在 → 不进 tombstone
    store = FakePolicyStore()
    store.refs["ref:geojson-alive00001"] = {"x": 1}
    head = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "看下 ref:geojson-alive00001 和 ref:geojson-reload0001"},
        {"role": "tool", "tool_call_id": "c", "content": "结果在 ref:geojson-gone000001"},
    ]
    notice = await build_evicted_refs_tombstone("s1", head, store)
    assert "[引用状态]" in notice
    assert "自动重载" in notice          # reloadable
    assert "ref:geojson-reload0001" in notice
    assert "已失效" in notice            # gone
    assert "ref:geojson-gone000001" in notice
    assert "ref:geojson-alive00001" not in notice, "在存 ref 不得误报"
    # 有界
    assert len(notice) <= cp._TOMBSTONE_MAX_CHARS


async def test_tombstone_silent_when_all_refs_alive(kill_switch_on):
    store = FakePolicyStore()
    store.refs["ref:data-aabbccdd0001"] = {"x": 1}
    head = [{"role": "user", "content": "数据在 ref:data-aabbccdd0001"}]
    assert await build_evicted_refs_tombstone("s1", head, store) == ""


async def test_tombstone_skip_store_without_ref_exists(kill_switch_on):
    head = [{"role": "user", "content": "ref:data-aabbccdd0002"}]
    assert await build_evicted_refs_tombstone("s1", head, object()) == ""


# ---------------------------------------------------------------------------
# 窗口未知诚实性（audit #6：慢性假 over_budget）
# ---------------------------------------------------------------------------


def test_window_unknown_reports_honest_not_violation():
    plan = plan_budget(context_window=0, max_output_tokens=16_384)
    assert plan.context_window == 8192
    assert plan.window_known is False
    assert plan.as_dict().get("window_known") is False
    items = [BudgetItem(category=Category.HISTORY, name="history", text="字" * 40_000)]
    rep = measure_components(items, plan)
    assert rep.over_budget is False, "窗口未知时不得断言必然违规"
    assert rep.window_unknown is True
    assert "window_unknown" in rep.as_dict()
    assert any(w.startswith("window_unknown") for w in rep.warnings)
    # 配置窗口 → 恢复必然违规语义（行为不变）
    plan2 = plan_budget(context_window=100_000, max_output_tokens=4_000)
    rep2 = measure_components([BudgetItem(category=Category.HISTORY, name="history", text="字" * 400_000)], plan2)
    assert rep2.over_budget is True
    assert rep2.as_dict().get("window_unknown") is None


def test_advisor_overflow_honest_when_window_unset():
    advisor = GisBudgetAdvisor()
    items = [BudgetItem(category=Category.HISTORY, name="history", est_tokens=7_000)]
    advice = advisor.advise(items, context_window=None, max_output_tokens=16_384)
    assert advice.window_known is False
    assert advice.overflow_reason is None, "慢性假 over_budget（8k 规划 vs 6000 软预算）必须消失"
    assert advice.as_dict().get("window_known") is False
    # 配置窗口 → 溢出判定恢复
    advice2 = GisBudgetAdvisor().advise(
        [BudgetItem(category=Category.HISTORY, name="history", est_tokens=90_000)],
        context_window=32_768, max_output_tokens=4_096,
    )
    assert advice2.window_known is True
    assert advice2.overflow_reason is not None and advice2.overflow_reason.startswith("total:")


def test_measure_assembled_context_unset_window_honest():
    rep = measure_assembled_context(
        [{"role": "system", "content": "s" * 90_000}, {"role": "user", "content": "问题"}],
        context_window=None, max_output_tokens=16_384,
    )
    assert rep.over_budget is False
    assert rep.window_unknown is True
    assert rep.as_dict().get("window_unknown") is True


async def test_executor_skips_when_window_unknown(kill_switch_on):
    head = [{"role": "system", "content": "TRACE " + "t" * 50_000}]
    head_meta = [{"name": "last_analysis", "category": "TRACE_SUMMARIES", "index": 0}]
    actions = [{"item": "last_analysis", "category": "TRACE_SUMMARIES",
                "action": "condense", "section_cap": 100, "over_by": 1000}]
    report = await execute_advice(
        head, head_meta, advice_actions=actions, window_known=False,
        usable_tokens=1000, session_id="s", store=FakePolicyStore(),
    )
    assert report["executed"] is False
    assert report["reason"] == "window_unknown"
    assert head[0]["content"].startswith("TRACE ")  # 未落刀


# ---------------------------------------------------------------------------
# 执行器编排（execute_advice）
# ---------------------------------------------------------------------------


async def test_execute_advice_condense_then_offload(kill_switch_on):
    store = FakePolicyStore()
    head = [
        {"role": "system", "content": "CORE " + "c" * 50},
        {"role": "system", "content": "TRACE " + "t" * 30_000},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c0", "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c0", "content": json.dumps({"r": "z" * 30_000})},
    ]
    head_meta = [{"name": "last_analysis", "category": "TRACE_SUMMARIES", "index": 1}]
    actions = [{
        "item": "last_analysis", "category": "TRACE_SUMMARIES",
        "action": "condense", "section_cap": 1_000, "over_by": 5_000,
    }]
    report = await execute_advice(
        head, head_meta, advice_actions=actions, window_known=True,
        usable_tokens=6_000, session_id="s1", store=store,
        tools_tokens=0, history_start=2,
    )
    assert report["executed"] is True
    ops = [d["op"] for d in report["decisions"]]
    assert ContextOp.CONDENSE.value in ops
    assert ContextOp.OFFLOAD_REF.value in ops
    assert _estimate_tokens(head[1]["content"]) <= 1_000
    assert any("ref:data-" in str(head[i]["content"]) for i in (3, 4))


async def test_execute_advice_reports_compress_out_of_scope(kill_switch_on):
    actions = [{"item": "tool_schemas", "category": "TOOL_SCHEMAS",
                "action": "compress", "section_cap": 100, "over_by": 50}]
    report = await execute_advice(
        [{"role": "system", "content": "S"}], [], advice_actions=actions,
        window_known=True, usable_tokens=1_000_000, session_id="s",
        store=FakePolicyStore(),
    )
    compress = [d for d in report["decisions"] if d["op"] == "compress"]
    assert compress and compress[0]["executed"] is False
    assert compress[0]["reason"] == "serialized_payload_out_of_policy_scope"


# ---------------------------------------------------------------------------
# 确定性溢出恢复：retrim + llm_client 阶梯
# ---------------------------------------------------------------------------


def test_retrim_messages_tightens_history():
    msgs = [{"role": "system", "content": "SYS"}]
    msgs.extend(_history(40, payload_chars=1_500))
    trimmed = retrim_messages_for_retry(msgs)
    assert trimmed is not None
    assert trimmed[0]["content"] == "SYS"
    assert len(trimmed) < len(msgs)
    # 轮次完整：user 开头、tool 消息都能配对到前方的 assistant.tool_calls
    assert trimmed[1]["role"] == "user"
    for i, m in enumerate(trimmed[1:], start=1):
        if m.get("role") == "tool":
            assert any(
                tc.get("id") == m.get("tool_call_id")
                for prev in trimmed[1:i] if prev.get("role") == "assistant"
                for tc in prev.get("tool_calls") or []
            ), "tool 消息必须仍能配对到 assistant.tool_calls"


def test_retrim_noop_returns_none():
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]
    assert retrim_messages_for_retry(msgs) is None
    assert retrim_messages_for_retry([{"role": "system", "content": "S"}]) is None


def test_retrim_hook_registered_and_safe(kill_switch_on):
    from app.services.chat import llm_client
    # context_policy 导入时已注册（kill switch 开）
    assert llm_client._context_retrim_fn is not None
    big = [{"role": "system", "content": "S"}]
    big.extend(_history(30, payload_chars=1_500))
    trimmed = llm_client._context_retrim_fn(big)
    assert trimmed is not None and len(trimmed) < len(big)


def test_context_too_large_classification():
    import httpx
    from app.services.chat.llm_client import _is_context_too_large_failure

    request = httpx.Request("POST", "https://x.invalid/v1/chat/completions")
    resp = httpx.Response(400, text='{"error": {"message": "This model maximum context length is 8192"}}', request=request)
    assert _is_context_too_large_failure(httpx.HTTPStatusError("bad", request=request, response=resp))
    resp2 = httpx.Response(400, text='{"error": {"message": "invalid tools"}}', request=request)
    assert not _is_context_too_large_failure(httpx.HTTPStatusError("bad", request=request, response=resp2))
    assert _is_context_too_large_failure(RuntimeError("maximum context length exceeded"))
    assert not _is_context_too_large_failure(RuntimeError("connection refused"))


def _install_mock_transport(monkeypatch, handler):
    from app.services.chat import llm_client
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def _factory(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", _factory)


async def test_call_llm_overflow_ladder_retrims_once(monkeypatch, kill_switch_on):
    from app.services.chat import llm_client
    from app.services.chat.llm_client import LLMConfig

    # 显式确保生产 hook 在位（前序测试可能注销过）
    llm_client.register_context_retrim_hook(cp._policy_retrim_hook)
    calls = {"n": 0}
    seen_msg_counts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls["n"] += 1
        seen_msg_counts.append(len(body["messages"]))
        if calls["n"] == 1:
            return httpx.Response(
                400, text='{"error": {"message": "This model maximum context length is 4096 tokens"}}',
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    _install_mock_transport(monkeypatch, handler)
    cfg = LLMConfig(base_url=f"https://retrim-{uuid.uuid4().hex[:8]}.invalid", model="m", api_key="k")
    msgs = [{"role": "system", "content": "S"}]
    msgs.extend(_history(30, payload_chars=1_500))

    result = await llm_client.call_llm(cfg, msgs)
    assert result["choices"][0]["message"]["content"] == "ok"
    assert calls["n"] == 2, "恰好一次重裁重试"
    assert seen_msg_counts[1] < seen_msg_counts[0], "第二次请求必须是收紧后的输入"


async def test_call_llm_overflow_without_hook_fails_honestly(monkeypatch, kill_switch_off):
    from app.services.chat import llm_client
    from app.services.chat.llm_client import LLMConfig

    # 关闭策略 → hook 未注册（清掉可能残留的注册）→ 裸失败
    llm_client.register_context_retrim_hook(None)
    try:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(413, text='{"error": {"message": "input too long"}}')

        _install_mock_transport(monkeypatch, handler)
        cfg = LLMConfig(base_url=f"https://nohook-{uuid.uuid4().hex[:8]}.invalid", model="m", api_key="k")
        msgs = [{"role": "system", "content": "S"}]
        msgs.extend(_history(30, payload_chars=1_500))
        with pytest.raises(httpx.HTTPStatusError):
            await llm_client.call_llm(cfg, msgs)
        assert calls["n"] == 1
    finally:
        # 恢复生产注册（策略开启时的默认 hook），避免向后续测试泄漏
        llm_client.register_context_retrim_hook(cp._policy_retrim_hook)


async def test_call_llm_ladder_bounded_to_one_retry(monkeypatch, kill_switch_on):
    """重裁后仍 CONTEXT_TOO_LARGE → 原样上抛（不无限阶梯）。"""
    from app.services.chat import llm_client
    from app.services.chat.llm_client import LLMConfig

    llm_client.register_context_retrim_hook(cp._policy_retrim_hook)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text='{"error": {"message": "context length exceeded"}}')

    _install_mock_transport(monkeypatch, handler)
    cfg = LLMConfig(base_url=f"https://ladder-{uuid.uuid4().hex[:8]}.invalid", model="m", api_key="k")
    msgs = [{"role": "system", "content": "S"}]
    msgs.extend(_history(30, payload_chars=1_500))
    with pytest.raises(httpx.HTTPStatusError):
        await llm_client.call_llm(cfg, msgs)
    assert calls["n"] == 2, "1 次原始 + 1 次重裁重试，绝不更多"


async def test_call_llm_stream_overflow_ladder(monkeypatch, kill_switch_on):
    from app.services.chat import llm_client
    from app.services.chat.llm_client import LLMConfig

    llm_client.register_context_retrim_hook(cp._policy_retrim_hook)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                400, text='{"error": {"message": "input too long, context length is 8192"}}',
            )
        sse = (
            'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    _install_mock_transport(monkeypatch, handler)
    cfg = LLMConfig(base_url=f"https://stream-{uuid.uuid4().hex[:8]}.invalid", model="m", api_key="k")
    msgs = [{"role": "system", "content": "S"}]
    msgs.extend(_history(30, payload_chars=1_500))
    events = []
    async for ev in llm_client.call_llm_stream(cfg, msgs):
        events.append(ev)
    assert calls["n"] == 2
    assert any(ev[0] == "done" for ev in events)


# ---------------------------------------------------------------------------
# Pi 接缝：llm_payload 后处理 + attach_turn_context tombstone
# ---------------------------------------------------------------------------


def test_pi_llm_payload_postprocess_bounded_and_keeps_refs(kill_switch_on):
    payload = json.dumps({"result": "q" * 40_000, "ref_id": "ref:geojson-pi0000000001"})
    out = cp.policy_postprocess_llm_payload(payload, max_chars=2_000)
    assert len(out) <= 2_200
    assert "ref:geojson-pi0000000001" in out
    assert "(truncated)" in out
    # 未超限 → 原样
    assert cp.policy_postprocess_llm_payload("short") == "short"


def test_attach_turn_context_tombstone_slot():
    from app.services.chat.pi_turn_context import TURN_CONTEXT_MARKER, attach_turn_context

    out = attach_turn_context("msg", "tok", evicted_refs_block="[引用状态] x")
    assert "[引用状态] x" in out
    assert out.rstrip().endswith("do not quote or modify this marker.)")
    assert f"[{TURN_CONTEXT_MARKER}:tok]" in out
    # 不传时输出与旧形态一致（marker 之后是固定说明行）
    plain = attach_turn_context("msg", "tok")
    assert "[引用状态]" not in plain
    assert out.replace("[引用状态] x\n\n", "") == plain


# ---------------------------------------------------------------------------
# 估算器错误防护（CJK/ASCII 混排）
# ---------------------------------------------------------------------------


def test_estimator_guards_mixed_and_weird_inputs():
    cases = [
        None, "", "pure ascii 1234", "汉字混合 ascii mixed 内容",
        "emoji 😀😀😀 + combining é合", "	\n\t", "x" * 200_000, "字" * 200_000,
        {"k": ["v", 1, None]}, ["list", {"of": "stuff"}], 42, 3.14, object(),
    ]
    for case in cases:
        est = _estimate_tokens(case)
        assert isinstance(est, int) and est >= 0
        assert _estimate_tokens(case) == est  # 确定性
    # CJK 权重高于 ASCII（同字符数）
    assert _estimate_tokens("字" * 100) > _estimate_tokens("a" * 100)
    # 混排单调性：混合 ≥ 各部分单独（近似，不允许负值/塌缩）
    mixed = _estimate_tokens("abc 字abc 字")
    assert mixed >= min(_estimate_tokens("abc"), _estimate_tokens("字" * 2))


# ---------------------------------------------------------------------------
# 合成规模：32K / 64K / 128K / 256K
# ---------------------------------------------------------------------------


def _synthetic_case(window: int, n_turns: int, payload_chars: int) -> dict:
    """合成场景：超预算历史 + 老 pinned 裁决 + 超 MAP_STATE 分区的 env 块。"""
    metadata = {
        "map_state": {"viewport": {"center": [104.0, 30.0], "zoom": 10}, "layers": {}},
        "list_refs": {},
        "event_log": [],
        "started_at": None,
    }
    msgs = [
        {"role": "system", "content": "你是 WebGIS 助手。" * 20},
        {"role": "user", "content": "分析成都的数据 字"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c-verdict", "type": "function",
             "function": {"name": "cartographic_review", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c-verdict",
         "content": "[CARTOGRAPHY_VERDICT] {\"verdict\": \"fail\", \"checks\": []}"},
    ]
    for i in range(n_turns):
        msgs.extend(_turn(i, payload_chars=payload_chars, cjk_ratio=0.25))
    return {"metadata": metadata, "messages": msgs, "window": window}


@pytest.mark.parametrize("size", [32_768, 65_536, 131_072, 262_144])
async def test_synthetic_scale_budget_and_determinism(monkeypatch, kill_switch_on, size):
    from app.core.config import settings
    from app.services.chat.context_assembler import ChatContextAssembler

    n_turns = {32_768: 90, 65_536: 180, 131_072: 360, 262_144: 720}[size]
    case = _synthetic_case(size, n_turns=n_turns, payload_chars=300)
    monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", size, raising=False)

    # 各自独立的确定性 store（ref id 单调无随机）→ 两次运行完全同构
    result1 = await ChatContextAssembler(store=FakePolicyStore(metadata=case["metadata"])).assemble(
        "scale-session", list(case["messages"])
    )
    result2 = await ChatContextAssembler(store=FakePolicyStore(metadata=case["metadata"])).assemble(
        "scale-session", list(case["messages"])
    )

    # 1) 确定性：同输入 → 逐字节同决策与同消息
    assert result1.messages == result2.messages
    assert result1.budget_report == result2.budget_report
    assert result1.budget_report is not None
    report = result1.budget_report
    # 2) 策略在窗（window 已配置）下真实执行
    assert report["context_policy"]["enabled"] is True
    assert report["context_policy"].get("window_known") is not False
    # 3) 历史预算刚性：历史部分 ≤ 6000 软预算 + 单轮超冲（轮粒度 DROP_OLDEST）
    est_all = sum(_estimate_tokens(str(m.get("content", ""))) for m in result1.messages)
    history_tokens = report["by_category"].get("HISTORY", 0)
    assert history_tokens <= 6_000 + 1_500, (
        f"history={history_tokens} 必须贴近 6000 软预算（+单轮超冲容差）"
    )
    # 4) 总量 ≤ 配置窗口 ×1.1（估算器已偏高 ~30%，再留 10% 组装余量）
    assert est_all <= size * 1.10, f"total={est_all} vs window={size}"
    # 5) KEEP pin 在 256K 压力下存活
    joined = "\n".join(str(m.get("content")) for m in result1.messages)
    assert "CARTOGRAPHY_VERDICT" in joined
    # 6) DROP_OLDEST 确实发生（大历史不可能全量保留）
    assert report["context_policy"]["history_ops"]["dropped_turns"] > 0
    # 7) SUMMARIZE 摘要进 prompt
    assert "用户:" in joined


async def test_assemble_policy_off_vs_on_artifacts(monkeypatch, kill_switch_on):
    """策略开/关的差异面 = 策略产物（摘要 notice / tombstone / context_policy 键）。"""
    from app.core.config import settings
    from app.services.chat.context_assembler import ChatContextAssembler

    monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 32_768, raising=False)
    case = _synthetic_case(32_768, n_turns=60, payload_chars=300)
    msgs = list(case["messages"])
    msgs[-1]["content"] += " ref:data-gone12345678"

    on_result = await ChatContextAssembler(store=FakePolicyStore(metadata=case["metadata"])).assemble(
        "cmp-session", list(case["messages"])
    )
    monkeypatch.setenv("GIS_CONTEXT_POLICY", "0")
    off_result = await ChatContextAssembler(store=FakePolicyStore(metadata=case["metadata"])).assemble(
        "cmp-session", list(case["messages"])
    )
    on_report = on_result.budget_report or {}
    off_report = off_result.budget_report or {}
    assert "context_policy" in on_report
    assert "context_policy" not in off_report
    on_joined = "\n".join(str(m.get("content")) for m in on_result.messages)
    off_joined = "\n".join(str(m.get("content")) for m in off_result.messages)
    assert "用户:" in on_joined and "用户:" not in off_joined  # 摘要是策略产物
    assert "context_policy" not in off_report


async def test_assemble_advisor_flags_map_state_condense_executed(monkeypatch, kill_switch_on):
    """advisor 判 MAP_STATE 超分区 → 策略真实压缩 env 块（[环境感知] 尾部）。"""
    from app.core.config import settings
    from app.services.chat.context_assembler import ChatContextAssembler

    # 8k 窗口 → usable 4096 → MAP_STATE cap = 6% ≈ 245 tok；base_layer 字段
    # 逐字进 env 块（唯一无界渲染点）→ 合成超分区前提确定性成立。
    monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW", 8_192, raising=False)
    metadata = {
        "map_state": {
            "viewport": {"center": [104.0, 30.0], "zoom": 10},
            "base_layer": "底图名称 " + "长" * 3000,
            "layers": {},
        },
        "list_refs": {},
        "event_log": [],
        "started_at": None,
    }
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "看地图"},
    ]
    result = await ChatContextAssembler(store=FakePolicyStore(metadata=metadata)).assemble(
        "cond-idx-session", msgs
    )
    report = result.budget_report or {}
    assert report["context_policy"]["executed"] is True
    condense = [d for d in report["context_policy"]["decisions"] if d["op"] == ContextOp.CONDENSE.value]
    assert any(d["target"] == "map_state_env" for d in condense), (
        "MAP_STATE 超分区必须被真实 CONDENSE，而不是只建议"
    )


# ---------------------------------------------------------------------------
# build_policy_items 形状
# ---------------------------------------------------------------------------


def test_build_policy_items_categories():
    head = [
        {"role": "system", "content": "CORE"},
        {"role": "system", "content": "ENV-TAIL"},
        {"role": "system", "content": "PLAN"},
        {"role": "user", "content": "最终问题"},
    ]
    head_meta = [
        {"name": "map_state_env", "category": "MAP_STATE", "base_chars": 4},
        {"name": "session_plan", "category": "SESSION_PLAN"},
        {"name": "unknown_block", "category": None},
    ]
    items = build_policy_items(head, head_meta, "{}")
    by_name = {i.name: i for i in items}
    assert by_name["system_core"].category is Category.SYSTEM_INSTRUCTIONS
    assert by_name["map_state_env"].category is Category.MAP_STATE
    assert by_name["session_plan"].category is Category.SESSION_PLAN
    assert by_name["unknown_block"].category is Category.SYSTEM_INSTRUCTIONS
    assert by_name["user_final"].category is Category.USER_PROMPT
    assert by_name["history"].category is Category.HISTORY
    assert by_name["tool_schemas"].category is Category.TOOL_SCHEMAS
    # HISTORY 汇总了非 system、非 final user 的消息
    assert by_name["history"].est_tokens == 0
