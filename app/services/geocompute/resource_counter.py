"""跨进程资源聚合计数器（Wave 8 / audit 07 R2）—— advisory 层，默认关闭。

诚实边界（先读这段再用）：
- **默认关闭**：仅当 ``REDIS_URL`` 可解析（env 优先，回退 settings）**且**
  env ``WEBGIS_CROSS_PROCESS_GOVERNOR=1`` 时才发起任何 Redis 交互；
  关闭时 reserve/increase/release 全部为 no-op（零 Redis 调用、零客户端
  构造）。与 ``distributed_lock`` 不同，本层**不**要求 ``USE_REDIS`` ——
  它是纯 advisory 叠加层，宁可少纠缠一个开关（差异在此显式声明）。
- **advisory 而非权威**：L1 进程内 governor（``budgets.ResourceGovernor``）
  始终是权威真相。本层只在聚合投影用量**确信**超过链上最具约束限额时
  建议拒绝，判定依据经 ``BudgetExceededError.details["cross_process"]``
  诚实披露（counter 读数 / 限额 / 超出维度）。
- **fail-open + 有界退避**：Redis 故障/超时 → 记录一段退避（默认 30s，
  期间直接放行且不发任何 Redis 命令），退避到期后重新探测 —— 永不把
  「不可用」锁存成永久决定（与 ``distributed_lock.SessionLockRegistry``
  的 60s 重验、``cache_broadcast`` 的 30s 退避同一纪律）。
- **崩溃对账**：所有计数键带 TTL（默认 1h；进程内 run deadline 不透传
  到本层，故取 1h 下限而非 max(2×deadline, 1h) —— 后者需要把 deadline
  穿透 governor 公共 API，当前不值）。INCRBY 后、EXPIRE 前崩溃会留下
  无 TTL 键 —— 写路径每次探测 TTL<0 即删除重建（stale-sweep，宁可丢弃
  不可信读数）。进程在预留后、释放前崩溃 → 计数虚高直到 TTL 过期。
- **残余漂移（known drift，如实披露）**：
  (a) 上述崩溃窗口的虚高最多存活一个 TTL；
  (b) DECRBY 后负值的钳制（DECRBY + 读负置 0）非原子，与并发 INCRBY
      存在竞态窗口，可能吞掉并发 pod 刚写入的少量计数（方向：放大
      容量）；
  (c) Redis 故障期间的增量/减量全部丢失 —— 计数低于真相（方向：放大
      容量）；
  (d) 多 pod 并发预留的检查存在竞态 —— 都读到同一投影后同时放行。
  结论：本层在自身故障时**多放行**，绝不**多拒绝**；这正是它只能做
  advisory、不能替代 L1 准入的原因。
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: 跨进程治理总开关（默认关）：须显式 env ``WEBGIS_CROSS_PROCESS_GOVERNOR=1``。
CROSS_PROCESS_FLAG_ENV = "WEBGIS_CROSS_PROCESS_GOVERNOR"

#: Redis 故障后的重探测退避（有界；永不锁存「不可用」）。
DEFAULT_BACKOFF_S = 30.0

#: 计数键 TTL（崩溃残留的存活上界；每次 reserve/increase 滑动续租）。
DEFAULT_ENTRY_TTL_S = 3600.0

_KEY_PREFIX = "webgis:geocompute:governor:v1"


def _cross_process_flag_on() -> bool:
    return os.getenv(CROSS_PROCESS_FLAG_ENV, "").strip() == "1"


def _configured_redis_url() -> str:
    """REDIS_URL 解析：env 优先（支持运行期改写），回退 Settings 单一事实源。"""
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    try:
        from app.core.config import settings as _settings

        return str(getattr(_settings, "REDIS_URL", "") or "").strip()
    except Exception:  # noqa: BLE001 - settings 不可用 → 视为未配置（fail-open）
        return ""


@dataclass
class CrossProcessDecision:
    """advisory 准入判定。

    ``allowed=False`` 时 ``details`` 携带诚实证据（``scope`` / ``over`` /
    ``counter`` 读数），由调用方原样放进 ``BudgetExceededError.details``。
    """

    allowed: bool
    details: Dict[str, Any] = field(default_factory=dict)


_ALLOWED = CrossProcessDecision(True, {"layer": "cross_process_advisory"})


class CrossProcessCounter:
    """Redis INCRBY/DECRBY 聚合计数（每稳定作用域链键 × 维度一个键）。

    ``client`` 可注入（测试用 fakeredis / 嵌入方自定义实现 get/set/incrby/
    decrby/ttl/expire/delete 接口即可）；未注入时按 env 动态解析 —— 每次
    操作重新评估 ``enabled``，运行期改 env 立即生效（与 distributed_lock
    的「不缓存不可用/未配置决定」同一取向）。线程安全：客户端惰性创建
    受锁保护；退避状态为标量读写。
    """

    def __init__(
        self,
        client: Optional[Any] = None,
        *,
        key_prefix: str = _KEY_PREFIX,
        backoff_s: float = DEFAULT_BACKOFF_S,
        entry_ttl_s: float = DEFAULT_ENTRY_TTL_S,
    ):
        self._injected = client
        self._client = client
        self._client_lock = threading.Lock()
        self._key_prefix = key_prefix
        self._backoff_s = max(0.0, float(backoff_s))
        self._entry_ttl_s = max(1.0, float(entry_ttl_s))
        self._down_until = 0.0

    # ------------------------------------------------------------ public

    @property
    def enabled(self) -> bool:
        """注入客户端 → 恒可用；否则 env 动态评估（默认 False = 零交互）。"""
        if self._injected is not None:
            return True
        return bool(_configured_redis_url()) and _cross_process_flag_on()

    def key_for(self, chain_key: str, dim: str) -> str:
        """稳定链键 → 计数键（哈希派生：键名不携带原始作用域标识）。"""
        digest = hashlib.sha1(
            chain_key.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:16]
        return f"{self._key_prefix}:{digest}:{dim}"

    def reserve(
        self,
        chain_key: str,
        *,
        rows: int = 0,
        bytes_: int = 0,
        nodes: int = 0,
        limits: Optional[Dict[str, Optional[int]]] = None,
    ) -> CrossProcessDecision:
        """advisory 预留：INCRBY 后检查聚合投影用量。

        仅当「本次增量后的投影读数 > 链上最具约束限额」才拒绝（并回滚
        自己的增量）；无该维限额 / Redis 不可用 / 未启用 → 一律放行。
        """
        deltas = self._deltas(rows, bytes_, nodes)
        if not deltas or not self.enabled:
            return _ALLOWED
        client = self._get_client()
        if client is None:
            return _ALLOWED  # 有界退避内 / 客户端不可用：fail-open
        ttl = int(self._entry_ttl_s)
        counted: Dict[str, int] = {}
        try:
            for dim, delta in deltas.items():
                counted[dim] = self._incr_with_stale_sweep(
                    client, self.key_for(chain_key, dim), delta, ttl,
                )
        except Exception as exc:  # noqa: BLE001 - fail-open（advisory 层）
            self._mark_down(exc)
            return _ALLOWED
        self._mark_up()
        caps = limits or {}
        over = [
            f"{dim} {value} > {cap}"
            for dim, value in counted.items()
            if (cap := caps.get(dim)) is not None and value > cap
        ]
        if not over:
            return CrossProcessDecision(
                True, {"layer": "cross_process_advisory", "counter": counted}
            )
        try:
            self.release(chain_key, rows=rows, bytes_=bytes_, nodes=nodes)
        except Exception:  # noqa: BLE001 - 回滚尽力而为；TTL 兜底残留
            pass
        return CrossProcessDecision(
            False,
            {"scope": chain_key, "over": over, "counter": counted},
        )

    def increase(self, chain_key: str, *, rows: int = 0, bytes_: int = 0,
                 nodes: int = 0) -> None:
        """记账方向 INCRBY（与 governor.charge 配对；故障静默降级）。"""
        deltas = self._deltas(rows, bytes_, nodes)
        if not deltas or not self.enabled:
            return
        client = self._get_client()
        if client is None:
            return
        ttl = int(self._entry_ttl_s)
        try:
            for dim, delta in deltas.items():
                self._incr_with_stale_sweep(
                    client, self.key_for(chain_key, dim), delta, ttl,
                )
        except Exception as exc:  # noqa: BLE001 - fail-open
            self._mark_down(exc)

    def release(self, chain_key: str, *, rows: int = 0, bytes_: int = 0,
                nodes: int = 0) -> None:
        """归还方向 DECRBY（与 reserve/increase 配对；负值钳 0 —— 见模块
        docstring 残余漂移 (b) 的诚实竞态声明）。"""
        deltas = self._deltas(rows, bytes_, nodes)
        if not deltas or not self.enabled:
            return
        client = self._get_client()
        if client is None:
            return
        ttl = int(self._entry_ttl_s)
        try:
            for dim, delta in deltas.items():
                key = self.key_for(chain_key, dim)
                value = int(client.decrby(key, delta))
                client.expire(key, ttl)
                if value < 0:
                    client.set(key, 0, ex=ttl)
        except Exception as exc:  # noqa: BLE001 - fail-open（漂移由 TTL 兜底）
            self._mark_down(exc)

    # ------------------------------------------------------------ internal

    @staticmethod
    def _deltas(rows: int, bytes_: int, nodes: int) -> Dict[str, int]:
        out: Dict[str, int] = {}
        if rows:
            out["rows"] = int(rows)
        if bytes_:
            out["bytes"] = int(bytes_)
        if nodes:
            out["nodes"] = int(nodes)
        return out

    def _incr_with_stale_sweep(
        self, client: Any, key: str, delta: int, ttl: int
    ) -> int:
        """INCRBY + TTL 滑动续租；读到无 TTL 键（INCRBY 后 EXPIRE 前崩溃的
        残留）→ 不可信，删除重建（读数归零重来，宁可少信不多信）。"""
        value = int(client.incrby(key, delta))
        remaining = int(client.ttl(key))
        if remaining < 0:
            client.delete(key)
            value = int(client.incrby(key, delta))
        client.expire(key, ttl)
        return value

    def _get_client(self) -> Optional[Any]:
        """退避优先（注入客户端同样受退避约束），再惰性创建同步客户端。"""
        if time.monotonic() < self._down_until:
            return None
        if self._injected is not None:
            return self._injected
        with self._client_lock:
            if self._client is not None:
                return self._client
            if not self.enabled:
                return None
            try:
                import redis  # 惰性导入：关闭时不付成本

                self._client = redis.Redis.from_url(
                    _configured_redis_url(),
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0,
                )
                logger.info(
                    "CrossProcessCounter: Redis-backed advisory governor enabled"
                )
            except Exception as exc:  # noqa: BLE001
                self._client = None
                self._mark_down(exc)
            return self._client

    def _mark_down(self, exc: Exception) -> None:
        """有界退避：期间 fail-open 且零 Redis 命令；到期自动重探测。"""
        self._down_until = time.monotonic() + self._backoff_s
        logger.warning(
            "CrossProcessCounter: Redis unavailable, failing open for %.0fs: %s",
            self._backoff_s, exc,
        )

    def _mark_up(self) -> None:
        self._down_until = 0.0


#: 进程级共享单例。enabled 在每次操作时动态评估 env —— 默认（无
#: WEBGIS_CROSS_PROCESS_GOVERNOR=1）零 Redis 交互。
_SHARED_COUNTER = CrossProcessCounter()


def shared_counter() -> CrossProcessCounter:
    """进程级共享 advisory 计数器（供 ``api.GOVERNOR`` 装配；默认关闭）。"""
    return _SHARED_COUNTER
