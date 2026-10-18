"""Pi turn 收口期的澄清 envelope 采集（方向 3 / ADR-0222，god-module 拆分）。

自 ``agent_pi_bridge._stream_turn`` 的 agent_settled 分支原样搬移的内聚块：
turn 结算（settle projections）**之前**计算 blocking 澄清 envelope 并写章节
pending 章 —— goal evaluator / refusal 谓词与前端 SSE 读到的是同一份
「本 turn 提出了澄清」事实。

纪律（与 pi_post_dispatch 一致）：

- never-raise：澄清面绝不阻断收口（store 故障 / import skew 均吞掉）；
- 系统性故障不能只沉在 debug 日志里不可见 —— 记 ``clarification_collect_failed``
  指标（指标旁路再挂也绝不影响收口，仅 debug 记录）；
- 模块顶层不 import ``agent_pi_bridge``（防环）；依赖均在调用点惰性解析。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.services.gis_harness.requirement_ir.envelope import (
        ClarificationEnvelope,
    )

logger = logging.getLogger(__name__)


async def collect_settlement_clarification(
    turn_sid: str, turn_id: str
) -> Optional[ClarificationEnvelope]:
    """采集 turn 收口期的 blocking 澄清 envelope（never-raise）。

    envelope 存在 → 写章节 pending 章并返回（调用方在 task_complete 后、
    终态 done 前发射 SSE）；outcome == "clear" → 幂等对账收口为 resolved
    （answer 路由投影曾失败时 goal signal 不再永久卡在 REQUEST_CLARIFICATION）
    并返回 None；其余情况返回 None。
    """
    try:
        from app.services.gis_harness.requirement_ir import (
            envelope as _req_env,
        )
        from app.services.session_data import (
            session_data_manager as _sdm,
        )
        _env, _outcome = await _req_env.collect_turn_clarification(
            _sdm, turn_sid, turn_id,
        )
        from app.services.harness_kernel import (
            get_runtime as _get_rt,
        )
        if _env is not None:
            await _get_rt(turn_sid).update_chapter_clarification(
                status="pending",
                turn_id=turn_id,
                question=_env.questions[0].question_zh,
                questions=[
                    {"code": q.code,
                     "context_key": q.context_key,
                     "question_zh": q.question_zh}
                    for q in _env.questions
                ],
                request_id=_env.request_id,
            )
            return _env
        if _outcome == "clear":
            # 对账（review P2-2a）：文档存在且全部已答/豁免，但章节仍 pending
            # （answer 路由投影曾失败）→ 幂等收口为 resolved，goal signal 不再
            # 永久卡在 REQUEST_CLARIFICATION。
            await _get_rt(turn_sid).update_chapter_clarification(
                status="resolved",
                turn_id=turn_id,
            )
    except Exception:  # noqa: BLE001 — 澄清面绝不阻断收口
        logger.debug(
            "[PiBridge] clarification collect failed session=%s",
            turn_sid, exc_info=True,
        )
        try:
            from app.services.harness_kernel import (
                metrics as _hk_metrics,
            )
            # 系统性故障（import skew/store 变更）不能只沉在 debug 日志里不可见
            _hk_metrics.record("clarification_collect_failed")
        except Exception:  # noqa: BLE001 — 指标旁路再挂也不影响收口
            logger.debug(
                "[PiBridge] clarification failure-metric record failed "
                "session=%s",
                turn_sid, exc_info=True,
            )
    return None
