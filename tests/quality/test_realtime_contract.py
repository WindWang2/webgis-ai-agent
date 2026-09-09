"""Realtime API 契约红线（Quality V2 W6）——WS/SSE 快照 + 兼容分类。

四条红线：
1. 快照字节一致：tests/quality/snapshots/realtime-contract.json 必须与
   当前派生结果一致（过期即红，刷新 = REALTIME_SNAPSHOT_UPDATE=1）；
2. 确定性：同一提交两次快照完全一致（无时间戳/行号/顺序噪声）；
3. 词表锚定：入站感知事件 ⊇ PERCEPTION_HANDLERS 全集；SSE 词表包含
   核心事件（token/tool_call/done/error…）；出站信封词表非空保护由
   行为测试承担（本闸只锁词表收缩）；
4. 分类器自测：移除事件/必需字段扩张/语义锚丢失 = breaking；
   新增事件 = additive；自 diff 恒空。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from app.lib.quality.api_compat import (  # noqa: E402
    diff_realtime_contract,
    snapshot_realtime_contract,
)

SNAPSHOT = REPO / "tests/quality/snapshots/realtime-contract.json"
UPDATE_ENV = "REALTIME_SNAPSHOT_UPDATE"


@pytest.fixture(scope="module")
def snapshot():
    return snapshot_realtime_contract()


def test_snapshot_is_current(snapshot):
    current = json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                         indent=1) + "\n"
    assert SNAPSHOT.exists(), "快照缺失"
    committed = SNAPSHOT.read_text(encoding="utf-8")
    if committed != current and __import__("os").environ.get(UPDATE_ENV) == "1":
        SNAPSHOT.write_text(current, encoding="utf-8")
        pytest.skip("快照已刷新（REALTIME_SNAPSHOT_UPDATE=1），重跑即为绿")
    assert committed == current, (
        "realtime-contract.json 过期：运行 "
        "REALTIME_SNAPSHOT_UPDATE=1 pytest tests/quality/"
        "test_realtime_contract.py 刷新（WS/SSE 契约变化必须伴随 PR 说明）"
    )


def test_snapshot_deterministic(snapshot):
    again = snapshot_realtime_contract()
    assert again == snapshot


def test_ws_inbound_covers_all_perception_handlers(snapshot):
    from app.services.ws_service import PERCEPTION_HANDLERS

    inbound = snapshot["websocket"]["inbound_events"]
    assert set(inbound) == set(PERCEPTION_HANDLERS), "入站词表漂移"


def test_ws_required_fields_match_handler_guards(snapshot):
    inbound = snapshot["websocket"]["inbound_events"]
    assert inbound["layer_opacity_changed"]["required_fields"] == \
        ["layer_id", "opacity"]
    assert inbound["layer_toggled"]["required_fields"] == ["layer_id"]
    assert inbound["viewport_change"]["required_fields"] == []
    assert "state_snapshot" in inbound


def test_sse_vocabulary_contains_core_events(snapshot):
    events = set(snapshot["sse"]["events"])
    core = {"token", "tool_call", "tool_result", "done", "error",
            "task_start", "task_complete", "task_error", "plan_ready",
            "keep_alive"}
    missing = core - events
    assert not missing, f"SSE 核心事件缺失: {missing}"


def test_ws_auth_anchors_all_present(snapshot):
    auth = snapshot["websocket"]["auth"]
    assert auth["subprotocol_preferred"], "#757 subprotocol 回显语义"
    assert auth["token_version_checked"], "#758 token_version 校验"
    assert auth["session_ownership_fail_closed"]


# ── diff 分类器自测 ──────────────────────────────────────────────────────


def _mutated(base: dict, path: str, value) -> dict:
    import copy

    out = copy.deepcopy(base)
    node = out
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return out


def test_diff_inbound_removal_is_breaking(snapshot):
    mutated = _mutated(snapshot,
                       "websocket.inbound_events.layer_toggled", None)
    del mutated["websocket"]["inbound_events"]["layer_toggled"]
    changes = diff_realtime_contract(snapshot, mutated)
    assert any(c.kind == "ws_inbound_event_removed" and c.breaking
               for c in changes)


def test_diff_required_field_growth_is_breaking(snapshot):
    mutated = _mutated(
        snapshot,
        "websocket.inbound_events.viewport_change.required_fields",
        ["center", "zoom"])
    changes = diff_realtime_contract(snapshot, mutated)
    assert any(c.kind == "ws_required_field_added" and c.breaking
               for c in changes)


def test_diff_sse_removal_is_breaking_and_addition_additive(snapshot):
    shrunk = _mutated(snapshot, "sse.events",
                      [e for e in snapshot["sse"]["events"] if e != "token"])
    assert any(c.kind == "sse_event_removed" and c.breaking
               for c in diff_realtime_contract(snapshot, shrunk))
    grown = _mutated(snapshot, "sse.events",
                     [*snapshot["sse"]["events"], "brand_new_event"])
    added = [c for c in diff_realtime_contract(snapshot, grown)]
    assert any(c.kind == "sse_event_added" and not c.breaking for c in added)


def test_diff_auth_anchor_loss_is_breaking(snapshot):
    mutated = _mutated(snapshot, "websocket.auth.token_version_checked",
                       False)
    changes = diff_realtime_contract(snapshot, mutated)
    assert any(c.kind == "ws_auth_anchor_dropped" and c.breaking
               for c in changes)


def test_diff_self_is_clean(snapshot):
    assert diff_realtime_contract(snapshot, snapshot) == []
