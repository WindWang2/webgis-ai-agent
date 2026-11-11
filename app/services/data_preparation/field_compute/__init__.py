"""类型化字段计算与可复现派生属性工作台（G19-TYPED-FIELD-COMPUTE）。

独立域：闭合类型化 IR → 同一解释器的预览/全量执行 → 版本锚与原子登记 →
UI / Pi 工具消费。原件不可变，输出新 ``ref:fc-*``；不写 G14 的编辑 journal，
不复制 preparation / Data Fabric / MapSpec 的任何权威。
"""
from app.services.data_preparation.field_compute.contracts import (
    FUNCTION_SET_VERSION, IR_VERSION, RECEIPT_VERSION, CompileError,
    ExecError, ExprNode, ComputePlan, ComputeReceipt, InputAnchor, ScopeSpec,
)

__all__ = [
    "IR_VERSION", "FUNCTION_SET_VERSION", "RECEIPT_VERSION",
    "CompileError", "ExecError", "ExprNode", "ComputePlan", "ComputeReceipt",
    "InputAnchor", "ScopeSpec",
]
