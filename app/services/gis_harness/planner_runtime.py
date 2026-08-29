"""Planner Runtime —— 进程级共享规划服务（GIS Harness Runtime v3, Phase C）。

v2 post-merge 审计（A2）：``webgis_map_intent`` / ``webgis_map_product`` /
``plan_orchestrator`` 各自 ``MapProductPlanner()`` 临时建实例，``_plan_memo``
只活在一次调用里 —— intent→product 链（同 query 2-3 次规划）跨调用零复用，
memo 形同虚设。

本模块提供进程级共享 planner：

    PlannerRuntime（本模块，immutable service）
        ├── intent planning        plan_from_intent（纯函数）
        ├── profile finalization   finalize_with_profile（纯函数）
        ├── bounded memo           OrderedDict，64 条，FIFO 驱逐
        └── manifest awareness     memo 键含 manifest 指纹，registry 内容
                                    变化自动失效

约束（与 ADR-0076/0080 一致）：

- planner **不是** session truth —— 不保存 session mutable state；会话态
  继续在 SessionPlan / GISWorldState / MapSpec 中；
- planner 无 I/O、无 LLM 依赖，``plan_from_intent`` 确定性（同输入同输出），
  跨会话/跨请求共享 memo 安全（键含 intent 全量 + 工具面 + 项目记忆 +
  manifest 指纹，不同输入必 miss）；
- registry 单例被替换（``reset_recipe_registry`` 等，测试场景）时身份守卫
  自动重建 planner —— 不复用旧 registry 引用；
- 测试隔离：``reset_planner_runtime()``。
"""
from __future__ import annotations

import threading
from typing import Optional

from app.services.gis_harness.planner import MapProductPlanner

_runtime_lock = threading.RLock()
_shared_planner: Optional[MapProductPlanner] = None


def get_planner_runtime() -> MapProductPlanner:
    """进程级共享 planner 实例（compile once, plan everywhere）。

    registry 单例身份变化（测试 reset / 模板热注册重建 registry）时重建
    planner —— memo 随之丢弃是正确语义（registry 内容已变，旧 memo 键的
    manifest 指纹维度不再可信）。
    """
    global _shared_planner
    with _runtime_lock:
        if _shared_planner is None or not _shared_planner.attached_to_current_registries():
            _shared_planner = MapProductPlanner()
        return _shared_planner


def reset_planner_runtime() -> None:
    """测试隔离：丢弃共享实例（下一个 get 重建，memo 清零）。"""
    global _shared_planner
    with _runtime_lock:
        _shared_planner = None


__all__ = ["get_planner_runtime", "reset_planner_runtime"]
