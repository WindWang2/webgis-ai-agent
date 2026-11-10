"""同代能力可用性快照缓存（G13 / WP03，ADR-0287 成本段的兑现）。

``compile_availability_snapshot()`` 每次要跑全注册表 8 面对账 + 目录编译
（ADR-0287 实测 ~10s 量级）。G06 阶段它只被离线 CLI 调用，无需缓存；
G13 把它接进生产热路径（候选检索 / 执行前复核），因此这里提供**同代
进程级缓存**：

设计纪律
--------
1. **只缓存结构事实**。缓存键 = 注册表指纹 + 目录代 + 扩展宿主状态摘要 +
   认证摘要 + 声明面（能力/算法注册表）代。owner / tenant / 权限 / 临时
   凭据是**请求侧事实**，绝不进入缓存键或缓存值（消费经
   ``query_availability(permissions=...)`` 逐请求投影）。
2. **命中零编译**。热路径只做 O(1) 键读取（registry_fingerprint 自带
   memo；宿主摘要读公开 status_report 的稳定字段）。
3. **单飞重建**。重建在 ``_build_lock`` 内进行：同代 N 个并发读者 =
   恰好 1 次编译，其余在双检后命中；**旧代构建晚到不得覆盖新代**
   （完成时校验当前键仍与构建键一致才落缓存）。构建锁只串行化重建，
   不触碰命中路径（命中走 ``_cache_lock`` 微秒级）。
4. **失败诚实**。构建失败保留 last-good（不冒充新代），带最小重试间隔
   防失败风暴；进程关闭时清理。冷启动尚无完整验证时，消费方拿到的就是
   快照里真实的 ``available_unverified / not_evaluated`` 状态。
5. **变更通知丢失兜底**：键本身由真实指纹构成，每次读取都廉价比对；
   通知丢失最多延迟到下一次读取，不会永久用旧快照。

调用环境：生产消费方（``compute_turn_active_tools`` / dispatch 复核）经
``asyncio.to_thread`` 进入本模块 —— 重建阻塞的是工作线程，前台事件循环
永不被全量对账卡住。不新增后台线程：重建只发生在请求路径或显式失效。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from app.lib.gis.capability_availability import (
    CapabilityAvailabilitySnapshot,
    compile_availability_snapshot,
)

logger = logging.getLogger(__name__)

#: kill switch：GIS_CAPABILITY_AVAILABILITY_CACHE=0 关闭缓存（每次直编）。
_CACHE_ENV = "GIS_CAPABILITY_AVAILABILITY_CACHE"
#: 构建失败后的最小重试间隔（秒）—— 同代失败不做立即风暴重试。
_MIN_RETRY_INTERVAL_S = 5.0


def _cache_enabled() -> bool:
    raw = (os.getenv(_CACHE_ENV) or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


class AvailabilitySnapshotBackoff(RuntimeError):
    """退避窗口内的同代构建失败（R2 P2-1/P2-2）。

    语义：证据缺席 —— 调用方（selector / dispatch 复核 / 发现面）按
    「零贡献」降级：不抑制候选、不拒绝调用、不披露可用性。**不是**
    旧代数据，也绝不在窗口内反复跑全量对账。
    """


@dataclass
class _CacheEntry:
    key: str
    snapshot: CapabilityAvailabilitySnapshot
    created_monotonic: float


class _SnapshotCache:
    """进程级单例缓存（线程安全；Pi 面经 asyncio.to_thread 进入）。"""

    def __init__(self) -> None:
        # 命中路径锁：只保护 entry/计数读写（微秒级，绝不嵌套编译）。
        self._cache_lock = threading.Lock()
        # 重建锁：串行化全量编译（同代并发 = 1 次编译；双检消除重复）。
        self._build_lock = threading.Lock()
        self._entry: Optional[_CacheEntry] = None
        # 上次构建失败 (键, monotonic) —— 同键退避窗口内服务 last-good。
        self._last_failure: Optional[Tuple[str, float]] = None
        # 诊断计数（有界整数，无内存增长）。
        self.stats: dict[str, int] = {
            "hits": 0, "misses": 0, "builds": 0,
            "build_failures": 0, "coalesced_builds": 0,
            "stale_overwrites_prevented": 0, "backoff_suppressed": 0,
        }

    # ── 结构键：全部 O(1)/轻量，绝无全量对账 ───────────────────────
    @staticmethod
    def _structural_key(registry: Any = None, *, bootstrap: bool = False) -> str:
        from app.lib.gis.capability_availability import (
            _certification_digest, _extension_state_digest,
        )
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.capability_registry import get_capability_registry

        parts: list[str] = []
        # 工具注册面指纹（新增/改 schema 立即换代；自带 memo）。
        if registry is not None:
            try:
                parts.append(f"reg:{registry.registry_fingerprint()}")
            except Exception:  # noqa: BLE001 — 注入 registry 无指纹时诚实降级
                parts.append("reg:unknown")
        else:
            from app.agent_pi_bridge import get_tool_registry

            try:
                parts.append(f"reg:{get_tool_registry().registry_fingerprint()}")
            except Exception:  # noqa: BLE001
                parts.append("reg:unavailable")
        # 目录代（get_execution_catalog 单例缓存；这里只取指纹不重编译）。
        try:
            from app.lib.gis.execution_catalog import get_execution_catalog

            parts.append(f"cat:{get_execution_catalog().generation_fingerprint}")
        except Exception:  # noqa: BLE001
            parts.append("cat:unavailable")
        # 声明面代：能力/算法注册表（不可变种子；计数 + ids 摘要兜底，
        # 防御测试内 load_builtins 之外的动态 register 路径）。
        try:
            parts.append(f"caps:{len(get_capability_registry().all_ids)}")
        except Exception:  # noqa: BLE001
            parts.append("caps:unavailable")
        try:
            algo_reg = get_algorithm_registry()
            ids_digest = hash(tuple(algo_reg.all_ids)) & 0xFFFFFFFF
            parts.append(f"algos:{algo_reg.count}:{ids_digest}")
        except Exception:  # noqa: BLE001
            parts.append("algos:unavailable")
        # 扩展宿主公开状态摘要（停用/激活/撤权/换版必变）。
        parts.append(f"host:{_extension_state_digest()}")
        # 认证索引摘要（快照编译入参的默认空索引稳定为 ""）。
        parts.append(f"cert:{_certification_digest(None)}")
        # R2 P3-2：bootstrap 标志进键 —— bootstrap 值来自全新构建的注册表
        # 实例，与全局 registry 代可以瞬态一致；同键不同源会导致错配。
        parts.append(f"boot:{int(bool(bootstrap))}")
        return "|".join(parts)

    def _lookup(
        self, key: str, *, count_miss: bool = True,
    ) -> tuple[Optional[CapabilityAvailabilitySnapshot], bool]:
        """命中路径：读 entry / 退避窗口判定 / 计数。

        返回 ``(snapshot_or_None, suppressed_by_backoff)``：

        - 同代 entry 命中 → ``(snapshot, False)``；
        - 退避窗口内同键构建刚失败（R2 P2-1/P2-2 修复）→ ``(None, True)``：
          **既不重试**秒级全量对账（冷进程失败风暴），**也不服务旧代
          last-good**（结构漂移后的新代构建失败，旧代状态对刚撤权/停用
          的工具可能已失真 —— 跨代数据不得冒充）。调用方按
          「证据缺席 = 零贡献」降级；窗口过后首次读取真实重试。
        """
        with self._cache_lock:
            entry = self._entry
            if entry is not None and entry.key == key:
                self.stats["hits"] += 1
                return entry.snapshot, False
            failed_key, failed_at = self._last_failure or (None, 0.0)
            if (
                failed_key == key
                and (time.monotonic() - failed_at) < _MIN_RETRY_INTERVAL_S
            ):
                self.stats["backoff_suppressed"] += 1
                return None, True
            if count_miss:
                self.stats["misses"] += 1
            return None, False

    def get(
        self,
        *,
        registry: Any = None,
        bootstrap: bool = False,
    ) -> CapabilityAvailabilitySnapshot:
        """取同代快照：命中直返；未命中/换代 → 单飞重建一次。

        退避窗口内的同键失败（无论有无 last-good）→ 抛
        :class:`AvailabilitySnapshotBackoff`（调用方按「证据缺席 = 零贡献」
        降级 —— selector 失败零贡献、dispatch 复核放行、发现面不披露，
        绝不以旧代冒充新代，也绝不在窗口内反复跑秒级全量对账）。
        """
        if not _cache_enabled():
            self.stats["misses"] += 1
            return compile_availability_snapshot(
                registry=registry, bootstrap=bootstrap)

        key = self._structural_key(registry, bootstrap=bootstrap)
        hit, backoff = self._lookup(key)
        if hit is not None:
            return hit
        if backoff:
            raise AvailabilitySnapshotBackoff(key)

        with self._build_lock:
            # 双检：等锁期间同代构建已完成 → 直接命中（合并的并发重建；
            # 双检的 miss 不重复计数）。
            hit, backoff = self._lookup(key, count_miss=False)
            if hit is not None:
                with self._cache_lock:
                    self.stats["coalesced_builds"] += 1
                return hit
            if backoff:
                raise AvailabilitySnapshotBackoff(key)
            try:
                snapshot = compile_availability_snapshot(
                    registry=registry, bootstrap=bootstrap)
            except Exception:
                with self._cache_lock:
                    self.stats["build_failures"] += 1
                    self._last_failure = (key, time.monotonic())
                logger.exception(
                    "[AvailabilityCache] snapshot build failed for "
                    "generation %r", key[:96])
                # 失败诚实上抛：调用方（selector/dispatch）按
                # 「证据缺席 = 零贡献」降级，绝不以缓存缺失为由崩溃 turn。
                raise
            with self._cache_lock:
                # 完成时校验当前键：等锁期间结构又漂移（更大概率是
                # 下一读者已重建更新代）则本次结果不落缓存，防止旧代
                # 覆盖新代。返回值本身仍是调用方请求代的真实快照。
                current_key = self._structural_key(registry, bootstrap=bootstrap)
                if current_key != key:
                    self.stats["stale_overwrites_prevented"] += 1
                    self.stats["builds"] += 1
                    return snapshot
                self._entry = _CacheEntry(
                    key=key, snapshot=snapshot,
                    created_monotonic=time.monotonic())
                self._last_failure = None
                self.stats["builds"] += 1
            return snapshot

    def invalidate(self) -> None:
        """显式失效（扩展停用/撤权钩子、测试用）。下次读取触发重建。"""
        with self._cache_lock:
            self._entry = None
            self._last_failure = None

    def clear_for_shutdown(self) -> None:
        with self._cache_lock:
            self._entry = None
            self._last_failure = None


_CACHE = _SnapshotCache()


def get_availability_snapshot(
    *,
    registry: Any = None,
    bootstrap: bool = False,
) -> CapabilityAvailabilitySnapshot:
    """生产入口：同代缓存快照（热路径命中零全量编译）。"""
    return _CACHE.get(registry=registry, bootstrap=bootstrap)


def invalidate_availability_snapshot() -> None:
    """显式失效（变更通知钩子；键漂移本身也会自然失效）。"""
    _CACHE.invalidate()


def availability_cache_stats() -> dict[str, int]:
    """只读诊断计数（有界整数）。"""
    with _CACHE._cache_lock:  # noqa: SLF001 — 诊断面单点
        return dict(_CACHE.stats)


def shutdown_availability_cache() -> None:
    """进程关闭清理（atexit 已挂；幂等）。"""
    _CACHE.clear_for_shutdown()


import atexit  # noqa: E402 — 模块尾注册，保持导入面干净

atexit.register(shutdown_availability_cache)
