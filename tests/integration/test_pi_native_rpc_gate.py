"""WP6 / ADR-0301：native real RPC gate（真实 rpc-entry 的有序阶段门）。

任务书 WP6 验收语料：clean start / handshake / tool_search / codemode /
steer / abort / resume（同会话续话）/ settle —— 每阶段 typed 断言、机器
可读 stage 报告（pytest -s 可复核）。这是 PR 的「native real RPC gate」
证据：所有阶段都跑在真实 vendored rpc-entry + 本地 stub LLM 上，任何一阶
段失败即 gate 红。

与既有单点测试的关系：本文件**组合**既有已验证行为成为有序验收门（每个
阶段的细化负面/边界都在 vendor contract 车道与 chaos 车道单点覆盖）；
不重复断言细节，只锁「阶段序列整体可用 + 阶段间状态一致」。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_contract_module():
    path = Path(__file__).parent / "test_pi_vendor_contract.py"
    spec = importlib.util.spec_from_file_location("_pi_vendor_contract_gate_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_C = _load_contract_module()

pytestmark = [
    _C.pytest.mark.heavy,
    _C.pytest.mark.skipif(bool(_C._needs_build()), reason=_C._needs_build() or "vendor built"),
]


def test_native_rpc_gate_ordered_stages(tmp_path):
    """六阶段有序 gate（真实 v1.6.1 rpc-entry）：

    S1 clean start + handshake（get_state success，模型面解析）
    S2 native discovery：tool_search → 发现 → 直调 deferred 工具
    S3 codemode 沙箱嵌套：parentToolCallId 事件 + 嵌套 id 形状
    S4 运行中 steer：steer 响应成功 + 注入消息抵达下一次模型调用
    S5 abort：中途 abort → aborted settle（终态恰好一次）
    S6 resume 等价：同 sessionId 续话 → messageCount 单调增长
    """
    stages: dict[str, dict] = {}

    # ── S1+S6 复用一个 harness（会话状态需要跨 turn 保持）───────────────
    harness = _C._VendorHarness(
        stub_port=0, tmp=tmp_path,
        mode="tool-then-text",
        tool="webgis_map_intent",
        tool_args=json.dumps({"query": "gate handshake"}),
    )
    harness.start_stub()
    try:
        # S1：clean start + handshake —— 探针每 turn 都是全新 rpc-entry 进程
        #（clean start）；首记录必为 get_state 响应（handshake）。
        r1 = harness.run_turn("gate stage1 handshake turn")
        lines = r1["lines"]
        hs = next(
            (r for r in lines
             if r.get("type") == "response" and r.get("command") == "get_state"
             and r.get("id") == "1"), None)
        assert hs is not None and hs.get("success") is True, hs
        hs_data = hs.get("data") or {}
        assert isinstance(hs_data.get("model"), dict), hs_data
        assert isinstance(hs_data.get("messageCount"), int), hs_data
        stages["S1_handshake"] = {"ok": True, "messageCount": hs_data["messageCount"]}

        # S6：会话连续性 —— 同一 worker 进程内第二 turn 的 messageCount 单调
        # 增长（turn journal 历史保持）。跨进程的 switch_session/恢复语义由
        # chaos/G22 车道覆盖（本 harness 的探针每 turn 全新进程 + --no-session，
        # 与生产 worker 的长驻形态一致地隔离了进程外状态）。
        r6 = harness.run_turn("gate stage6 continuity turn", continuity=True)
        hs_a = next(
            (r for r in r6["lines"]
             if r.get("type") == "response" and r.get("command") == "get_state"
             and r.get("id") == "c1"), None)
        hs_b = next(
            (r for r in r6["lines"]
             if r.get("type") == "response" and r.get("command") == "get_state"
             and r.get("id") == "c3"), None)
        assert hs_a is not None and hs_a.get("success") is True, hs_a
        assert hs_b is not None and hs_b.get("success") is True, hs_b
        mc_a = (hs_a.get("data") or {}).get("messageCount")
        mc_b = (hs_b.get("data") or {}).get("messageCount")
        assert isinstance(mc_a, int) and isinstance(mc_b, int) and mc_b > mc_a, (mc_a, mc_b)
        settled6 = [r for r in r6["lines"] if r.get("type") == "agent_settled"]
        assert len(settled6) == 2, settled6
        stages["S6_resume"] = {"ok": True, "messageCount": f"{mc_a}->{mc_b}"}
    finally:
        harness.stop_stub()

    # ── S2：native discovery ────────────────────────────────────────────
    h2 = _C._VendorHarness(
        stub_port=0, tmp=tmp_path, mode="discovery",
        tool="nearest_facility", tool_args="{}",
        search_query="buffer",
    )
    h2.start_stub()
    try:
        r2 = h2.run_turn("gate stage2 discovery turn")
    finally:
        h2.stop_stub()
    ev2 = _C._events(r2)
    search = next(
        (e for e in ev2
         if e.get("type") == "tool_execution_end" and e.get("toolName") == "tool_search"),
        None)
    direct = next(
        (e for e in ev2
         if e.get("type") in ("tool_execution_start", "tool_execution_end")
         and e.get("toolName") == "nearest_facility"),
        None)
    assert search is not None and search.get("isError") is not True, ev2
    assert direct is not None, ev2  # 发现后直调（非代理）
    stages["S2_discovery"] = {"ok": True}

    # ── S3：codemode 沙箱嵌套 ───────────────────────────────────────────
    h3 = _C._VendorHarness(
        stub_port=0, tmp=tmp_path, mode="codemode-nested",
        code="const r = await tools.webgis_map_intent({query: '成都小学分布'});\nreturn 'ok ' + String(r).slice(0, 20);",
    )
    h3.start_stub()
    try:
        r3 = h3.run_turn("gate stage3 codemode turn")
    finally:
        h3.stop_stub()
    ev3 = _C._events(r3)
    cm_start = next(
        (e for e in ev3
         if e.get("type") == "tool_execution_start" and e.get("toolName") == "codemode"),
        None)
    nested = [
        e for e in ev3
        if e.get("parentToolCallId") == (cm_start or {}).get("toolCallId")
    ]
    assert cm_start is not None and nested, ev3
    assert all(
        str(e.get("toolCallId", "")).startswith(cm_start["toolCallId"] + "/")
        for e in nested if e.get("type", "").startswith("tool_execution")
    ), nested
    stages["S3_codemode"] = {"ok": True, "nested": len(nested)}

    # ── S4：运行中 steer ────────────────────────────────────────────────
    h4 = _C._VendorHarness(
        stub_port=0, tmp=tmp_path, mode="tool-then-text",
        tool="webgis_map_intent",
        tool_args=json.dumps({"query": "gate steer"}),
    )
    # 首 LLM 响应拖慢 2s —— steer 在 agent 运行中到达（见 probe 注释）。
    h4.stub_env["STUB_DELAY_FIRST_S"] = "2"
    # 环境注入在 start_stub 前 → 重新写 env 再起 stub。
    h4.stub_env = {**h4.stub_env}
    h4.start_stub()
    try:
        r4 = h4.run_turn("gate stage4 steer base turn", steer=True)
    finally:
        h4.stop_stub()
    assert r4.get("steerSent") is True, r4.get("steerSent")
    lines4 = r4["lines"]
    steer_resp = next(
        (r for r in lines4
         if r.get("type") == "response" and r.get("command") == "steer"), None)
    assert steer_resp is not None and steer_resp.get("success") is True, steer_resp
    # 注入消息抵达下一次模型调用（stub log 的 lastUser 含 STEER-MARKER）。
    log4 = [
        json.loads(line)
        for line in h4.stub_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any("STEER-MARKER" in (e.get("lastUser") or "") for e in log4), log4
    # vendor steer 语义：注入**当前 run**（工具跑完后、下一次 LLM 调用前），
    # 不新起 run —— 恰一次 agent_settled 是正确形态（与 rpc-commands.md
    # `steer` 节一致；steer 抵达模型已由 stub log 证明）。
    settled4 = [r for r in lines4 if r.get("type") == "agent_settled"]
    assert len(settled4) == 1 and settled4[0].get("aborted") is not True, settled4
    stages["S4_steer"] = {"ok": True}

    # ── S5：abort 中途 → aborted settle 恰一次 ──────────────────────────
    h5 = _C._VendorHarness(
        stub_port=0, tmp=tmp_path, mode="tool-then-text",
        tool="webgis_map_intent",
        tool_args=json.dumps({"query": "gate abort"}),
        delay_second_s=4,  # 拖慢第二响应 —— abort 需要落在 run 中段
    )
    h5.start_stub()
    try:
        r5 = h5.run_turn("gate stage5 abort turn", abort_after_tool=True)
    finally:
        h5.stop_stub()
    ev5 = _C._events(r5)
    settled5 = [e for e in ev5 if e.get("type") == "agent_settled"]
    assert len(settled5) == 1 and settled5[0].get("aborted") is True, settled5
    stages["S5_abort"] = {"ok": True}

    # 机器可读 gate 报告（pytest -s 复核）。
    print("GATE_REPORT " + json.dumps(stages, ensure_ascii=False, sort_keys=True))
    assert all(s["ok"] for s in stages.values()), stages
