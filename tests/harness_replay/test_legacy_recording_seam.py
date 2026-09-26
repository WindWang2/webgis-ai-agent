"""legacy 录制面统一契约（ADR-0214 D5，WP4）。

钉死的行为：
- legacy ChatExecutionEngine 的两个 settle 点（chat 非流式 / chat_stream）
  都接入与 Pi bridge 同一录制缝（maybe_record_turn）—— 不再有「两路径
  只有一条录制」的盲区；
- 插桩在 emit_turn_summary 之后、TURN_EVIDENCE.remove 之前（与 bridge
  的 settle 顺序一致）；
- 录制调用被 try/except 包裹（记录面绝不阻断 settle）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.cartography

_ENGINE = Path(__file__).resolve().parents[2] / \
    "app/services/chat/execution_engine.py"


def _settle_blocks(source: str) -> list:
    """定位全部 `rt_ev.mark_ended()` 语句所在的语句序列（settle 块）。"""
    tree = ast.parse(source)
    lines = source.splitlines()
    blocks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) \
                and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "mark_ended" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "rt_ev":
            # 向后取 40 行窗口作为 settle 序列近似（settle 块彼此远离）。
            start = node.lineno - 1
            blocks.append("\n".join(lines[start:start + 40]))
    return blocks


def test_both_legacy_settle_points_record():
    source = _ENGINE.read_text(encoding="utf-8")
    blocks = _settle_blocks(source)
    assert len(blocks) == 2, "legacy 恰有两个 settle 点（chat / chat_stream）"
    for i, block in enumerate(blocks):
        assert "maybe_record_turn" in block, (
            f"settle 点 {i} 未接入录制缝（与 Pi bridge 的统一面缺失）")
        # 顺序契约：录制发生在 summary 汇聚之后（settle 语义与 bridge 一致）。
        assert block.index("emit_turn_summary(rt_ev)") \
            < block.index("maybe_record_turn(")


def test_recording_call_is_exception_shielded():
    """记录面调用必须裹在 try/except 里（never-raises 纪律的静态面）。"""
    source = _ENGINE.read_text(encoding="utf-8")
    lines = source.splitlines()
    call_sites = [
        i for i, line in enumerate(lines)
        if "maybe_record_turn(" in line and "import" not in line
    ]
    assert len(call_sites) >= 2
    for idx in call_sites:
        # 向上最近 20 行内必有 try:，向下最近 15 行内必有 except Exception。
        upward = "\n".join(lines[max(0, idx - 20):idx])
        downward = "\n".join(lines[idx:idx + 15])
        assert "try:" in upward, f"line {idx + 1}: 记录调用未裹 try"
        assert "except Exception" in downward, \
            f"line {idx + 1}: 记录调用缺 except 兜底"


def test_bridge_and_legacy_share_the_same_recorder_entrypoint():
    """两条路径 import 的必须是同一个 maybe_record_turn（单一录制缝）。"""
    bridge = Path(__file__).resolve().parents[2] / "app/agent_pi_bridge.py"
    b_src = bridge.read_text(encoding="utf-8")
    e_src = _ENGINE.read_text(encoding="utf-8")
    assert "from app.lib.harness.recorder import" not in b_src
    assert "from app.lib.harness.replay.recorder import" in b_src
    assert "from app.lib.harness.replay.recorder import" in e_src
    assert b_src.count("maybe_record_turn(") >= 2
    assert e_src.count("maybe_record_turn(") >= 2
