"""Worker placement envelope projection（H06 DoD2：resource envelope 真实消费）。

三个生产者的形状统一为 worker ``_placement_guard`` 可真实消费的
``{min_cpu, min_mem_mb, gpu}`` 扁平键：

1. **rg.v1 estimate dict**（workflow driver ``_estimate.as_dict()``）——
   此前形状失配：``as_dict()`` 产 ``dims`` 嵌套结构，而守卫读扁平的
   ``min_cpu/min_mem_mb/gpu`` → 全部落 0 → ``satisfies`` 恒真 → 生产路径
   守卫静默 no-op。本投影把 ``dims.memory_bytes`` 期望值与
   ``gpu_required`` 翻译为守卫语言，workflow 估算首次真实参与放置判定；
2. **cluster placement ``min_*`` 形状** —— 原样透传（placement 第 1 层
   语义不变，向后逐字节兼容）；
3. **geocompute plan-node ``ExecutionNode.estimate``** —— executor 派发
   时缺 envelope 则投影（plan compiler / agent_swarm 写入的 numeric
   hints 此前止步于 schema，从未到达 worker 准入）。

纪律：

- **fail-open**（与守卫既有姿态一致）：未知形状 / 投影异常 → ``{}``
  （不设约束），绝不因投影层把节点打成失败；
- **unknown ≠ 约束**：placement 层对 unknown 维不设门槛 —— ``unknown≠0``
  的保守**记账**是 governor 进程内准入的权威（ADR-0182），放置守卫只在
  有意义（known/estimated 且带数值）时拒绝；
- **开关**：``WEBGIS_PLACEMENT_ENVELOPE_PROJECTION=0`` 关闭 rg.v1 /
  plan-node 投影（``min_*`` 透传不受影响）；默认开启；
- envelope 只走 task_kwargs，不进幂等键（ADR-0214 D5 既有纪律）。
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024
#: rg.v1 dims 中的内存维键（Dimension.MEMORY_BYTES.value）
_MEMORY_DIM = "memory_bytes"
#: 已知 DimValue certainty 词表（rg.v1）；unknown/unavailable 不设约束
_MEANINGFUL_CERTAINTY = ("known", "estimated")

_PROJECTION_ENV = "WEBGIS_PLACEMENT_ENVELOPE_PROJECTION"

__all__ = [
    "project_worker_envelope",
    "envelope_from_node_estimate",
    "effective_dispatch_envelope",
]


def _projection_enabled() -> bool:
    return os.environ.get(_PROJECTION_ENV, "1").strip().lower() not in (
        "0", "false", "off", "no",
    )


def _project_rg_v1(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """rg.v1 ``as_dict()`` 形状 → 守卫键（有意义才约束，unknown 不设门槛）。"""
    out: Dict[str, Any] = {"min_cpu": 0, "min_mem_mb": 0, "gpu": 0}
    dims = envelope.get("dims")
    if isinstance(dims, dict):
        mem = dims.get(_MEMORY_DIM)
        if isinstance(mem, dict):
            certainty = str(mem.get("certainty") or "")
            if certainty in _MEANINGFUL_CERTAINTY:
                value = mem.get("expected")
                if value is None:
                    value = mem.get("max")  # range 缺失按 max 兜底（宁可高估）
                if value is None:
                    # review P3：与 DimValue.adjudged 同链 —— 只有 min 时用它
                    value = mem.get("min")
                if isinstance(value, (int, float)) and value > 0:
                    out["min_mem_mb"] = int(math.ceil(float(value) / _MIB))
    if envelope.get("gpu_required") is True:
        out["gpu"] = 1
    out["source"] = "rg.v1:" + str(envelope.get("resource_class") or "unknown")
    return out


def project_worker_envelope(envelope: Any) -> Dict[str, Any]:
    """任意生产者形状 → ``{min_cpu, min_mem_mb, gpu}``（纯函数，fail-open）。

    - ``min_*`` 扁平形状：原样透传（补齐缺键）；
    - rg.v1 形状（``dims``/``resource_class`` 键存在）：投影；
    - 其他 / 异常：``{}``（不设约束）。
    """
    if not isinstance(envelope, dict):
        return {}
    if "min_mem_mb" in envelope or "min_cpu" in envelope or "gpu" in envelope:
        # cluster placement 层形状 —— 逐字节兼容透传
        return {
            "min_cpu": envelope.get("min_cpu") or 0,
            "min_mem_mb": envelope.get("min_mem_mb") or 0,
            "gpu": envelope.get("gpu") or 0,
        }
    if "dims" in envelope or "resource_class" in envelope:
        if not _projection_enabled():
            return {}
        try:
            return _project_rg_v1(envelope)
        except Exception:  # noqa: BLE001 — 投影绝不阻断放置（fail-open 纪律）
            logger.warning("worker envelope rg.v1 projection failed; "
                           "guard passes without constraint", exc_info=True)
            return {}
    return {}


def envelope_from_node_estimate(node_estimate: Any) -> Dict[str, Any]:
    """plan-node ``ExecutionNode.estimate`` → 守卫键（缺内存声明 = 不约束）。"""
    if node_estimate is None:
        return {}
    mem_mb = getattr(node_estimate, "memory_mb", None)
    out: Dict[str, Any] = {"min_cpu": 0, "min_mem_mb": 0, "gpu": 0,
                           "source": "plan.node_estimate"}
    if isinstance(mem_mb, (int, float)) and mem_mb > 0:
        out["min_mem_mb"] = int(math.ceil(float(mem_mb)))
    return out


def effective_dispatch_envelope(
    explicit: Optional[Dict[str, Any]],
    node_estimate: Any = None,
) -> Optional[Dict[str, Any]]:
    """executor 派发用有效 envelope：显式 > plan-node 投影 > None。

    显式 envelope 原样透传（workflow driver 已是 rg.v1 dict，worker 侧
    再投影；cluster 层已是 min_* 形状）—— 本函数不二次加工显式值。
    """
    if explicit is not None:
        return explicit
    if not _projection_enabled():
        return None
    projected = envelope_from_node_estimate(node_estimate)
    return projected or None
