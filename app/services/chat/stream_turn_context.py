"""一次 legacy 流式 turn 的跨阶段显式状态（H03 阶段化拆分）。

独立模块化（S3/Wave G 债务棘轮）：``execution_engine`` 已是 god-module
（#1544），阶段状态契约定驻于此，引擎经组合消费 —— 字段分组即阶段
输入输出契约（详见类 docstring）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from app.services.chat.sse_contracts import TurnEventEmitter

if TYPE_CHECKING:
    # 仅类型注解需要（避免与 execution_engine 的导入环；运行时经 engine
    # 参数组合注入，无顶层依赖）。
    from app.services.chat.execution_engine import ChatExecutionEngine

logger = logging.getLogger(__name__)

class StreamTurnContext:
    """一次 legacy 流式 turn 的跨阶段显式状态（H03 阶段化拆分）。

    ``chat_stream`` 曾是 964 行单生成器：阶段间变量靠闭包捕获传递，
    断连路径甚至用 ``locals().get("_wave_flushed", True)`` 探测**未定义
    变量**。拆分后所有 ``_stream_*`` 阶段方法读写这一显式状态对象 ——
    下方字段分组即阶段输入输出契约：

    - admission/请求入参：session_id / message / user_id / project_id /
      skill_name / map_state
    - context setup 产出：messages（锁下加载的会话历史，RUN-13）
    - turn 身份（_stream_bind_turn_identity）：task / turn_id / run_id /
      rt_ev / rt_cm / tev_cm / emitter / owner_token / hk_lock_token
    - planner（_stream_planner_phase）：plan / plan_existed_this_turn
    - 主循环（_stream_rounds/_stream_single_round）：turn_start_user_message
      / turn_id_for_catalog / turn_deadline / no_progress_streak /
      executed_tools / turn_finished
    - 工具波（_stream_tool_wave，断连路径 F9-parity 消费）：wave_flushed /
      pending_tools / completion_results / standard_calls / tool_result_msgs

    ``turn_finished`` 是阶段向编排层回传「本 turn 已发过终态、立即收尾」
    的唯一信号（对应拆分前各分支的裸 ``return``）。
    ``plan_finalized_event`` / ``aborted_step_events`` 是拆分前两个嵌套
    闭包的方法化。
    """

    def __init__(
        self,
        *,
        engine: "ChatExecutionEngine",
        session_id: str,
        message: str,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        skill_name: Optional[str] = None,
        map_state: Optional[dict] = None,
        engine_lock: Optional[Any] = None,
    ) -> None:
        # 请求入参（不可变段）
        self.engine = engine
        self.session_id = session_id
        self.message = message
        self.user_id = user_id
        self.project_id = project_id
        self.skill_name = skill_name
        self.map_state = map_state
        # RUN-03 全 turn 持锁的锁对象（ADR-0180 legacy_bind_engine_lock 需要）
        self.lock = engine_lock
        # context setup 产出（RUN-13：锁下加载；默认空表保证 trim 全态）
        self.messages: list = []
        # turn 身份
        self.task: Any = None
        self.turn_id: Optional[str] = None
        self.run_id: Optional[str] = None
        self.rt_ev: Any = None
        self.rt_cm: Any = None
        self.tev_cm: Any = None
        self.emitter: Optional[TurnEventEmitter] = None
        self.owner_token: Optional[str] = None
        self.hk_lock_token: Any = None
        # planner
        self.plan: Any = None
        self.plan_existed_this_turn: bool = False
        # 主循环
        self.turn_start_user_message: str = ""
        self.turn_id_for_catalog: Optional[str] = None
        self.turn_deadline: float = 0.0
        self.no_progress_streak: int = 0
        self.executed_tools: set = set()
        # 工具波（断连路径按此重建 F9-parity 落库）
        self.wave_flushed: bool = True
        self.pending_tools: list = []
        self.completion_results: dict = {}
        self.standard_calls: list = []
        self.tool_result_msgs: list = []
        # 编排信号
        self.turn_finished: bool = False

    def plan_finalized_event(self) -> Optional[str]:
        """design-v3：只有本轮确实存在/产生过非终态计划才发 plan_finalized，
        避免无计划轮次或已恢复出终态计划时发出 spurious finalize。"""
        if not self.plan_existed_this_turn:
            return None
        try:
            from app.services.chat import planner as _planner
            plan_obj = _planner.get_plan(self.session_id)
            if plan_obj is None:
                return None
            skipped = [s.n for s in plan_obj.steps if not s.done]
            return self.emitter.plan_finalized(self.task.id, skipped)
        except Exception as e:
            logger.warning(f"[chat_execution_engine] plan_finalized 构造失败: {e}")
            return None

    def aborted_step_events(self) -> list:
        """F7/F8: 取消/断连路径上把未完成的 step 记 cancelled
        （绝不留 running），并生成对应的 step_cancelled SSE 事件。"""
        evts: list = []
        for p in self.pending_tools:
            if p["step"].id in self.completion_results:
                continue
            try:
                self.engine.tracker.cancel_step(self.task.id, p["step"].id)
            except Exception:
                continue
            evts.append(
                self.emitter.step_cancelled(self.task.id, p["step"].id, p["tool_name"])
            )
        return evts
