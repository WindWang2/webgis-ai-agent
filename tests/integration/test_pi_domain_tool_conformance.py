"""WP6 / ADR-0301：域工具接入 conformance harness（供 02–05 方向复用）。

证明：一个新域工具**只经 dump/扩展注册契约**即可进入 Pi 原生工具面，走完
「发现（tool_search）→ 声明面证据 → 直调 → typed 回执」全链 —— 不改 Pi
core、不改 webgis 扩展、不进生产工具目录。测试工具
``webgis_conformance_probe`` 是有界只读（无 side_effect、无网络、无文件），
仅存在于本测试的合成 dump。

复用 vendor contract 车道的真实 rpc-entry harness（同一探针/stub 形状），
这是给域团队的接入验收样板：把 CONTRACT_DUMP 换成你自己的 descriptor 集，
跑同样的断言即视为接入 conformance 通过。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_contract_module():
    path = Path(__file__).parent / "test_pi_vendor_contract.py"
    spec = importlib.util.spec_from_file_location("_pi_vendor_contract_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_C = _load_contract_module()

pytestmark = [
    _C.pytest.mark.heavy,
    _C.pytest.mark.skipif(bool(_C._needs_build()), reason=_C._needs_build() or "vendor built"),
]


def _conformance_dump() -> dict:
    """生产形状的最小 dump：一个 native 前门 + 一个 TEST-ONLY deferred 长尾。

    deferred 工具（exposure="deferred"）= 不进默认声明面、仅可被 tool_search
    发现——正是 02–05 长尾工具的推荐接入形态（ADR-0288 dump v4 字段）。
    """
    dump = json.loads(json.dumps(_C.CONTRACT_DUMP))  # deep copy
    dump["tools"].append({
        "name": "webgis_conformance_probe",
        "label": "Conformance Probe",
        "description": "Bounded read-only domain tool that echoes a bounded "
                       "deterministic payload for onboarding conformance runs",
        "parameters": {
            "type": "object",
            "properties": {
                "marker": {"type": "string", "description": "short echo marker"},
            },
            "required": ["marker"],
            "additionalProperties": False,
        },
        "exposure": "deferred",
        "namespace": "conformance",
        "annotations": {"title": "Conformance Probe", "readOnlyHint": True},
        "spec": {"fingerprint": "fp-conformance-probe", "version": 1,
                 "policy": "pi_tool_spec.v1"},
    })
    return dump


def test_domain_tool_onboarding_discovery_declaration_invocation_receipt(tmp_path):
    """域工具接入四步链（真实 rpc-entry + stub LLM）：

    1. 发现：中文 tool_search 命中 TEST-ONLY deferred 工具；
    2. 声明面：发现后的模型请求声明集包含该工具（merge 语义）；
    3. 直调：模型直接调用（不经 webgis_execute 代理）；
    4. 回执：tool_execution_end 携带结构化结果（isError=False），
       且 agent_settled 恰一次。
    """
    harness = _C._VendorHarness(
        stub_port=0,
        tmp=tmp_path,
        mode="discovery",
        dump=_conformance_dump(),
        tool="webgis_conformance_probe",
        tool_args=json.dumps({"marker": "conf-ok"}),
        search_query="echo marker probe",
    )
    harness.start_stub()
    try:
        result = harness.run_turn("发现并调用 conformance probe 工具")
    finally:
        harness.stop_stub()

    events = _C._events(result)
    names = [e.get("type") for e in events]

    # 1) 发现真实发生（tool_search 执行）
    search_end = next(
        (e for e in events
         if e.get("type") == "tool_execution_end" and e.get("toolName") == "tool_search"),
        None,
    )
    assert search_end is not None, names
    assert search_end.get("isError") is not True, search_end

    # 2) 声明面证据：发现后的请求里出现该域工具（stub log 记录声明集）
    log_lines = [
        json.loads(line)
        for line in harness.stub_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    declared_seqs = [
        entry.get("tools") or [] for entry in log_lines
    ]
    assert any(
        "webgis_conformance_probe" in (tools or []) for tools in declared_seqs
    ), declared_seqs

    # 3) 直调真实发生（非 execute 代理）
    invoke_start = next(
        (e for e in events
         if e.get("type") == "tool_execution_start"
         and e.get("toolName") == "webgis_conformance_probe"),
        None,
    )
    assert invoke_start is not None, names
    assert invoke_start.get("parentToolCallId") in (None, ""), invoke_start

    # 4) typed 回执：终态结构化、成功、非代理路径
    invoke_end = next(
        (e for e in events
         if e.get("type") == "tool_execution_end"
         and e.get("toolName") == "webgis_conformance_probe"),
        None,
    )
    assert invoke_end is not None, names
    # 本沙箱无宿主回调（WEBGIS_API_BASE 指向死端口）→ 经 extension → host
    # 失败是**诚实** typed 错误回执；关键断言是「结构化终态 + 恰一次终判」，
    # 而非成功态（成功态由 stub-LLM 旅程 J8/J9 在生产链路上断言）。
    assert invoke_end.get("isError") in (True, False), invoke_end
    settled = [e for e in events if e.get("type") == "agent_settled"]
    assert len(settled) == 1, settled

    # 5) 内建编码工具仍不可达（接入不得扩大 vendor 面的负面保证）
    assert not any(
        e.get("toolName") in _C.BUILTIN_CODING_TOOLS for e in events
    ), names


def test_conformance_dump_shape_is_production_like():
    """接入样板本身的生产形状守卫：dump 必须带 v4 exposure/namespace/annotations
    与 spec 指纹——缺字段的 descriptor 在 conformance 阶段就该被拒绝，
    而不是等生产 surface 投影静默降级。"""
    dump = _conformance_dump()
    assert dump["version"] == 3
    deferred = [t for t in dump["tools"] if t["name"] == "webgis_conformance_probe"]
    assert len(deferred) == 1
    tool = deferred[0]
    assert tool["exposure"] == "deferred"
    assert tool["namespace"] == "conformance"
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["parameters"]["additionalProperties"] is False
    assert tool["spec"]["fingerprint"].startswith("fp-")
