"""层级资源治理（ADR-0096 D6）：有界准入 + 记账，防单查询拖垮进程。

目标 §11 的最小忠实实现：
- 层级作用域 global → tenant → project → session → execution → node，
  请求在 **所有祖先作用域** 上同时记账/受限（charge 沿链上传，admit
  沿链下查）；
- 限额是公开常数/显式配置（rows/bytes/nodes/concurrency），不是计费系统；
- 超限 → 类型化 ``BudgetExceededError``，details 指明冒限的作用域并附
  可行动建议；
- 树有界：execution 作用域逐 run 创建/摘除，身份作用域（tenant/project/
  session）按挂载引用计数回收（``ensure_scope``/``release_scope``）；
- 线程安全（节点并发记账）；跨进程汇总默认不做（与 ADR-0094:273 同一
  取舍）—— Wave 8（audit 07 R2）起提供**可选的** advisory 层
  ``resource_counter.CrossProcessCounter``（默认关闭、fail-open；L1
  进程内树始终是权威真相，见 ``ResourceGovernor.cross_process``）。
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.geocompute.errors import BudgetExceededError


class ScopeKind(str, Enum):
    """作用域层级。

    Wave 8（audit 07 §1.1/R7）决策记录：曾经的 ``NODE`` 成员已删除 ——
    它从未被实例化（全仓库无 ``create_scope`` 调用方），按节点建 scope
    只剩 governor 级锁开销与子树增长（节点记账本就沿链上报到全部祖先
    作用域），保留成员只会造成「声明未接线」的假面。若未来需要节点级
    限额，重新加入成员并在派发点实例化。
    """

    GLOBAL = "global"
    TENANT = "tenant"
    PROJECT = "project"
    SESSION = "session"
    EXECUTION = "execution"


class BudgetLimits(BaseModel):
    """作用域限额（None = 该维不在此作用域设限）。

    ``max_concurrency``（ADR-0101 D3/D10）：该作用域链上同时占用的
    execution 槽位上界 —— 调度器的 weighted admission 以它为背压源。
    """

    max_rows: Optional[int] = Field(default=None, ge=1)
    max_bytes: Optional[int] = Field(default=None, ge=1)
    max_nodes: Optional[int] = Field(default=None, ge=1)
    max_concurrency: Optional[int] = Field(default=None, ge=1)


class ScopeUsage(BaseModel):
    rows: int = 0
    bytes: int = 0
    nodes: int = 0
    concurrency: int = 0


class _Scope:
    """内部作用域节点（限额 + 计数 + 锁沿单作用域；汇总读走树遍历）。"""

    def __init__(self, kind: ScopeKind, scope_id: str, limits: Optional[BudgetLimits]):
        self.kind = kind
        self.scope_id = scope_id
        self.limits = limits
        self.usage = ScopeUsage()
        self.children: List["_Scope"] = []
        #: 子作用域索引（子 path → 子作用域）：``_chain``/``_find`` 的 O(1)
        #: 查找。逐节点记账每段都要解析一次链，children 线性扫描会把单次
        #: 记账变成 O(兄弟数)（#1745 1）。``children`` 列表保留供遍历/诊断。
        self.by_path: Dict[str, "_Scope"] = {}
        #: 身份作用域（tenant/project/session）的活跃挂载数：
        #: ``ensure_scope`` 每次挂载 +1（幂等，重复挂载即重复计数）、
        #: ``create_scope`` 建时置 1（创建者持有）、``release_scope`` -1。
        #: 归零且无后代、记账归零后随链回收 —— 身份作用域此前只建不拆，
        #: 长命进程下随历史 org/project/session 数无界累积（#1745 1）。
        #: execution 作用域由 executor 的 ``teardown_scope`` 摘除，不参与。
        self.refs = 0
        self.lock = threading.Lock()

    @property
    def path(self) -> str:
        return f"{self.kind.value}:{self.scope_id}"


class ResourceGovernor:
    """层级预算树。根节点默认全局作用域（可配限额）。

    ``cross_process``（可选，duck-typed
    ``resource_counter.CrossProcessCounter``）：Wave 8（audit 07 R2）的
    **advisory** 跨进程聚合计数器。给定后 ``reserve``/``charge``/``release``
    会在稳定祖先链键（去掉逐 run 唯一的 EXECUTION 段）上做 INCRBY/DECRBY，
    且 reserve 时做尽力准入检查 —— 只在投影用量**确信**超过链上最具约束
    限额时拒绝；Redis 故障一律放行（fail-open）。``None``（默认）= 纯
    进程内，零 Redis 交互；L1 进程内树在任何配置下都是权威真相。
    """

    def __init__(
        self,
        global_limits: Optional[BudgetLimits] = None,
        *,
        cross_process: Optional[Any] = None,
    ):
        self._root = _Scope(ScopeKind.GLOBAL, "root", global_limits)
        self._lock = threading.Lock()
        self._cross_process = cross_process

    def ensure_scope(
        self,
        parent_path: str,
        kind: ScopeKind,
        scope_id: str,
        limits: Optional[BudgetLimits] = None,
    ) -> str:
        """幂等获取或创建子作用域（session 级挂载用；id 由调用方稳定派生）。

        ADR-0101 D10：find-then-create 在 governor 级锁内完成 —— 并发首次
        挂载不再可能产生重复子作用域。

        #1745 1：身份作用域的每次挂载计一份引用（``refs += 1``）—— 调用方
        必须以 :meth:`release_scope` 配对归还，否则该作用域永不被回收
        （fail-safe 方向：宁可留下节点，不可拆掉在飞 run 的记账链）。
        """
        target = f"{parent_path}/{kind.value}:{scope_id}"
        with self._lock:
            existing = self._find(target)
            if existing is not None:
                existing.refs += 1
                return target
            parent = self._find(parent_path)
            if parent is None:
                raise BudgetExceededError(
                    f"budget scope '{parent_path}' does not exist",
                    suggestions=["create the parent scope first"],
                )
            child = _Scope(kind, scope_id, limits)
            child.refs = 1
            parent.children.append(child)
            parent.by_path[child.path] = child
            return target

    def release_scope(self, path: str) -> None:
        """归还 :meth:`ensure_scope` 的挂载引用，并回收链上闲置的身份作用域。

        与 ``ensure_scope`` 严格配对（``api.run_plan_sync`` 的 finally）：
        自 ``path`` 向上逐段 ``refs -= 1``，随后自叶向根摘除「引用归零 +
        无后代执行作用域 + 记账归零」的身份作用域（tenant/project/session）
        —— 它们此前创建后无拆除路径，进程内随历史身份数单调增长
        （#1745 1）。execution 作用域仍归 executor 的 ``teardown_scope``
        管，本方法不触碰；记账非零（归还泄漏的残留）的作用域不回收 ——
        那是可见的故障信号，不是静默丢账。
        """
        chain = self._chain(path)
        if len(chain) <= 1:
            return
        with self._lock:
            for scope in chain:
                scope.refs = max(0, scope.refs - 1)
            self._reclaim_chain(chain)

    def _reclaim_chain(self, chain: List[_Scope]) -> None:
        """自叶向根回收链上可回收的身份作用域（调用方持 governor 级锁）。

        本层不可回收时上层必留后代，直接停。链快照可能取自加锁前（并发
        归还/重新挂载）：只摘「父里仍挂着这一个对象」的情况 —— 防止把并发
        新建的同 path 子作用域从索引里误删。
        """
        for idx in range(len(chain) - 1, 0, -1):
            scope = chain[idx]
            if not self._reclaimable(scope):
                break
            parent = chain[idx - 1]
            if parent.by_path.get(scope.path) is scope:
                del parent.by_path[scope.path]
                if scope in parent.children:
                    parent.children.remove(scope)

    def _reclaimable(self, scope: _Scope) -> bool:
        """身份作用域可否回收（调用方持 governor 级锁）。"""
        if scope.kind is ScopeKind.EXECUTION:
            return False
        if scope.refs or scope.children:
            return False
        usage = scope.usage
        return not (usage.rows or usage.bytes or usage.nodes or usage.concurrency)

    def teardown_scope(self, path: str) -> None:
        """从父作用域摘除 execution 作用域（防 GLOBAL_GOVERNOR 子树无限增长）。

        已发生的用量保留在祖先链上（记账不回滚）；仅移除树节点本身。

        身份作用域（tenant/project/session）不在此拆除 —— 同会话可能仍有
        其他 run 在飞，就地拆链会把它们的记账接到一棵新树上；它们的回收
        走 :meth:`release_scope` 的引用计数（#1745 1）。
        """
        if "/" not in path:
            return
        parent_path, _, leaf = path.rpartition("/")
        with self._lock:
            parent = self._find(parent_path) if parent_path else self._root
            if parent is None:
                return
            child = parent.by_path.pop(leaf, None)
            if child is None:
                return
            parent.children.remove(child)

    def create_scope(
        self,
        parent_path: str,
        kind: ScopeKind,
        scope_id: str,
        limits: Optional[BudgetLimits] = None,
    ) -> str:
        """在父作用域下创建子作用域，返回完整路径（``a:b`` 链）。

        路径格式：``global:root/session:s-1/execution:gexec-x``。

        #1745 1：创建者持一份引用（``refs = 1``）—— 身份作用域需显式
        ``release_scope`` 归还后才可能被回收；execution 作用域由 executor
        的 ``teardown_scope`` 摘除，引用不参与其生命周期。
        """
        with self._lock:
            parent = self._find(parent_path)
            if parent is None:
                raise BudgetExceededError(
                    f"budget scope '{parent_path}' does not exist",
                    suggestions=["create the parent scope first"],
                )
            child = _Scope(kind, scope_id, limits)
            child.refs = 1
            parent.children.append(child)
            parent.by_path[child.path] = child
        return f"{parent_path}/{kind.value}:{scope_id}"

    def reserve(
        self,
        path: str,
        *,
        rows: int = 0,
        bytes_: int = 0,
        nodes: int = 0,
        concurrency: int = 0,
    ) -> None:
        """原子预留（评审 M2：admit→charge TOCTOU 的修复）。

        沿链 root→leaf 逐作用域「检查并立即记账」（固定顺序，无死锁）；
        链中途拒绝时对已记账的祖先做补偿回滚 —— 检查与记账之间不再留
        TOCTOU 窗口。``concurrency`` 是调度槽位预留（ADR-0101 D3）：
        节点派发前占用、落定后由 :meth:`release` 释放。

        Wave 8 R2：装配了跨进程计数器且本调用含 rows/bytes/nodes 增量时，
        先在其稳定链键上做 advisory 预留（确信超限 → 拒绝，details 诚实
        披露判定依据）；随后的链式预留若被拒，对计数器做补偿回滚保持
        两侧一致。纯 concurrency 预留不触达计数器（并发槽位是进程内
        调度概念）。
        """
        counter = self._cross_process
        counter_engaged = counter is not None and bool(rows or bytes_ or nodes)
        chain_key = self._stable_chain_key(path) if counter_engaged else ""
        if counter_engaged:
            decision = counter.reserve(
                chain_key,
                rows=rows,
                bytes_=bytes_,
                nodes=nodes,
                limits=self._chain_caps(chain_key),
            )
            if not decision.allowed:
                raise BudgetExceededError(
                    "cross-process admission denied (advisory): "
                    + "; ".join(decision.details.get("over", [])),
                    suggestions=[
                        "retry after in-flight runs drain (usage is a gauge)",
                        "narrow the query extent or add filters",
                        "raise the scope budget explicitly for approved heavy paths",
                    ],
                    details={"scope": chain_key, "cross_process": decision.details},
                )

        def _apply(scope: _Scope, sign: int) -> None:
            # 调用方负责持锁（+1 路径在下方 with scope.lock 内；补偿路径
            # 在 except 分支逐个持锁）—— threading.Lock 不可重入。
            scope.usage.rows += sign * rows
            scope.usage.bytes += sign * bytes_
            scope.usage.nodes += sign * nodes
            scope.usage.concurrency += sign * concurrency

        charged: List[_Scope] = []
        try:
            for scope in self._chain(path):
                with scope.lock:
                    lim = scope.limits
                    u = scope.usage
                    if lim is not None:
                        over: List[str] = []
                        # 评审 CRITICAL 修正：只检查本次**有增量**的维度 ——
                        # 零增量维度不再参与上限判定（否则计划级估计预留 +
                        # 逐节点实际记账的双计一旦触顶，后续纯 concurrency
                        # 预留会被 rows 检查永久拒绝 → 调度器假性饿死）。
                        if rows and lim.max_rows is not None and u.rows + rows > lim.max_rows:
                            over.append(f"rows {u.rows}+{rows} > {lim.max_rows}")
                        if bytes_ and lim.max_bytes is not None and u.bytes + bytes_ > lim.max_bytes:
                            over.append(f"bytes {u.bytes}+{bytes_} > {lim.max_bytes}")
                        if nodes and lim.max_nodes is not None and u.nodes + nodes > lim.max_nodes:
                            over.append(f"nodes {u.nodes}+{nodes} > {lim.max_nodes}")
                        if concurrency and lim.max_concurrency is not None and (
                            u.concurrency + concurrency > lim.max_concurrency
                        ):
                            over.append(
                                f"concurrency {u.concurrency}+{concurrency} > "
                                f"{lim.max_concurrency}"
                            )
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
                    _apply(scope, +1)
                    charged.append(scope)
        except BudgetExceededError:
            for scope in charged:
                with scope.lock:
                    _apply(scope, -1)
            if counter_engaged:
                try:
                    counter.release(chain_key, rows=rows, bytes_=bytes_, nodes=nodes)
                except Exception:  # noqa: BLE001 - advisory 层补偿尽力而为
                    pass
            raise

    def release(
        self,
        path: str,
        *,
        rows: int = 0,
        bytes_: int = 0,
        nodes: int = 0,
        concurrency: int = 0,
    ) -> None:
        """沿链归还预留（无上限检查；compensation 方向）。

        调度器在节点落定（completed/failed/cancelled/skipped）后释放其
        concurrency 槽位 —— 与 ``reserve(concurrency=…)`` 严格配对
        （Wave 8 R7：单位数与预留时一致）。
        下限钳制在 0（评审 MINOR：重复 release 不得把用量记负、变相
        抬高其他作用域的可用容量）。

        Wave 8 R1：执行器在 run 收尾按「预留估计 + 实际记账」全额归还，
        rows/bytes/nodes 由此保持**并发在飞量衡**语义 —— 长寿命祖先
        作用域精确回到 run 前基本线（钳零是重复归还的兜底）。

        Wave 8 R2：装配跨进程计数器时同步 DECRBY（TTL 兜底崩溃残留）；
        计数器故障绝不倒灌本归还（L1 权威）。
        """
        if self._cross_process is not None and bool(rows or bytes_ or nodes):
            try:
                self._cross_process.release(
                    self._stable_chain_key(path),
                    rows=rows,
                    bytes_=bytes_,
                    nodes=nodes,
                )
            except Exception:  # noqa: BLE001 - advisory 层绝不阻断 L1 归还
                pass
        for scope in self._chain(path):
            with scope.lock:
                u = scope.usage
                u.rows = max(0, u.rows - rows)
                u.bytes = max(0, u.bytes - bytes_)
                u.nodes = max(0, u.nodes - nodes)
                u.concurrency = max(0, u.concurrency - concurrency)

    def charge(
        self,
        path: str,
        *,
        rows: int = 0,
        bytes: int = 0,
        nodes: int = 0,
    ) -> None:
        """沿链记账（全部祖先同时累加）。先 admit 后 charge。

        Wave 8 R2：装配跨进程计数器时同步 INCRBY（与 :meth:`release` 的
        DECRBY 配对；键上 TTL 兜底崩溃残留）。
        """
        if self._cross_process is not None and bool(rows or bytes or nodes):
            try:
                self._cross_process.increase(
                    self._stable_chain_key(path),
                    rows=rows,
                    bytes_=bytes,
                    nodes=nodes,
                )
            except Exception:  # noqa: BLE001 - advisory 层绝不阻断 L1 记账
                pass
        for scope in self._chain(path):
            with scope.lock:
                scope.usage.rows += rows
                scope.usage.bytes += bytes
                scope.usage.nodes += nodes

    def usage(self, path: str) -> Tuple[int, int, int]:
        """链末作用域的累计用量（诊断/证据用；兼容三元组形状）。"""
        scope = self._find(path)
        if scope is None:
            return (0, 0, 0)
        with scope.lock:
            return (scope.usage.rows, scope.usage.bytes, scope.usage.nodes)

    def usage_full(self, path: str) -> Optional[ScopeUsage]:
        """链末作用域的完整用量（含 concurrency 槽位；诊断/证据用）。"""
        scope = self._find(path)
        if scope is None:
            return None
        with scope.lock:
            return scope.usage.model_copy()

    def limits_for(self, path: str) -> Optional["BudgetLimits"]:
        """公开只读：精确解析作用域，返回其限额（未知路径 → None）。

        外部设施（如 data_fabric 的 ``batch_size_for``）经此读取限额，
        不再触达私有 ``_find``（Wave 5：私有 API 耦合的修复）。
        """
        scope = self._find(path)
        return None if scope is None else scope.limits

    # ── 内部 ─────────────────────────────────────────────────────────

    def _stable_chain_key(self, path: str) -> str:
        """跨进程计数键：去掉尾部 EXECUTION 段后的稳定祖先链。

        execution 作用域逐 run 唯一（uuid 后缀），不能作为跨进程聚合键；
        去掉它之后同一 (tenant/…)session 的所有 run 聚合到同一键。
        """
        scopes = self._chain(path)
        while len(scopes) > 1 and scopes[-1].kind is ScopeKind.EXECUTION:
            scopes.pop()
        return "/".join(scope.path for scope in scopes)

    def _chain_caps(self, chain_key: str) -> Dict[str, Optional[int]]:
        """稳定链上各维度的最具约束限额（None = 该维不限；advisory 检查用）。"""
        caps: Dict[str, Optional[int]] = {"rows": None, "bytes": None, "nodes": None}
        for scope in self._chain(chain_key):
            lim = scope.limits
            if lim is None:
                continue
            for dim in caps:
                cap = getattr(lim, f"max_{dim}")
                if cap is not None and (caps[dim] is None or cap < caps[dim]):
                    caps[dim] = cap
        return caps

    def _chain(self, path: str) -> List[_Scope]:
        """按路径解析作用域链；未知段按无限额透传（容忍并发创建次序）。

        段查找走 ``by_path`` 索引（O(1)）—— 每次节点记账都要解析一次链，
        线性扫 ``children`` 会让记账成本随兄弟作用域数增长（#1745 1）。
        """
        scopes: List[_Scope] = [self._root]
        cur: Optional[_Scope] = self._root
        for part in (p for p in path.split("/") if p and p != "global:root"):
            nxt = cur.by_path.get(part) if cur is not None else None
            if nxt is None:
                break
            scopes.append(nxt)
            cur = nxt
        return scopes

    def _find(self, path: str) -> Optional[_Scope]:
        """精确解析：路径的每一段都必须命中真实作用域，否则 None。"""
        cur: Optional[_Scope] = self._root
        for part in (p for p in path.split("/") if p and p != "global:root"):
            if cur is None:
                return None
            cur = cur.by_path.get(part)
        return cur


GLOBAL_GOVERNOR = ResourceGovernor()
