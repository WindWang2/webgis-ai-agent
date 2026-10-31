"""选择性局部重算（goal-02 M7）—— 步骤内容指纹 + 失效传播。

指纹语义（幂等键）：

```
step_fp = sha256(canonical_json({
    schema, tool, params, units, science_context,
    input_identities,   # 每 param 的内容身份：ref@content_revision
                         # 或上游步骤指纹（内容链——同内容换 ref 不重算）
}))
```

- **内容身份**复用 session store 的 content_revision（V5-E 活体戳）：
  ref 字符串变化或原地 overwrite 都会改变身份；同内容新 ref 不变；
- **失效传播**只沿 depends_on / step: 输入边下游闭包；style 维度
  **永不**进入科学失效（仅呈现态——与 workflow_instance 事件维度一致）；
- 恢复语义：satisfied 且指纹未变的步骤在 resume/re-run 时直接复用
  （envelope 不重铸、ref 不重复 store——不重复写产物）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Set

from app.services.gis_harness.analysis_workflows.contracts import (
    AnalysisStepSpec,
    AnalysisWorkflowContract,
    ScienceContext,
    canonical_json,
)
from app.services.gis_harness.analysis_workflows.state import WorkflowRunState

import hashlib

#: 指纹 schema（算法演进时 additive 升版——旧指纹自然全部失效触发重算）。
FINGERPRINT_SCHEMA = 1


def _hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()


def ref_identity(ref: str, content_revision: Any = None) -> str:
    """ref 的内容身份（ref@revision；无 revision 时退化为 ref 本身——
    上游没给 revision 说明 store 侧不支持活体戳，保守把地址当内容）。"""
    rev = content_revision if content_revision is not None else ""
    return f"{ref}@{rev}"


def step_fingerprint(
    step: AnalysisStepSpec,
    *,
    input_identities: Dict[str, str],
    science: ScienceContext,
) -> str:
    """步骤内容指纹（确定性；单位/科学上下文进指纹——换单位=新计算）。"""
    return _hash({
        "schema": FINGERPRINT_SCHEMA,
        "tool": step.tool_name,
        "params": step.step_params,
        "units": step.unit_contract,
        "science": science.model_dump(),
        "inputs": {k: input_identities[k]
                   for k in sorted(input_identities)},
    })


def downstream_closure(
    contract: AnalysisWorkflowContract, seeds: Set[str],
) -> Set[str]:
    """种子步骤的直接/间接下游（依赖闭包；不含种子自身）。"""
    consumers: Dict[str, Set[str]] = {}
    for step in contract.steps:
        sources = set(step.depends_on)
        sources.update(
            v.removeprefix("step:")
            for v in step.role_inputs.values() if v.startswith("step:"))
        for src in sources:
            consumers.setdefault(src, set()).add(step.step_id)
    closure: Set[str] = set()
    frontier = set(seeds)
    while frontier:
        nxt: Set[str] = set()
        for sid in frontier:
            for consumer in consumers.get(sid, ()):  # BFS
                if consumer not in closure:
                    closure.add(consumer)
                    nxt.add(consumer)
        frontier = nxt
    return closure


def plan_invalidate_on_role_change(
    contract: AnalysisWorkflowContract,
    state: WorkflowRunState,
    role: str,
    new_identity: str,
) -> Set[str]:
    """某数据角色内容变更 → 待重算步骤集（直接消费者 + 下游闭包）。

    只标记 stale——已 satisfied 但输入漂移的步骤与未跑步骤的差额才是
    真正需要重跑的面；调用方（executor）按指纹精确裁决。
    """
    consumers = {
        s.step_id for s in contract.steps
        if any(v == f"role:{role}" for v in s.role_inputs.values())
    }
    return consumers | downstream_closure(contract, consumers)


def plan_invalidate_on_param_change(
    contract: AnalysisWorkflowContract,
    step_id: str,
) -> Set[str]:
    """步骤参数变更 → 该步骤 + 下游闭包。"""
    return {step_id} | downstream_closure(contract, {step_id})


def style_only_invalidation(
    contract: AnalysisWorkflowContract,
) -> Set[str]:
    """style-only 变更永不触发科学重算（核心验收钉扎）。"""
    return set()


def collect_stale_steps(
    contract: AnalysisWorkflowContract,
    state: WorkflowRunState,
    *,
    current_identities: Dict[str, Dict[str, str]],
) -> List[Dict[str, Any]]:
    """全量对账：逐 satisfied 步骤重算指纹，输出漂移清单。

    current_identities：role → {param: identity} 由调用方从 session store
    活体读取（tools 层）；本函数纯比较。
    """
    stale: List[Dict[str, Any]] = []
    steps_by_id = {s.step_id: s for s in contract.steps}
    for step_id, entry in state.steps.items():
        if entry.state != "satisfied":
            continue
        step = steps_by_id.get(step_id)
        if step is None:
            continue
        identities: Dict[str, str] = {}
        for param, source in step.role_inputs.items():
            if source.startswith("role:"):
                role = source.removeprefix("role:")
                ident = (current_identities.get(role) or {}).get(param)
                if ident is None:
                    binding = contract.data_contracts.get(role)
                    ident = ref_identity(binding.ref) if binding else ""
                identities[param] = ident
            else:
                upstream = source.removeprefix("step:")
                identities[param] = f"fp:{state.steps[upstream].fingerprint}" \
                    if upstream in state.steps else ""
        fresh = step_fingerprint(
            step, input_identities=identities,
            science=contract.science_context)
        if fresh != entry.fingerprint:
            closure = downstream_closure(contract, {step_id})
            stale.append({
                "step_id": step_id,
                "old_fingerprint": entry.fingerprint[:16],
                "new_fingerprint": fresh[:16],
                "downstream": sorted(closure),
            })
    return stale
