"""Long-Horizon Continuation 裁决（ADR-0119 决策 D7）。

V5 基线（baseline G5）：resume anchor 之外，「继续执行」的裁决散落 ——
数据资格不足 → deepen 的回路、渲染失败 → 修复 → 再验证的回路各有局部
预算（runtime_repair per-fingerprint ledger、finalizer MAX_PASSES），
但没有**单一裁决点**；恢复（resume）后如何从 evidence 重新判定继续/
中止没有显式语义。

V6 契约：
- **单一纯函数裁决点** ``decide_continuation``：输入 = recovery_state
  （循环预算）、durable ledger attempts、observation 摘要、资格状态、
  最近失败分类；输出 = 有界 ContinuationDecision（verdict + loop +
  披露）。同输入必同输出 —— resume 后**不机械 replay**，从证据重判。
- **两条生产回路显式化**：
  ① 数据资格不足 → ``deepen_profile``（LOOP_BUDGETS.deepen）→ 重资格
     （``requalify``）→ continue；
  ② 渲染/运行时失败 → classify（failure_taxonomy）→ ``repair`` →
     reobserve（rendered-state 再验证，D9 observation ladder）。
- **预算耗尽 → abort_with_disclosure**：任何回路余量 0 → 诚实中止，
  绝不无限对抗；失败分类自身的 REMEDIATION_POLICY（≤3）仍生效。
- 接线：runtime_repair outcome / finalization finding 处调用；不新开
  主循环、不做第二 planner —— 裁决只产「下一步语义」，执行仍归既有
  引擎（Pi / finalizer / deepen_profile 工具）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from app.services.gis_harness.durable_context import LOOP_BUDGETS
from app.services.gis_harness.failure_taxonomy import (
    HarnessFailureClass,
    classify_harness_failure,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContinuationDecision:
    """一次 long-horizon 续行裁决（有界、可披露）。"""

    verdict: str            # continue | deepen_profile | requalify |
                            # remediate_and_retry | reobserve | replan |
                            # abort_with_disclosure
    loop: str = ""          # deepen | requalify | repair | replan | ""
    reason: str = ""
    disclosure: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # 单串 disclosure 归一为单元素 tuple（防 ("...", "...") 少尾逗号
        # 的隐式拼接退化为 str —— 迭代面契约恒为 tuple[str, ...]）。
        if isinstance(self.disclosure, str):
            object.__setattr__(self, "disclosure", (self.disclosure,))

    def to_payload(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "loop": self.loop,
            "reason": self.reason[:160],
            "disclosure": [d[:160] for d in self.disclosure[:4]],
        }


def loops_remaining(recovery_state: Dict[str, Any]) -> Dict[str, int]:
    """recovery_state → 各回路余量（负值钳 0；缺键按满额）。"""
    used = (recovery_state or {}).get("loops") or {}
    out: Dict[str, int] = {}
    for loop, budget in LOOP_BUDGETS.items():
        out[loop] = max(0, int(budget) - int(used.get(loop) or 0))
    return out


def decide_continuation(
    *,
    recovery_state: Dict[str, Any],
    observation: Optional[Dict[str, Any]] = None,
    qualification: Optional[Dict[str, Any]] = None,
    failure: Optional[Dict[str, Any]] = None,
    ledger_attempts: int = 0,
) -> ContinuationDecision:
    """long-horizon 续行裁决（确定性纯函数）。

    优先级（先急后缓、先硬预算后软回路）：
    1. 总预算耗尽（durable ledger 或全回路余量 0）→ abort_with_disclosure；
    2. 最近失败为不可恢复类（cancelled / budget_exhausted）→ abort；
    3. 渲染/运行时失败 → repair（余量内）或 reobserve（repair 已计）→
       rendered-state 再验证；
    4. 数据资格不足（qualification.status ∈ blocked/insufficient 且
       deepen 可加深）→ deepen_profile →（下一轮）requalify → continue；
    5. observation 缺席/不完整（诚实三态 pending）→ reobserve；
    6. 其余 → continue。
    """
    remaining = loops_remaining(recovery_state)
    total_remaining = sum(remaining.values())

    # 1) 硬预算
    if total_remaining <= 0:
        return ContinuationDecision(
            verdict="abort_with_disclosure",
            reason="all continuation loops exhausted",
            disclosure=("所有续行回路预算耗尽（deepen/repair/replan）—— "
                        "诚实部分完成；恢复后按新证据重判，不自动续跑"),
        )
    if failure and ledger_attempts >= 3:
        return ContinuationDecision(
            verdict="abort_with_disclosure",
            reason=f"failure budget exhausted for {failure.get('tool') or 'tool'}",
            disclosure=("该工具失败预算耗尽（durable ledger 跨 worker/重启"
                        "一致）—— 不再重试"),
        )

    # 2) 不可恢复失败类
    if failure:
        fc = _failure_class_of(failure)
        if fc in (HarnessFailureClass.CANCELLED, HarnessFailureClass.BUDGET_EXHAUSTED):
            return ContinuationDecision(
                verdict="abort_with_disclosure",
                reason=f"unrecoverable failure class: {fc.value}",
                disclosure=(f"失败类 {fc.value} 不可续行 —— 诚实中止"),
            )
        # 3) 渲染/运行时失败回路
        if remaining["repair"] > 0:
            return ContinuationDecision(
                verdict="remediate_and_retry",
                loop="repair",
                reason=f"repairable failure ({fc.value}) with budget",
                disclosure=(f"失败 {fc.value} → 有界修复后重试"
                            f"（repair 余量 {remaining['repair']}）"),
            )
        return ContinuationDecision(
            verdict="reobserve",
            reason="repair budget exhausted — re-verify rendered state honestly",
            disclosure=("修复预算耗尽 → 重新取证 rendered-state，"
                        "以真实观测裁决而非再重试"),
        )

    # 4) 数据资格回路
    if qualification is not None:
        q_status = str(qualification.get("status") or "").lower()
        if q_status in ("blocked", "insufficient", "underqualified"):
            if remaining["deepen"] > 0:
                return ContinuationDecision(
                    verdict="deepen_profile",
                    loop="deepen",
                    reason=f"qualification {q_status} — deepen dataset profile",
                    disclosure=(f"数据资格不足（{q_status}）→ 深化画像后重资格"
                                f"（deepen 余量 {remaining['deepen']}）"),
                )
            if remaining["requalify"] > 0:
                return ContinuationDecision(
                    verdict="requalify",
                    loop="requalify",
                    reason="deepen exhausted — re-qualify against facts",
                    disclosure=("画像深化预算耗尽 → 按既有事实重资格"
                                "（可能降级方法并披露）"),
                )
            return ContinuationDecision(
                verdict="abort_with_disclosure",
                reason="qualification loops exhausted",
                disclosure=("数据资格回路耗尽 —— 拒绝带病执行"),
            )

    # 5) 观察诚实三态
    if observation is not None:
        obs_state = str(observation.get("state") or "").lower()
        if obs_state in ("pending", "unknown", ""):
            return ContinuationDecision(
                verdict="reobserve",
                reason=f"observation state={obs_state or 'unknown'}",
                disclosure=("观察态 pending/unknown —— 先取证再裁决，"
                            "不以工具调用成功假通过"),
            )

    return ContinuationDecision(verdict="continue")


def _failure_class_of(failure: Dict[str, Any]) -> HarnessFailureClass:
    """failure payload → HarnessFailureClass（已分类则直取，否则再分类）。"""
    fc_value = str((failure or {}).get("class") or "").strip()
    if fc_value:
        try:
            return HarnessFailureClass(fc_value)
        except ValueError:
            pass
    return classify_harness_failure(
        code=str((failure or {}).get("code") or "") or None,
        message=str((failure or {}).get("message") or "") or None,
        error_type=str((failure or {}).get("error_type") or "") or None,
    )


__all__ = [
    "ContinuationDecision",
    "decide_continuation",
    "loops_remaining",
]
