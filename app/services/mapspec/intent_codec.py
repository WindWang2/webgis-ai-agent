"""MapSpec mutation Body → 引擎 Intent 的单一映射源（ADR-0201）。

H02 解巨石后映射本体登记在 ``mutation_registry``（每 descriptor 一条
codec）；本模块保留既有函数签名（路由 + proposal merge 消费面），
实现改为 registry 派生 —— 新增 Body 只需在 registry 登记一处。

校验失败抛 ``ValueError``（消息与原路由逐字一致）——路由层捕获取 400。
"""
from __future__ import annotations

from app.services.mapspec.lifecycle_engine import MutationIntent


def body_to_intent(req) -> MutationIntent:
    """14 Body discriminated union → 引擎 MutationIntent（registry 派生）。"""
    from app.services.mapspec.mutation_registry import MUTATION_REGISTRY

    return MUTATION_REGISTRY.body_to_intent(req)

def intent_kind(body) -> str:
    """Body 的 intent 词（存证/日志用）。"""
    return str(getattr(body, "intent", type(body).__name__))


def intent_target(body) -> str:
    """Body 的主结构目标（layer/component id；无则空串）。"""
    for attr in ("layer_id", "component_id"):
        value = getattr(body, attr, None)
        if value:
            return str(value)
    return ""


__all__ = ["body_to_intent", "intent_kind", "intent_target"]
