"""跨层工具拒绝机器码单一事实源与对照表（H01，ADR-0224 结果面修复）。

现状（H01 RECON K7/K8）：turn 面 drop codes（``pi_surface_receipt``）、
dispatch/registry 拒绝码（``capability_bind`` / ``registry`` /
``pi_input_gate``）与 resultAbi envelope 的 ``error.class`` 是三套独立词表
——同一事实（凭证缺席 / tier-3 / provider 断路）在不同层不同名，遥测与完成
判据无法对齐归因；桥内 not-found / tier-3 拒绝甚至只有文本。

本模块**不改变任何既有码值**（纯加性收编）：

1. 机器码常量——与各生产者字面量逐位一致（`test_pi_h01_result_contract`
   与 `test_h01_reason_code_parity` 钉住漂移）；
2. ``ERROR_CLASS_BY_CODE``——registry/dispatch 权威码 → envelope
   ``error.class`` 的确定性映射（先于文本匹配，杜绝启发式分类）；
3. ``TURN_DROP_PARITY``——turn 面 drop code ↔ dispatch canonical 事实码
   对照，供一致性守卫与跨层归因。

不建第二裁决体系：裁决仍在 ToolDispatchService / registry / turn qualification
原位；本模块只是词表投影的单一事实源。
"""
from __future__ import annotations

from typing import Dict

# ── 机器码常量（与生产者字面量逐位一致） ─────────────────────────────

#: registry.dispatch 存在性 / 状态闸（app/tools/registry.py）
UNKNOWN_TOOL = "UNKNOWN_TOOL"
TOOL_NOT_EXECUTABLE = "TOOL_NOT_EXECUTABLE"
#: registry tier-3 确认闸（app/tools/registry.py）
TIER3_CONFIRMATION_REQUIRED = "TIER3_CONFIRMATION_REQUIRED"
#: registry 凭证/权限闸（app/tools/registry.py，#1402 / H05）
CREDENTIALS_REQUIRED = "CREDENTIALS_REQUIRED"
PERMISSION_DENIED = "PERMISSION_DENIED"
#: Pi 桥输入闸（app/services/chat/pi_input_gate.py GATE_ERROR_CODE）
SCHEMA_VALIDATION_REJECTED = "SCHEMA_VALIDATION_REJECTED"
#: dispatch capability bind（capability_bind.py，ADR-0213）
CAPABILITY_INELIGIBLE = "CAPABILITY_INELIGIBLE"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
#: resultAbi 取消路径（pi_result_abi.py）
OPERATION_CANCELLED = "OPERATION_CANCELLED"
TOOL_ERROR = "TOOL_ERROR"

#: envelope ``error.class`` 封闭词表（``pi_result.v1``；H01 起新增
#: ``provider_unavailable`` 成员——加性扩展，消费方按未知 class 字符串
#: 兜底不破坏）。
ERROR_CLASSES = frozenset({
    "capability_ineligible",
    "provider_unavailable",
    "schema_validation",
    "timeout",
    "cancelled",
    "not_found",
    "authorization",
    "tool_error",
})

#: registry/dispatch 权威码 → envelope error.class（确定性映射；
#: 未知码回落 ``tool_error``，由 ``_error_class`` 文本兜底继续覆盖）。
ERROR_CLASS_BY_CODE: Dict[str, str] = {
    CAPABILITY_INELIGIBLE: "capability_ineligible",
    PROVIDER_UNAVAILABLE: "provider_unavailable",
    UNKNOWN_TOOL: "not_found",
    TOOL_NOT_EXECUTABLE: "not_found",
    TIER3_CONFIRMATION_REQUIRED: "authorization",
    CREDENTIALS_REQUIRED: "authorization",
    PERMISSION_DENIED: "authorization",
    SCHEMA_VALIDATION_REJECTED: "schema_validation",
    OPERATION_CANCELLED: "cancelled",
}

#: turn 面 drop code（pi_surface_receipt.DROP codes）→ dispatch canonical
#: 事实码（capability_reasons.REASON_CODES 词表）对照。键值均为既有词表
#: 成员；新增 drop code（如 ``permission_not_granted``）时必须在此登记，
#: 未登记由一致性守卫测试暴露。
TURN_DROP_PARITY: Dict[str, str] = {
    "tier3_excluded": "confirm_required",
    "side_effect_destructive": "confirm_required",
    "credential_absent": "credentials_missing",
    "permission_not_granted": "permission_not_granted",
    "offline_network_tool": "offline_network_required",
    "durable_worker_unavailable": "dependency_unavailable",
}


def error_class_for_code(code: str) -> str:
    """权威机器码 → envelope error.class；未知码返回 ``tool_error``。"""
    return ERROR_CLASS_BY_CODE.get(str(code or "").strip(), TOOL_ERROR)


__all__ = [
    "UNKNOWN_TOOL",
    "TOOL_NOT_EXECUTABLE",
    "TIER3_CONFIRMATION_REQUIRED",
    "CREDENTIALS_REQUIRED",
    "PERMISSION_DENIED",
    "SCHEMA_VALIDATION_REJECTED",
    "CAPABILITY_INELIGIBLE",
    "PROVIDER_UNAVAILABLE",
    "OPERATION_CANCELLED",
    "TOOL_ERROR",
    "ERROR_CLASSES",
    "ERROR_CLASS_BY_CODE",
    "TURN_DROP_PARITY",
    "error_class_for_code",
]
