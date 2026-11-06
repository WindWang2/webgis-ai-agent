"""产品最终门（ADR-0284 D6）—— partial ≠ final 的 ref 资格裁决。

语义：
- 只有 ref **命中某工作流 envelope** 且该 run 非 satisfied（或科学终局门
  blocked）时才不合格——非工作流来源的 ref（用户上传/单工具直出）不归
  本门管辖（它们没有「部分失败」语义）；
- 裁决是**披露面**：调用方（webgis_map_product）据此外科式降级（不合格
  ref 不作为成品层 + 警告），不中断产品组装；
- ``GIS_AWF_PRODUCT_GATE=0`` bit 级回退（调用方检查，本模块零配置依赖）。

纯函数（states 注入）；``load_workflow_states`` 是唯一的 IO 适配（经
session store alias 枚举读 ``analysis_workflow.v1:*`` 状态块，有界 ≤32）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from app.services.gis_harness.analysis_workflows.state import (
    state_from_payload,
)

#: 单会话加载的工作流状态上限（有界——防御异常会话拖垮产品组装）。
MAX_WORKFLOW_STATES = 32

#: 门结论码（封闭）。
REASON_RUN_NOT_SATISFIED = "AWF_RUN_NOT_SATISFIED"
REASON_SCIENCE_BLOCKED = "G-SCIENCE-BLOCKED"

_ALIAS_PREFIX = "analysis_workflow.v1:"


async def load_workflow_states(session_id: str) -> Dict[str, Any]:
    """读会话内全部工作流运行态（alias 前缀过滤；有界）。

    IO 失败=返回空 dict（门退化为不裁决——诚实披露优于假门）。
    """
    from app.services.session_data import session_data_manager

    states: Dict[str, Any] = {}
    try:
        refs = await session_data_manager.list_refs(session_id)
    except Exception:  # noqa: BLE001 — 枚举失败=门不可用
        return states
    for ref_id, alias in list(refs.items()):
        if not isinstance(alias, str) or not alias.startswith(_ALIAS_PREFIX):
            continue
        try:
            payload = await session_data_manager.get(session_id, ref_id)
        except Exception:  # noqa: BLE001 — 单块失败不影响其余
            continue
        state = state_from_payload(
            payload) if isinstance(payload, dict) else None
        if state is not None and len(states) < MAX_WORKFLOW_STATES:
            states[state.workflow_id] = state
    return states


def assess_refs_eligibility(
    states: Dict[str, Any], refs: Iterable[str],
) -> List[Dict[str, Any]]:
    """逐 ref 裁决成品资格（纯函数；找不到源工作流的 ref = 合格）。"""
    seen: List[str] = []
    findings: List[Dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, str) or not ref or ref in seen:
            continue
        seen.append(ref)
        finding: Dict[str, Any] = {
            "ref": ref, "eligible": True, "run_status": "",
            "reason_code": "", "workflow_id": "", "step_id": "",
        }
        for state in states.values():
            hit = next(
                (env for env in state.envelopes.values()
                 if env.ref == ref), None)
            if hit is None:
                continue
            finding["workflow_id"] = state.workflow_id
            finding["step_id"] = hit.step_id
            finding["run_status"] = state.status
            gate = (state.science_validation or {}).get("final_gate", "")
            if gate == "blocked":
                finding["eligible"] = False
                finding["reason_code"] = REASON_SCIENCE_BLOCKED
            elif state.status != "satisfied":
                finding["eligible"] = False
                finding["reason_code"] = (
                    f"{REASON_RUN_NOT_SATISFIED}:{state.status}")
            break
        findings.append(finding)
    return findings


__all__ = [
    "MAX_WORKFLOW_STATES",
    "REASON_RUN_NOT_SATISFIED",
    "REASON_SCIENCE_BLOCKED",
    "assess_refs_eligibility",
    "load_workflow_states",
]
