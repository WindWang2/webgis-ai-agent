"""声明式性能预算（Platform V4，ADR-0131 D6）。

生产运行时的**观测预算**面：关键路径耗时对照 manifest 预算，超限打
结构化日志 + Prometheus breach counter（告警接缝），并进直方图。

与既有两处预算真相的关系（**不合并**，口径不同是文档化决策）：

- ``tests/benchmarks/baselines.json``：perf 车道持久基线（机器相对语义，
  PERF_UPDATE_BASELINES 再生成）；
- ``tests/fixtures/perf_budget.py``：测试内联断言助手（median+floor+ratio）；
- 本 manifest：**生产侧 SLO 语义**（用户可感知的路径预算，固定值不随
  机器缩放）。三者消费方不同、变更频率不同，合并反而制造第二真相源。

预算 key 是封闭词表：manifest 声明即注册；``observe_budget`` 拒绝未登记
key（与 sre_metrics 的封闭组件词表同纪律）。
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

#: manifest 默认位置（仓库根相对）
BUDGET_MANIFEST_PATH = Path("config") / "perf_budgets.json"

_PERF_BUDGET_OBSERVED = Histogram(
    "perf_budget_observed_seconds",
    "Observed duration of a budgeted key path.",
    ["budget"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

_PERF_BUDGET_BREACH = Counter(
    "perf_budget_breach_total",
    "Budget breaches observed (observed > limit).",
    ["budget"],
)


@dataclass(frozen=True)
class Budget:
    """一条路径预算（limit_s 是生产 SLO 语义的固定上界）。"""

    key: str
    limit_s: float
    description: str = ""
    component: str = ""


def load_manifest(path: Optional[Path] = None) -> Dict[str, Budget]:
    """读取预算 manifest（JSON → Budget 注册表；格式错误抛 ValueError）。"""
    manifest_path = Path(path) if path else _repo_root() / BUDGET_MANIFEST_PATH
    with open(manifest_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict) or not isinstance(raw.get("budgets"), list):
        raise ValueError("perf budget manifest must be {version, budgets: [...]}")
    budgets: Dict[str, Budget] = {}
    for entry in raw["budgets"]:
        key = str(entry.get("key", "")).strip()
        limit = entry.get("limit_s")
        if not key or not isinstance(limit, (int, float)) or limit <= 0:
            raise ValueError(f"invalid budget entry: {entry!r}")
        if key in budgets:
            raise ValueError(f"duplicate budget key: {key}")
        budgets[key] = Budget(
            key=key,
            limit_s=float(limit),
            description=str(entry.get("description", "")),
            component=str(entry.get("component", "")),
        )
    return budgets


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


class BudgetRegistry:
    """封闭 key 的预算注册表 + 观测面（线程安全）。"""

    def __init__(self, budgets: Optional[Dict[str, Budget]] = None):
        self._lock = threading.Lock()
        self._budgets: Dict[str, Budget] = dict(budgets or {})

    # ── 注册（测试/启动时）──────────────────────────────────────────

    def register(self, budget: Budget) -> None:
        with self._lock:
            if budget.key in self._budgets:
                raise ValueError(f"duplicate budget key: {budget.key}")
            self._budgets[budget.key] = budget

    def load_manifest(self, path: Optional[Path] = None) -> int:
        budgets = load_manifest(path)
        with self._lock:
            self._budgets.update(budgets)
        return len(budgets)

    # ── 查询 ────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[Budget]:
        with self._lock:
            return self._budgets.get(key)

    def keys(self) -> tuple:
        with self._lock:
            return tuple(sorted(self._budgets))

    # ── 观测 ────────────────────────────────────────────────────────

    def observe(self, key: str, seconds: float) -> Optional[Budget]:
        """记录一次路径耗时；超限返回触发的 Budget（未注册 key 返回 None
        并 debug 日志——观测面绝不抛、绝不阻断业务路径）。"""
        try:
            _PERF_BUDGET_OBSERVED.labels(budget=key).observe(max(0.0, float(seconds)))
        except Exception:  # noqa: BLE001
            logger.debug("perf budget histogram failed", exc_info=True)
        budget = self.get(key)
        if budget is None:
            logger.debug("perf budget %r not registered; observed only", key)
            return None
        if float(seconds) > budget.limit_s:
            try:
                _PERF_BUDGET_BREACH.labels(budget=key).inc()
            except Exception:  # noqa: BLE001
                logger.debug("perf breach counter failed", exc_info=True)
            logger.warning(
                "[perf-budget] breach budget=%s observed_s=%.3f limit_s=%.3f "
                "component=%s",
                key, float(seconds), budget.limit_s, budget.component or "-",
            )
            return budget
        return None


_default_registry: Optional[BudgetRegistry] = None
_default_lock = threading.Lock()


def get_budget_registry() -> BudgetRegistry:
    """进程级默认注册表（首次调用时加载仓库 manifest；缺失文件 → 空表）。"""
    global _default_registry
    with _default_lock:
        if _default_registry is None:
            registry = BudgetRegistry()
            try:
                registry.load_manifest()
            except FileNotFoundError:
                logger.info("perf budget manifest not found; starting empty")
            except ValueError:
                logger.warning("perf budget manifest invalid; starting empty",
                               exc_info=True)
            _default_registry = registry
        return _default_registry


def reset_budget_registry_for_tests() -> BudgetRegistry:
    global _default_registry
    with _default_lock:
        _default_registry = BudgetRegistry()
        return _default_registry


def observe_budget(key: str, seconds: float) -> Optional[Budget]:
    """便捷入口：往默认注册表观测一次耗时（返回触发的 Budget 或 None）。"""
    return get_budget_registry().observe(key, seconds)


__all__ = [
    "BUDGET_MANIFEST_PATH",
    "Budget",
    "BudgetRegistry",
    "load_manifest",
    "get_budget_registry",
    "reset_budget_registry_for_tests",
    "observe_budget",
]
