"""层级资源治理（ADR-0096 D6）：有界准入 + 记账，防单查询拖垮进程。

目标 §11 的最小忠实实现：
- 层级作用域 global → tenant → project → session → execution → node，
  请求在 **所有祖先作用域** 上同时记账/受限（charge 沿链上传，admit
  沿链下查）；
- 限额是公开常数/显式配置（rows/bytes/nodes/concurrency），不是计费系统；
- 超限 → 类型化 ``BudgetExceededError``，details 指明冒限的作用域并附
  可行动建议；
- 线程安全（节点并发记账）；不做跨进程汇总（与 ADR-0094:273 同一取舍，
  Deferred 里写明）。
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.geocompute.errors import BudgetExceededError


class ScopeKind(str, Enum):
    GLOBAL = "global"
    TENANT = "tenant"
    PROJECT = "project"
    SESSION = "session"
    EXECUTION = "execution"
    NODE = "node"


class BudgetLimits(BaseModel):
    """作用域限额（None = 该维不在此作用域设限）。"""

    max_rows: Optional[int] = Field(default=None, ge=1)
    max_bytes: Optional[int] = Field(default=None, ge=1)
    max_nodes: Optional[int] = Field(default=None, ge=1)


class ScopeUsage(BaseModel):
    rows: int = 0
    bytes: int = 0
    nodes: int = 0


class _Scope:
    """内部作用域节点（限额 + 计数 + 锁沿单作用域；汇总读走树遍历）。"""

    def __init__(self, kind: ScopeKind, scope_id: str, limits: Optional[BudgetLimits]):
        self.kind = kind
        self.scope_id = scope_id
        self.limits = limits
        self.usage = ScopeUsage()
        self.children: Dict[str, "_Scope"] = []
        self.lock = threading.Lock()

    @property
    def path(self) -> str:
        return f"{self.kind.value}:{self.scope_id}"


class ResourceGovernor:
    """层级预算树。根节点默认全局作用域（可配限额）。"""

    def __init__(self, global_limits: Optional[BudgetLimits] = None):
        self._root = _Scope(ScopeKind.GLOBAL, "root", global_limits)
        self._lock = threading.Lock()

    def ensure_scope(
        self,
        parent_path: str,
        kind: ScopeKind,
        scope_id: str,
        limits: Optional[BudgetLimits] = None,
    ) -> str:
        """幂等获取或创建子作用域（session 级挂载用；id 由调用方稳定派生）。"""
        existing = self._find(f"{parent_path}/{kind.value}:{scope_id}")
        if existing is not None:
            return f"{parent_path}/{kind.value}:{scope_id}"
        return self.create_scope(parent_path, kind, scope_id, limits)

    def teardown_scope(self, path: str) -> None:
        """从父作用域摘除 execution 作用域（防 GLOBAL_GOVERNOR 子树无限增长）。

        已发生的用量保留在祖先链上（记账不回滚）；仅移除树节点本身。
        """
        if "/" not in path:
            return
        parent_path, _, _ = path.rpartition("/")
        parent = self._find(parent_path) if parent_path else self._root
        if parent is None:
            return
        with self._lock:
            parent.children = [
                c for c in parent.children
                if f"{parent.path}/{c.path}" != path and c.path != path
            ]

    def create_scope(
        self,
        parent_path: str,
        kind: ScopeKind,
        scope_id: str,
        limits: Optional[BudgetLimits] = None,
    ) -> str:
        """在父作用域下创建子作用域，返回完整路径（``a:b`` 链）。

        路径格式：``global:root/session:s-1/execution:gexec-x``。
        """
        parent = self._find(parent_path)
        if parent is None:
            raise BudgetExceededError(
                f"budget scope '{parent_path}' does not exist",
                suggestions=["create the parent scope first"],
            )
        child = _Scope(kind, scope_id, limits)
        with self._lock:
            parent.children.append(child)
        return f"{parent_path}/{kind.value}:{scope_id}"

    def reserve(
        self,
        path: str,
        *,
        rows: int = 0,
        bytes_: int = 0,
        nodes: int = 0,
    ) -> None:
        """原子预留（评审 M2：admit→charge TOCTOU 的修复）。

        沿链 root→leaf 逐作用域「检查并立即记账」（固定顺序，无死锁）；
        链中途拒绝时对已记账的祖先做补偿回滚 —— 检查与记账之间不再留
        TOCTOU 窗口。
        """
        charged: List[_Scope] = []
        try:
            for scope in self._chain(path):
                with scope.lock:
                    lim = scope.limits
                    u = scope.usage
                    if lim is not None:
                        over: List[str] = []
                        if lim.max_rows is not None and u.rows + rows > lim.max_rows:
                            over.append(f"rows {u.rows}+{rows} > {lim.max_rows}")
                        if lim.max_bytes is not None and u.bytes + bytes_ > lim.max_bytes:
                            over.append(f"bytes {u.bytes}+{bytes_} > {lim.max_bytes}")
                        if lim.max_nodes is not None and u.nodes + nodes > lim.max_nodes:
                            over.append(f"nodes {u.nodes}+{nodes} > {lim.max_nodes}")
                        if over:
                            raise BudgetExceededError(
                                f"admission denied at scope '{scope.path}': "
                                + "; ".join(over),
                                suggestions=[
                                    "narrow the query extent or add filters",
                                    "aggregate per-source before transfer",
                                    "raise the scope budget explicitly for approved heavy paths",
                                ],
                                details={"scope": scope.path, "over": over},
                            )
                    u.rows += rows
                    u.bytes += bytes_
                    u.nodes += nodes
                    charged.append(scope)
        except BudgetExceededError:
            for scope in charged:
                with scope.lock:
                    scope.usage.rows -= rows
                    scope.usage.bytes -= bytes_
                    scope.usage.nodes -= nodes
            raise

    def charge(
        self,
        path: str,
        *,
        rows: int = 0,
        bytes: int = 0,
        nodes: int = 0,
    ) -> None:
        """沿链记账（全部祖先同时累加）。先 admit 后 charge。"""
        for scope in self._chain(path):
            with scope.lock:
                scope.usage.rows += rows
                scope.usage.bytes += bytes
                scope.usage.nodes += nodes

    def usage(self, path: str) -> Tuple[int, int, int]:
        """链末作用域的累计用量（诊断/证据用）。"""
        scope = self._find(path)
        if scope is None:
            return (0, 0, 0)
        with scope.lock:
            return (scope.usage.rows, scope.usage.bytes, scope.usage.nodes)

    # ── 内部 ─────────────────────────────────────────────────────────

    def _chain(self, path: str) -> List[_Scope]:
        """按路径解析作用域链；未知段按无限额透传（容忍并发创建次序）。"""
        scopes: List[_Scope] = []
        cur: Optional[_Scope] = self._root
        scopes.append(cur)
        parts = [p for p in path.split("/") if p and p != "global:root"]
        for part in parts:
            nxt = None
            if cur is not None:
                for child in cur.children:
                    if child.path == part:
                        nxt = child
                        break
            cur = nxt
            if cur is None:
                break
            scopes.append(cur)
        return scopes

    def _find(self, path: str) -> Optional[_Scope]:
        """精确解析：路径的每一段都必须命中真实作用域，否则 None。"""
        cur: Optional[_Scope] = self._root
        parts = [p for p in path.split("/") if p and p != "global:root"]
        for part in parts:
            nxt = None
            if cur is not None:
                for child in cur.children:
                    if child.path == part:
                        nxt = child
                        break
            if nxt is None:
                return None
            cur = nxt
        return cur


GLOBAL_GOVERNOR = ResourceGovernor()
