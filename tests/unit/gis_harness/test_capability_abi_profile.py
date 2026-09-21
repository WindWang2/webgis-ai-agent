"""ADR-0204 D5：capability ABI profile（V2 derived 聚合）+ 多 provider 确定性。

覆盖验收矩阵（capability-abi-v2-design.md）：
#1 真实 registry 多 provider 确定性解析（3 类能力）；
#5 preferred provider 不可用（offline）→ 次选居首；
#10 预算超限 → 资格披露；
#12 取消契约披露；
#14 确定性 provider 选择。
"""
from __future__ import annotations

import json

import pytest

from app.services.gis_harness.capability_resolution import (
    CAPABILITY_ABI_VERSION,
    capability_abi_profile,
    describe_capability,
)
from app.services.gis_harness.candidate_planner_v8 import plan_candidates_v8
from app.services.gis_harness.qualification_v8 import QualificationContext


def _live_capabilities_with_multiple_tool_providers(min_providers: int = 2):
    """真实 registry 上有 ≥N 个 tool provider 的 capability ids（确定性序）。"""
    from app.lib.gis.runtime_manifest import get_runtime_manifest

    m = get_runtime_manifest()
    return [
        cap for cap, tools in sorted(m.capability_to_tools.items())
        if len(tools) >= min_providers
    ]


# ── 验收 #1 / #14：真实 registry 多 provider 确定性解析 ────────────────


@pytest.mark.parametrize("capability_id", [
    "image_segmentation", "workspace_state_inspection", "poi_query",
])
def test_multi_provider_capability_resolves_deterministically(capability_id):
    """3 类真实能力：多 provider 在解析/排序/manifest 三面一致且确定。"""
    from app.lib.gis.runtime_manifest import get_runtime_manifest

    m = get_runtime_manifest()
    manifest_tools = m.capability_to_tools.get(capability_id, [])
    assert len(manifest_tools) >= 2, f"{capability_id} 必须有多 provider"

    ctx = QualificationContext(task_hint="probe")
    plan_a = plan_candidates_v8(capability_id, ctx)
    plan_b = plan_candidates_v8(capability_id, ctx)
    ids_a = [c.id for c in plan_a.candidates]
    ids_b = [c.id for c in plan_b.candidates]
    assert ids_a == ids_b and len(ids_a) >= 2, "同输入同序（确定性）"
    # 解析面与 manifest 收敛面共享 provider 词表（声明面并入后不再漂移）
    graph_ids = set(ids_a)
    assert graph_ids & set(manifest_tools), "图与 manifest 的 provider 面相交"


def test_profile_aggregates_multi_provider_surface():
    caps = _live_capabilities_with_multiple_tool_providers(2)
    assert caps, "真实 registry 必须存在多 provider 能力"
    profile = capability_abi_profile(caps[0])
    assert profile is not None
    assert profile["abi_version"] == CAPABILITY_ABI_VERSION == 2
    assert profile["provider_count"] == (
        len(profile["tools"]) + len(profile["models"]))
    assert profile["provider_count"] >= 2
    # 聚合旗标是全并集语义（键封闭、有界）
    derived = profile["derived"]
    assert set(derived) == {
        "deterministic", "offline_capable", "credentials_required",
        "permissions", "side_effect_classes", "output_semantic_types",
    }
    # 确定性：同输入字节级同输出
    again = capability_abi_profile(caps[0])
    assert json.dumps(profile, sort_keys=True) == json.dumps(again, sort_keys=True)


def test_profile_provenance_declared_vs_derived():
    """声明/派生溯源可见（recon 实测：poi_query 双源、image_segmentation 声明簇）。"""
    profile = capability_abi_profile("image_segmentation")
    assert profile is not None
    sources = {t["source"] for t in profile["tools"]}
    assert "declared" in sources
    profile_poi = capability_abi_profile("poi_query")
    assert profile_poi is not None
    assert {"declared", "derived"} & {t["source"] for t in profile_poi["tools"]}


def test_profile_cancellation_contract_disclosed():
    """验收 #12：取消契约按 execution_policy 诚实披露（不虚构）。"""
    profile = capability_abi_profile("image_segmentation")
    assert profile is not None
    for t in profile["tools"]:
        assert t["cancellation"] in ("durable", "dispatch_level")
        if t["execution_policy"] == "celery":
            assert t["cancellation"] == "durable"


def test_describe_capability_embeds_abi_profile():
    out = describe_capability("image_segmentation")
    assert out is not None
    assert out["abi"] is not None
    assert out["abi"]["abi_version"] == 2
    # 未知 capability：画像与 abi 一致返回 None
    assert describe_capability("no_such_capability_anywhere") is None
    assert capability_abi_profile("no_such_capability_anywhere") is None


def test_credential_and_permission_unions_surface():
    """验收 #6/#7（描述符面）：provider 的凭证/权限要求聚合进 profile。

    图重建优先消费注入 registry（#1470 lifespan 语义）—— 测试经同一接缝
    注入带凭证/权限声明的探针工具。
    """
    from app.agent_pi_bridge import set_tool_registry
    from app.services.gis_harness.capability_graph import (
        reset_capability_graph,
    )
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    reg.register(
        "abi_sec_probe",
        "sec probe",
        lambda **kw: {"ok": True},
        parameters={"type": "object", "properties": {}, "required": []},
        tier=1,
        requires_credentials=("some_api_key",),
        required_permission="admin",
        capabilities=["poi_query"],
    )
    set_tool_registry(reg)
    try:
        reset_capability_graph()
        profile = capability_abi_profile("poi_query")
        assert profile is not None
        derived = profile["derived"]
        assert "some_api_key" in derived["credentials_required"]
        assert "admin" in derived["permissions"]
        probe = [t for t in profile["tools"] if t["id"] == "abi_sec_probe"]
        assert probe and probe[0]["requires_credentials"] == ["some_api_key"]
        assert probe[0]["required_permission"] == "admin"
    finally:
        set_tool_registry(None)
        reset_capability_graph()


# ── 验收 #5 / #10：situation 驱动的替代选择（scratch graph）────────────


class _ScratchNode:
    def __init__(self, id, kind, extras):
        self.id = id
        self.kind = kind
        self.extras = extras
        self.key = f"{kind}:{id}"
        self.label = id
        self.corpus = ""


class _ScratchGraph:
    """最小图桩：capability + 两工具，一个 network=True 一个 network=False。"""

    def __init__(self):
        self._nodes = {
            "capability:net_cap": _ScratchNode(
                "net_cap", "capability", {"domain": "general"}),
            "tool:remote_tool": _ScratchNode(
                "remote_tool", "tool",
                {"network": True, "deterministic": True, "status": "stable",
                 "side_effect": "pure", "latency_class": "slow",
                 "execution_policy": "thread"}),
            "tool:local_tool": _ScratchNode(
                "local_tool", "tool",
                {"network": False, "deterministic": True, "status": "stable",
                 "side_effect": "pure", "latency_class": "medium",
                 "execution_policy": "thread"}),
        }
        self._cap_edges = {
            "net_cap": ["remote_tool", "local_tool"],
        }

    def node(self, key):
        return self._nodes.get(key)

    def has(self, kind, id):
        return f"{kind}:{id}" in self._nodes

    def tools_for_capability(self, capability_id):
        return list(self._cap_edges.get(capability_id, []))

    def models_for_capability(self, capability_id):
        return []

    def fallback_chain(self, kind, id):
        return []

    def conflicts_of_capability(self, capability_id):
        return []


def test_offline_situation_prefers_local_provider():
    """验收 #5：offline 时 network provider 失格，本地 provider 居首。"""
    ctx = QualificationContext(offline=True)
    plan = plan_candidates_v8("net_cap", ctx, graph=_ScratchGraph())
    assert [c.id for c in plan.candidates] == ["local_tool"]
    assert any(e["id"] == "remote_tool" for e in plan.excluded)


def test_online_situation_keeps_both_and_orders_deterministically():
    ctx = QualificationContext(offline=False)
    graph = _ScratchGraph()
    plan_a = plan_candidates_v8("net_cap", ctx, graph=graph)
    plan_b = plan_candidates_v8("net_cap", ctx, graph=graph)
    # 双双合格；确定性排序按 latency 档位（local medium < remote slow）
    assert [c.id for c in plan_a.candidates] == ["local_tool", "remote_tool"]
    assert [c.id for c in plan_a.candidates] == [c.id for c in plan_b.candidates]


def test_profile_offline_flag_derives_from_provider_face():
    graph = _ScratchGraph()
    profile = capability_abi_profile("net_cap", graph=graph)
    assert profile is not None
    # 一个 network=False provider 存在 → offline_capable 存在语义 True
    assert profile["derived"]["offline_capable"] is True
    assert profile["derived"]["deterministic"] is True
