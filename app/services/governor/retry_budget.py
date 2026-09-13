"""RetryBudget（R10，ADR-0182 D8）：防指数重试风暴。

既有仓里 ≥10 处独立重试点（recon §3.7：tool/HTTP/Data Fabric/workflow/
Pi/self-heal），最坏情形级联乘数 3×2×5×3 —— 无任何聚合视图。V1 的
RetryBudget 是 governor 面的**统一协调器**：

- 令牌制：进程级总量 + 每 session 份额；class 词表封闭
  （contract.RetryClass）；
- ``retry_allowed`` 是重试决策的**咨询点**（V1 不改造既有重试点 ——
  避免热区扩散；第一消费方是 governor 自己的 dispatch 面）；
- 硬保证：session 被 cancel 后 token 清零 —— **用户取消后重试必须停止**；
  retry 与 fallback 互斥（degrade 决策存在时 retry_allowed=False）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Tuple

from app.services.governor.contract import RetryClass
from app.services.governor.metrics import observe_retry

#: RetryBudget 的决策原因（可解释；进 trace）
ALLOW = "allow"
DENY_GLOBAL_EXHAUSTED = "deny_global_exhausted"
DENY_SESSION_EXHAUSTED = "deny_session_exhausted"
DENY_CANCELLED = "deny_cancelled"
DENY_DEGRADE_TAKEN = "deny_degrade_taken"


@dataclass
class _SessionTokens:
    tokens: float
    charges: Dict[str, int] = field(default_factory=dict)
    cancelled: bool = False
    last_charge_at: float = 0.0


class RetryBudget:
    """进程级 + 会话级双层令牌账（线程安全）。"""

    def __init__(self, *, global_tokens: int = 120,
                 session_tokens: int = 12):
        self._global_tokens = float(global_tokens)
        self._session_tokens = float(session_tokens)
        self._global_left = float(global_tokens)
        self._sessions: Dict[str, _SessionTokens] = {}
        self._lock = threading.Lock()
        #: degrade/fallback 已发生的 (session, op) 短窗集合（互斥语义）
        self._degraded: Dict[Tuple[str, str], float] = {}
        self._degrade_window_s = 60.0

    # ── 咨询 ─────────────────────────────────────────────────────────

    def retry_allowed(self, session_id: str, retry_class: RetryClass, *,
                      operation: str = "", degrade_taken: bool = False,
                      ) -> Tuple[bool, str]:
        """该 (session, class) 是否还允许再试一次（不扣 token）。"""
        with self._lock:
            self._gc_degraded()
            sess = self._sessions.get(session_id)
            if sess is not None and sess.cancelled:
                return False, DENY_CANCELLED
            key = (session_id, operation or retry_class.value)
            if key in self._degraded:
                # 同窗内 retry 与 fallback 不双发（D8）
                return False, DENY_DEGRADE_TAKEN
            if degrade_taken:
                self._degraded[key] = time.monotonic()
                return False, DENY_DEGRADE_TAKEN
            if self._global_left <= 0:
                return False, DENY_GLOBAL_EXHAUSTED
            if sess is None:
                return True, ALLOW
            if sess.tokens <= 0:
                return False, DENY_SESSION_EXHAUSTED
            return True, ALLOW

    def note_degrade(self, session_id: str, operation: str) -> None:
        """记录一次降级/fallback（同窗内 retry 被拒 —— 互斥）。"""
        with self._lock:
            self._gc_degraded()
            self._degraded[(session_id, operation)] = time.monotonic()

    # ── 记账 ─────────────────────────────────────────────────────────

    def charge(self, session_id: str, retry_class: RetryClass) -> None:
        """实扣一次重试（retry_allowed=True 之后由重试执行方调用）。"""
        with self._lock:
            sess = self._sessions.setdefault(
                session_id, _SessionTokens(tokens=self._session_tokens))
            if not sess.cancelled:
                sess.tokens = max(0.0, sess.tokens - 1)
                self._global_left = max(0.0, self._global_left - 1)
            sess.charges[retry_class.value] = (
                sess.charges.get(retry_class.value, 0) + 1)
            sess.last_charge_at = time.monotonic()
        observe_retry(retry_class.value, "charged")

    # ── 生命周期 ─────────────────────────────────────────────────────

    def cancel_session(self, session_id: str) -> None:
        """取消：token 清零 + cancelled 标记（retry 停止的硬保证）。

        对从未记过账的会话同样生效（建档即取消）—— 否则「未重试过的
        会话」取消后仍可无限重试，硬保证失效。
        """
        with self._lock:
            sess = self._sessions.setdefault(
                session_id, _SessionTokens(tokens=0.0))
            sess.tokens = 0.0
            sess.cancelled = True

    def close_session(self, session_id: str) -> None:
        """会话关闭：账目摘除（有界性）。"""
        with self._lock:
            self._sessions.pop(session_id, None)

    def refund_global(self, amount: int = 1) -> None:
        """全局池回填（校准/管理面用；绝不超初始容量）。"""
        with self._lock:
            self._global_left = min(
                float(self._global_tokens), self._global_left + amount)

    # ── 观测 ─────────────────────────────────────────────────────────

    def snapshot(self) -> Dict:
        with self._lock:
            return {
                "global_left": self._global_left,
                "global_capacity": self._global_tokens,
                "sessions": {
                    sid: {
                        "tokens": s.tokens,
                        "cancelled": s.cancelled,
                        "charges": dict(s.charges),
                    }
                    for sid, s in self._sessions.items()
                },
            }

    # ── 内部 ─────────────────────────────────────────────────────────

    def _gc_degraded(self) -> None:
        cutoff = time.monotonic() - self._degrade_window_s
        stale = [k for k, t in self._degraded.items() if t < cutoff]
        for k in stale:
            self._degraded.pop(k, None)


__all__ = [
    "RetryBudget",
    "ALLOW",
    "DENY_GLOBAL_EXHAUSTED",
    "DENY_SESSION_EXHAUSTED",
    "DENY_CANCELLED",
    "DENY_DEGRADE_TAKEN",
]
