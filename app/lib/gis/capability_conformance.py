"""Capability Binding Conformance —— core registry 声明面一致性校验（ADR-0204 D2）。

capability→tool 绑定有两个声明面：算法 ``tool_candidates``（派生面）与工具
``capabilities=`` 元数据（声明面）。两面对账此前只在扩展包认证（ADR-0201）
里存在，core registry 靠隐式投影——本模块把对账收敛为一个**纯函数校验器**，
由 ``compile_runtime_manifest`` 消费（fatal 入启动 fail-fast 闸），测试与
治理脚本直调。

分级哲学（与 manifest 议题同语义）：

- **fatal** = 引用破损：声明的 capability id 不在词表（永远不可解析的绑定，
  与 ``algorithm_dangling_capability`` / ``recipe_dangling_capability`` 同级）；
- **warning** = 完整度 / 分歧：声明面无算法链支撑（合法态——交互/编排/巡检
  工具不走算法候选链）、关键 metadata 缺席（禁止静默残缺注册）、同能力多
  provider 输出契约互相冲突（披露等价性风险，不阻断）。

产出**排序确定、条数有界**；只读描述符事实，绝不反写 registry。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

SEVERITY_FATAL = "fatal"
SEVERITY_WARNING = "warning"

#: 校验发现预算（巨型 registry 下截断并披露，不静默）。
MAX_FINDINGS = 512
#: 聚合类 warning 在 manifest 侧折叠时的工具清单上界（本模块逐条产出，
#: 折叠归 manifest 编译器）。
MAX_DETAIL_ITEMS = 16


@dataclass(frozen=True)
class ConformanceIssue:
    """一条声明面一致性发现（排序键 = (code, tool, capability)）。"""

    code: str
    severity: str
    tool: str
    capability: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code, "severity": self.severity,
            "tool": self.tool[:128], "capability": self.capability[:128],
            "detail": self.detail[:240],
        }


#: capability_id_dangling：声明的 capability id 不在词表（fatal）。
CODE_ID_DANGLING = "capability_id_dangling"
#: capability_binding_unbacked：声明无算法候选链支撑（warning；合法态披露）。
CODE_BINDING_UNBACKED = "capability_binding_unbacked"
#: descriptor_metadata_incomplete：声明 capability 但关键 ABI 元数据缺席
#: （warning；network/deterministic/side_effect/result_size_policy）。
CODE_METADATA_INCOMPLETE = "descriptor_metadata_incomplete"
#: provider_output_contract_divergence：同 capability 的 provider 输出语义
#: 互相冲突（warning；多 provider 等价性风险披露）。
CODE_OUTPUT_DIVERGENCE = "provider_output_contract_divergence"

_UNCLASSIFIED_SIDE_EFFECT = "unclassified"
_UNKNOWN_RESULT_SIZE = "unknown"


def _derived_capability_tools(
    algorithms: Iterable[Any],
) -> Dict[str, set]:
    """算法候选链 → {capability: {tool}}（派生面，纯读）。"""
    out: Dict[str, set] = {}
    for algo in algorithms:
        caps = getattr(algo, "capabilities", None) or ()
        tools = getattr(algo, "tool_candidates", None) or ()
        for cap in caps:
            out.setdefault(str(cap), set()).update(str(t) for t in tools)
    return out


def _declared_bindings(name: str, meta: Mapping[str, Any]) -> List[str]:
    """工具元数据 → 声明的 capability id 列表（去重保序、有界）。"""
    raw = meta.get("capabilities") or ()
    if isinstance(raw, str):
        raw = (raw,)
    out: List[str] = []
    for c in raw:
        s = str(c or "").strip()
        if s and s not in out:
            out.append(s[:128])
    return out[:8]


def _metadata_gaps(meta: Mapping[str, Any]) -> List[str]:
    """声明面工具的关键 ABI 元数据缺口（有界词表，逐字段诚实）。"""
    gaps: List[str] = []
    if meta.get("network") is None:
        gaps.append("network")
    if meta.get("deterministic") is None:
        gaps.append("deterministic")
    side_effect = str(meta.get("side_effect") or "").lower()
    if not side_effect or side_effect == _UNCLASSIFIED_SIDE_EFFECT:
        gaps.append("side_effect")
    if str(meta.get("result_size_policy") or _UNKNOWN_RESULT_SIZE) == _UNKNOWN_RESULT_SIZE:
        gaps.append("result_size_policy")
    return gaps


def validate_capability_conformance(
    *,
    capability_ids: Iterable[str],
    algorithms: Iterable[Any],
    tool_metadata: Iterable[Tuple[str, Mapping[str, Any]]],
) -> List[ConformanceIssue]:
    """声明面一致性校验（纯函数；同输入同序同输出）。

    ``capability_ids``：CapabilityRegistry 词表（权威）；
    ``algorithms``：AlgorithmRegistry 描述符迭代（capabilities/tool_candidates）；
    ``tool_metadata``：``(tool_name, metadata_dict)`` 迭代。
    """
    caps = {str(c) for c in capability_ids}
    derived = _derived_capability_tools(algorithms)
    issues: List[ConformanceIssue] = []

    def _emit(issue: ConformanceIssue) -> None:
        if len(issues) < MAX_FINDINGS:
            issues.append(issue)

    # capability → {output_semantic_type: [tool]}（输出契约分歧面）
    output_face: Dict[str, Dict[str, List[str]]] = {}

    for name, meta in tool_metadata:
        declared = _declared_bindings(name, meta)
        if not declared:
            continue
        gaps = _metadata_gaps(meta)
        if gaps:
            _emit(ConformanceIssue(
                CODE_METADATA_INCOMPLETE, SEVERITY_WARNING,
                str(name)[:128], ",".join(declared[:4]),
                "missing: " + ",".join(gaps)))
        out_type = str(meta.get("output_semantic_type") or "")
        for cap in declared:
            if cap not in caps:
                _emit(ConformanceIssue(
                    CODE_ID_DANGLING, SEVERITY_FATAL,
                    str(name)[:128], cap,
                    "declared capability id not in CapabilityRegistry"))
                continue
            if name not in derived.get(cap, set()):
                _emit(ConformanceIssue(
                    CODE_BINDING_UNBACKED, SEVERITY_WARNING,
                    str(name)[:128], cap,
                    "declared binding has no algorithm tool_candidates support"))
            if out_type:
                output_face.setdefault(cap, {}).setdefault(
                    out_type, []).append(str(name)[:128])

    for cap in sorted(output_face.keys()):
        faces = output_face[cap]
        if len(faces) < 2:
            continue
        variants = sorted(faces.keys())
        tools = sorted(t for v in variants for t in faces[v][:4])[:MAX_DETAIL_ITEMS]
        _emit(ConformanceIssue(
            CODE_OUTPUT_DIVERGENCE, SEVERITY_WARNING,
            "", cap,
            f"providers declare distinct output_semantic_type "
            f"{variants[:4]}: {tools}"))

    issues.sort(key=lambda i: (i.code, i.tool, i.capability))
    return issues


def aggregate_tool_issues(
    issues: Sequence[ConformanceIssue],
    *,
    codes: Sequence[str],
    code: str,
    detail_header: str,
) -> List[ConformanceIssue]:
    """逐工具 warning 折叠为单条聚合（manifest 编译期防启动噪音）。"""
    tools: List[str] = []
    for i in issues:
        if i.code in codes and i.tool and i.tool not in tools:
            tools.append(i.tool)
    if not tools:
        return []
    shown = ", ".join(tools[:MAX_DETAIL_ITEMS])
    suffix = f" (+{len(tools) - MAX_DETAIL_ITEMS} more)" if len(tools) > MAX_DETAIL_ITEMS else ""
    return [ConformanceIssue(
        code, SEVERITY_WARNING, "", "",
        f"{detail_header}: {len(tools)} tools [{shown}]{suffix}")]


__all__ = [
    "ConformanceIssue",
    "CODE_ID_DANGLING",
    "CODE_BINDING_UNBACKED",
    "CODE_METADATA_INCOMPLETE",
    "CODE_OUTPUT_DIVERGENCE",
    "SEVERITY_FATAL",
    "SEVERITY_WARNING",
    "MAX_FINDINGS",
    "validate_capability_conformance",
    "aggregate_tool_issues",
]
