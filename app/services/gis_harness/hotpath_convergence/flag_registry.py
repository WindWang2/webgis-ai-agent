"""GIS Harness hotpath feature-flag registry（方向 09 flag 收敛）.

单一真相：登记 Pi / GIS Harness 热路径上所有行为 flag 的环境变量名、默认
状态、类别与咨询点。``tests/unit/test_hotpath_flag_registry.py`` 做双向
一致性检查：

1. 扫描热路径目录（``app/services/gis_harness/``、``app/services/chat/``、
   ``app/agent_pi_bridge.py``）源码中的 ``GIS_*`` 字面量，每一个都必须登记
   —— 防止新 flag 绕过盘点静默增殖（行为矩阵爆炸的根因）；
2. registry 里的每个条目都能在仓内源码中找到咨询点 —— 防止死条目。

类别语义（不再新增第四种）：

- ``stable``：默认 ON，保留 kill switch（=0/false/off/no 关闭）。稳定热路径
  能力不搞「代码存在但生产不走」。
- ``opt_in``：默认 OFF，需显式开启（重量级/改变产品行为的入口）。
- ``mode``：非布尔模式枚举（值本身有语义，空值 = 模块默认）。

裁决记录：``GIS_MISSION_HOTPATH`` 保持 opt-in（创建持久 Mission 是重副作用
入口，默认开启会在每个多步 GIS turn 写 MissionRuntime）；其余 harness 披露
/决策链 flag 全部已是默认 ON 的 kill switch 形态 —— 本 registry 不改任何
默认值，只收口可见性（ADR-0204）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FlagKind = Literal["stable", "opt_in", "mode"]


@dataclass(frozen=True)
class HotpathFlag:
    env: str
    kind: FlagKind
    default_on: bool
    owner: str           # 首要咨询模块（相对 app/ 的路径）
    purpose: str


REGISTRY: tuple[HotpathFlag, ...] = (
    # ── 决策链 / dispatch 闸（V9，#1395）───────────────────────────────
    HotpathFlag("GIS_CAPABILITY_DISPATCH_BIND", "stable", True,
                "services/gis_harness/hotpath_convergence/capability_bind.py",
                "dispatch 前能力资格绑定：INELIGIBLE 且有合格替代时拒绝"),
    HotpathFlag("GIS_CAPABILITY_GRAPH_V8", "stable", True,
                "services/gis_harness/capability_graph.py",
                "capability graph 投影（V8）"),
    HotpathFlag("GIS_CAPABILITY_PLANNING_V1", "stable", True,
                "services/gis_harness/capability_resolution.py",
                "capability 解析/规划 V1"),
    HotpathFlag("GIS_CAPABILITY_RETRIEVAL_V7", "stable", True,
                "services/gis_harness/capability_descriptors.py",
                "capability 描述子检索 V7"),
    HotpathFlag("GIS_MISSION_HOTPATH", "opt_in", False,
                "services/gis_harness/hotpath_convergence/flags.py",
                "多步 GIS turn 自动 Mission create/reuse（需 GIS_MISSION_RUNTIME 同时开启）"),
    HotpathFlag("GIS_MISSION_RUNTIME", "stable", True,
                "services/mission_runtime/service.py",
                "MissionRuntime 总闸（mission hotpath 的第二重门）"),
    HotpathFlag("GIS_SKILL_POLICY", "stable", True,
                "services/gis_harness/skills/policy.py",
                "SkillPolicy 确定性技能决策（D02）"),
    # ── 调度 / 检索 / 上下文 ──────────────────────────────────────────
    HotpathFlag("GIS_CLAIM_INGEST", "stable", True,
                "services/gis_harness/hotpath_convergence/flags.py",
                "settle 时进程内 claim ingest"),
    HotpathFlag("GIS_CONTEXT_POLICY", "stable", True,
                "services/chat/context_policy.py",
                "上下文组装策略层"),
    HotpathFlag("GIS_TOOL_RETRIEVAL_V4", "stable", True,
                "services/chat/tool_retrieval.py",
                "工具检索 V4"),
    HotpathFlag("GIS_TOOL_RETRIEVAL_V6", "stable", True,
                "services/chat/tool_retrieval.py",
                "工具检索 V6（覆盖 V4；semantic_retrieval 同门）"),
    HotpathFlag("GIS_TOOL_SEMANTIC", "stable", True,
                "services/chat/tool_surface_v3.py",
                "工具语义面（=0 关闭；唯一仅 kill-switch 形态的稳定 flag）"),
    HotpathFlag("GIS_SITUATION_CONTEXT", "stable", True,
                "services/gis_situation/compiler.py",
                "态势环境块编译（env_block）"),
    # ── 运行态投影 / 完成度 / 恢复 ────────────────────────────────────
    HotpathFlag("GIS_RUNTIME_STATE_MACHINE", "stable", True,
                "services/gis_harness/runtime_state_machine.py",
                "HarnessRuntime 任务级阶段状态机（V7，ADR-0134）"),
    HotpathFlag("GIS_WORKFLOW_INSTANCE", "stable", True,
                "services/gis_harness/workflow_instance.py",
                "WorkflowInstance 运行态（V4，ADR-0104）"),
    HotpathFlag("GIS_WORKFLOW_RUNTIME_V6", "stable", True,
                "services/gis_harness/runtime_bridge.py",
                "typed DAG 运行态投影（V6 Wave 2）"),
    HotpathFlag("GIS_RECOVERY_LEDGER", "stable", True,
                "services/gis_harness/recovery_ledger.py",
                "恢复台账（fail-closed 重试/重规划预算）"),
    HotpathFlag("GIS_INTENT_DIFF_REPLAN", "stable", True,
                "services/session_plan.py",
                "intent diff 驱动的重规划"),
    # ── 追踪 / 评估 ───────────────────────────────────────────────────
    HotpathFlag("GIS_TRACE_PERSIST", "stable", True,
                "services/gis_harness/trace_store.py",
                "证据链 JSONL 持久化（ADR-0103）"),
    HotpathFlag("GIS_TRACE_COMPRESS", "stable", True,
                "services/gis_harness/trace_store.py",
                "证据链压缩"),
    HotpathFlag("GIS_TRACE_FSYNC", "opt_in", False,
                "services/gis_harness/trace_store.py",
                "证据链逐条 fsync（性能代价，默认关）"),
    # ── 委派 / 评估器（opt-in 或 mode）────────────────────────────────
    HotpathFlag("GIS_HARNESS_DELEGATION", "opt_in", False,
                "services/gis_harness/delegation.py",
                "harness→specialist 委派"),
    HotpathFlag("GIS_SWARM_ORCHESTRATOR", "opt_in", False,
                "agent_pi_bridge.py",
                "SwarmBridge 委派网关总闸（ADR-0187）"),
    HotpathFlag("GIS_FINAL_DISPLAY_CONFIRM", "mode", True,
                "services/gis_harness/display_confirmation.py",
                "终态显示确认模式（auto/…；空值 = 模块默认）"),
    HotpathFlag("GIS_VISUAL_EVALUATOR", "mode", False,
                "services/gis_harness/visual_evaluator.py",
                "VLM 视觉评估器（空值 = 关闭）"),
)

_REGISTRY_BY_ENV = {f.env: f for f in REGISTRY}


def get_flag(env: str) -> HotpathFlag | None:
    return _REGISTRY_BY_ENV.get(env)


def registered_envs() -> frozenset[str]:
    return frozenset(_REGISTRY_BY_ENV)


__all__ = [
    "HotpathFlag",
    "REGISTRY",
    "get_flag",
    "registered_envs",
]
