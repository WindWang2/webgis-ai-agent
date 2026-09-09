"""V6 Wave 13：Contextual Context Assembly 三层块单测。

覆盖：三层块内容（有/无 active node）、hard cap 截断与字节度量、确定性
（同输入同输出逐位相等）、预算体系接入（context_policy 登记类别）。
"""
import pytest

from app.services.chat import v6_context_blocks as v6
from app.services.chat.context_assembler import ChatContextAssembler
from app.services.session_plan import (
    SessionPlan,
    format_session_plan_projection,
)


def _chapter():
    """带 failed/ready/blocked/stale 各一的最小章节。"""
    return {
        "recipe_id": "test_recipe",
        "query": "测试",
        "data_requirements": [
            {"capability": "admin_boundary", "status": "complete",
             "depends_on": [], "bound_ref": "ref:bound-1",
             "resolved_tool": "webgis_admin_boundary",
             "resolved_algorithm": "admin_lookup"},
            {"capability": "heatmap", "status": "pending",
             "depends_on": ["admin_boundary"]},
            {"capability": "viz", "status": "failed", "depends_on": []},
            {"capability": "export", "status": "pending",
             "depends_on": ["viz"]},
        ],
        "analysis_steps": [],
        "workflow_contract": {
            "data_blockers": ["crs_not_ready"],
            "method_blockers": [],
            "obligations": [],
        },
        "map_product": {
            "status": "pending",
            "render_status": "issues",
            "projection": "[MapProduct] status=pending",
        },
        "workflow_runtime_v6": {
            "nodes": [
                {"node_id": "cap:heatmap", "kind": "analysis",
                 "capability": "heatmap", "role": "", "state": "stale",
                 "bound_ref": "", "inputs": ["ref:bound-1"],
                 "evidence": "fp-old", "algorithm": "heatmap_kde",
                 "params_hash": "ph1", "stale_reason": "evidence_drift",
                 "failure_class": "", "repair_state": ""},
            ],
        },
    }


def _plan():
    return SessionPlan(
        envelope_id="sp-test", session_id="v6-test",
        gis_chapter=_chapter(),
    )


def _review(fingerprint="fp-1"):
    return {"cartography": {
        "status": "failed_checks",
        "termination_reason": "checks_failed",
        "mapspec_fingerprint": fingerprint,
    }}


# ── Active node 选择 ─────────────────────────────────────────────────────

def test_select_active_node_priority_repairing_failed_running():
    running = {"node_id": "cap:a", "capability": "a", "status": "running"}
    failed = {"node_id": "cap:b", "capability": "b", "status": "failed"}
    repairing = {"node_id": "cap:c", "capability": "c", "state": "active",
                 "repair_state": "retrying"}
    assert v6.select_active_node([running, failed], [])["capability"] == "b"
    picked = v6.select_active_node(
        [running, failed],
        [repairing, {"node_id": "cap:a", "capability": "a", "state": "active"}],
    )
    assert picked["capability"] == "c"
    assert v6.select_active_node([running], [])["capability"] == "a"


def test_select_active_node_absent_without_running_failed_repairing():
    nodes = [
        {"node_id": "cap:a", "capability": "a", "status": "complete"},
        {"node_id": "cap:b", "capability": "b", "state": "satisfied"},
    ]
    assert v6.select_active_node(nodes, []) is None
    assert v6.select_active_node([], []) is None


def test_select_active_node_tie_break_is_input_order_independent():
    first = {"node_id": "cap:b", "capability": "b", "status": "failed"}
    second = {"node_id": "cap:a", "capability": "a", "status": "failed"}
    assert v6.select_active_node([first, second], [])["capability"] == "a"
    assert v6.select_active_node([second, first], [])["capability"] == "a"


# ── Node-local 块 ────────────────────────────────────────────────────────

def test_node_local_block_absent_without_active_node():
    assert v6.build_node_local_block(None) is None
    assert v6.active_node_view({}) is None
    assert v6.active_node_view(None) is None


def test_node_local_block_covers_ports_params_obligations_failure_refs():
    view = {
        "node_id": "cap:heatmap", "capability": "heatmap",
        "state": "failed", "tool": "webgis_heatmap",
        "algorithm": "heatmap_kde",
        "ports_in": ["input:geojson"], "ports_out": ["output:raster"],
        "params": {"radius": "500"},
        "obligations": ["crs_disclosure"],
        "failure": "tool_failure",
        "bound_ref": "ref:bound-1", "input_refs": ["ref:bound-0"],
    }
    block = v6.build_node_local_block(view)
    assert block is not None
    assert block.name == "node_local"
    assert not block.truncated
    assert block.byte_len == len(block.text.encode("utf-8"))
    assert block.byte_len <= v6.NODE_LOCAL_BLOCK_MAX_BYTES
    for marker in ("[Node-local Context]", "ports:", "params:",
                   "obligations:", "failure:", "artifact_refs:"):
        assert marker in block.text
    assert "heatmap_kde" in block.text
    assert "ref:bound-1" in block.text


def test_node_local_block_from_chapter_with_failed_node():
    view = v6.active_node_view(_chapter())
    assert view is not None
    assert view["capability"] == "viz"
    block = v6.build_node_local_block(view)
    assert block is not None
    assert "capability=viz" in block.text
    assert "state=failed" in block.text


def test_node_local_block_absent_when_all_nodes_settled():
    chapter = _chapter()
    for row in chapter["data_requirements"]:
        row["status"] = "complete"
    chapter["workflow_runtime_v6"]["nodes"] = []
    assert v6.active_node_view(chapter) is None


# ── Workflow-global 块 ───────────────────────────────────────────────────

def test_workflow_global_block_absent_without_plan():
    assert v6.build_workflow_global_block(None) is None


def test_workflow_global_block_reuses_projection_surface():
    block = v6.build_workflow_global_block(_plan())
    assert block is not None
    assert block.name == "workflow_global"
    assert not block.truncated
    assert "[Workflow-global Context]" in block.text
    assert "[SessionPlan]" in block.text
    assert "[GIS Plan Progress]" in block.text
    assert "stale=1" in block.text
    assert "blocked=" in block.text


def test_session_plan_projection_progress_line_extension():
    text = format_session_plan_projection(_plan())
    assert text.startswith("[SessionPlan]")
    assert "[GIS Plan Progress]" in text
    assert "total=" in text and "stale=" in text and "blocked=" in text
    # 无 DAG 章节零漂移：无进度行。
    empty = SessionPlan(envelope_id="sp-e", session_id="s")
    assert "[GIS Plan Progress]" not in format_session_plan_projection(empty)
    assert format_session_plan_projection(None).startswith("[SessionPlan]")


# ── Map Situation 块 ─────────────────────────────────────────────────────

def test_map_situation_block_content():
    block = v6.build_map_situation_block(
        map_state={"_cartographic_mutation_revision": 7,
                   "_cartographic_review": _review()},
        review=_review(),
        current_fingerprint="fp-1",
        chapter=_chapter(),
        render_diagnostics=[{"code": "table_empty", "severity": "warning",
                             "component_id": "c1"}],
    )
    assert block.name == "map_situation"
    assert not block.truncated
    assert "mapspec_rev=7" in block.text
    assert "verdict=fail" in block.text
    assert "render=issues" in block.text
    # 运行态 stale 节点 + 渲染诊断 = 2 条未决 finding，其中 stale 阻断完成。
    assert "findings: pending=2 blocking=1" in block.text
    assert "table_empty:c1" in block.text


def test_map_situation_block_honest_absence():
    block = v6.build_map_situation_block(
        map_state={}, review=_review(), current_fingerprint="other",
        chapter={},
    )
    assert "mapspec_rev=none" in block.text
    assert "verdict=none" in block.text
    assert "render=unknown" in block.text
    assert "findings: pending=0 blocking=0" in block.text
    assert "components: none" in block.text


# ── Hard cap + 字节度量 ──────────────────────────────────────────────────

def test_cap_text_truncates_with_trace_and_byte_bound():
    big = "图" * 5000
    text, truncated = v6._cap_text(big, 100)
    assert truncated is True
    assert len(text.encode("utf-8")) <= 100
    assert "…(truncated:" in text
    text2, truncated2 = v6._cap_text("ok", 100)
    assert truncated2 is False and text2 == "ok"


def test_block_hard_cap_truncation_leaves_trace_and_metric(monkeypatch):
    monkeypatch.setattr(v6, "MAP_SITUATION_BLOCK_MAX_BYTES", 80)
    block = v6.build_map_situation_block(map_state={}, chapter=_chapter())
    assert block.truncated is True
    assert block.byte_len <= 80
    assert "…(truncated:" in block.text
    metric = v6.blocks_metric([block])
    assert metric["map_situation_byte_cost"] == block.byte_len
    assert metric["map_situation_byte_cap"] == 80
    assert metric["map_situation_truncated"] is True
    assert metric["truncated"] == ["map_situation"]
    assert metric["total_byte_cost"] == block.byte_len


# ── 确定性 ───────────────────────────────────────────────────────────────

def test_blocks_are_deterministic():
    chapter = _chapter()
    state = {"_cartographic_mutation_revision": 3,
             "_cartographic_review": _review()}
    first = (
        v6.build_node_local_block(v6.active_node_view(chapter)).text,
        v6.build_workflow_global_block(_plan()).text,
        v6.build_map_situation_block(
            map_state=state, review=state["_cartographic_review"],
            current_fingerprint="fp-1", chapter=chapter).text,
    )
    chapter2 = _chapter()
    state2 = {"_cartographic_mutation_revision": 3,
              "_cartographic_review": _review()}
    second = (
        v6.build_node_local_block(v6.active_node_view(chapter2)).text,
        v6.build_workflow_global_block(_plan()).text,
        v6.build_map_situation_block(
            map_state=state2, review=state2["_cartographic_review"],
            current_fingerprint="fp-1", chapter=chapter2).text,
    )
    assert first == second


# ── 预算体系接入 ─────────────────────────────────────────────────────────

def test_v6_blocks_register_condensable_policy_categories():
    from app.services.chat.context_budget import Category
    from app.services.chat.context_policy import build_policy_items

    head = [
        {"role": "system", "content": "core"},
        {"role": "system", "content": "[Node-local Context]\nnode=x"},
        {"role": "system", "content": "[Workflow-global Context]\nplan"},
        {"role": "system", "content": "[Map Situation]\nmap"},
        {"role": "user", "content": "hi"},
    ]
    meta = [
        {"name": "map_state_env", "category": "MAP_STATE", "base_chars": 4},
        {"name": "v6_node_local", "category": "ALGORITHM_METADATA"},
        {"name": "v6_workflow_global", "category": "SESSION_PLAN"},
        {"name": "v6_map_situation", "category": "MAP_STATE"},
    ]
    items = build_policy_items(head, meta)
    by_name = {i.name: i.category for i in items}
    assert by_name["v6_node_local"] == Category.ALGORITHM_METADATA
    assert by_name["v6_workflow_global"] == Category.SESSION_PLAN
    assert by_name["v6_map_situation"] == Category.MAP_STATE


class _MetadataStore:
    """固定 metadata 桩（复用既有 assembler 测试模式）。"""

    def __init__(self, metadata: dict):
        self._metadata = metadata

    async def get_session_metadata(self, session_id):
        return self._metadata


@pytest.mark.asyncio
async def test_assemble_injects_v6_blocks_and_metrics():
    store = _MetadataStore({
        "map_state": {"_cartographic_mutation_revision": 5},
        "list_refs": {}, "event_log": [], "started_at": None,
    })
    assembler = ChatContextAssembler(store=store)
    messages = [
        {"role": "system", "content": "You are a WebGIS AI agent."},
        {"role": "user", "content": "Hello!"},
    ]
    result = await assembler.assemble("v6-no-plan", messages)
    contents = [m.get("content", "") for m in result.messages]
    assert any("[Map Situation]" in c for c in contents)
    assert "mapspec_rev=5" in "\n".join(contents)
    # 无计划 → workflow-global 与 node-local 缺席（不编造空块）。
    assert not any("[Node-local Context]" in c for c in contents)
    assert not any("[Workflow-global Context]" in c for c in contents)
    assert result.v6_blocks is not None
    assert result.v6_blocks["map_situation_byte_cost"] > 0
    assert result.budget_report is not None
    assert ("v6_context_blocks" in result.budget_report
            or result.budget_report is None)


@pytest.mark.asyncio
async def test_assemble_with_plan_injects_all_three_blocks(monkeypatch):
    async def _fake_load(session_id, **kwargs):
        return _plan()

    monkeypatch.setattr(
        "app.services.session_plan.load_session_plan", _fake_load)
    store = _MetadataStore({
        "map_state": {"_cartographic_mutation_revision": 5},
        "list_refs": {}, "event_log": [], "started_at": None,
    })
    assembler = ChatContextAssembler(store=store)
    messages = [
        {"role": "system", "content": "You are a WebGIS AI agent."},
        {"role": "user", "content": "Hello!"},
    ]
    result = await assembler.assemble("v6-with-plan", messages)
    contents = [m.get("content", "") for m in result.messages]
    assert any("[Node-local Context]" in c for c in contents)
    assert any("[Workflow-global Context]" in c for c in contents)
    assert any("[Map Situation]" in c for c in contents)
    metric = result.v6_blocks
    assert metric["node_local_byte_cost"] > 0
    assert metric["workflow_global_byte_cost"] > 0
    assert metric["map_situation_byte_cost"] > 0
    assert metric["truncated"] == []
    assert result.budget_report["v6_context_blocks"] == metric


@pytest.mark.asyncio
async def test_assemble_v6_deterministic_across_turns():
    store = _MetadataStore({
        "map_state": {"_cartographic_mutation_revision": 5},
        "list_refs": {}, "event_log": [], "started_at": None,
    })
    assembler = ChatContextAssembler(store=store)
    messages = [
        {"role": "system", "content": "You are a WebGIS AI agent."},
        {"role": "user", "content": "Hello!"},
    ]
    first = await assembler.assemble("v6-determinism", messages)
    second = await assembler.assemble("v6-determinism", messages)
    def _pick(res):
        return [m.get("content", "") for m in res.messages
                if m.get("role") == "system" and (
                    "[Map Situation]" in m.get("content", "")
                    or "[Node-local Context]" in m.get("content", "")
                    or "[Workflow-global Context]" in m.get("content", ""))]
    assert _pick(first) == _pick(second)
