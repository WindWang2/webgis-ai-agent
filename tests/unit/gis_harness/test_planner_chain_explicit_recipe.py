"""R3-P1（qc-loop round 3）回归锁：显式 recipe_id 路径必须照常发射证据链。

历史缺陷：``candidates`` 只在 recipe 兜底重选分支绑定，显式 recipe_id
直接命中时 ``CANDIDATE_WORKFLOWS`` 发射行引用未绑定局部变量 →
UnboundLocalError 被记录面的 ``except Exception: pass`` 吞掉 →
规划主干道（workflow_compiler / tools / plan_orchestrator 均传显式
recipe_id）的证据链静默断链，只余 intent 阶段三条。
"""
import contextlib
import uuid

from app.lib.runtime.context import bind_runtime_context
from app.lib.runtime.gis_trace import get_gis_trace_registry
from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.planner import MapProductPlanner


def _bind_turn():
    turn_id = f"qc-r3-{uuid.uuid4().hex[:10]}"
    stack = contextlib.ExitStack()
    stack.enter_context(
        bind_runtime_context(turn_id=turn_id, session_id=f"qc-sess-{turn_id}")
    )
    return turn_id, stack


def test_explicit_recipe_id_still_emits_candidate_and_selected_stages():
    turn_id, stack = _bind_turn()
    try:
        intent = resolve_map_request_intent("成都小学分布密度热力图")
        MapProductPlanner().plan_from_intent(
            intent, recipe_id="poi_distribution_overview", use_memo=False)
        chain = get_gis_trace_registry().get(turn_id)
        assert chain is not None, "evidence chain must exist"
        d = chain.as_dict()
        get_gis_trace_registry().drop(turn_id)
        covered = {s.get("stage") for s in d["stages"]}
        assert "CANDIDATE_WORKFLOWS" in covered, sorted(covered)
        assert "SELECTED_WORKFLOW" in covered, sorted(covered)
        cand = next(
            s for s in d["stages"] if s.get("stage") == "CANDIDATE_WORKFLOWS")
        assert cand.get("selected") == "poi_distribution_overview"
    finally:
        stack.__exit__(None, None, None)
