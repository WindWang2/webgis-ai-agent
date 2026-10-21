"""#1745（1）：governor 身份作用域的生命周期 + 链解析成本。

两个缺陷：
- tenant/project/session 作用域创建后无拆除路径（``teardown_scope`` 的
  docstring 自承只摘 execution 作用域）—— 长命进程下 ``GLOBAL_GOVERNOR``
  子树随历史 org/project/session 数无界累积；
- ``_chain`` 每一段都线性扫 ``cur.children`` —— 每次节点记账都对会话子表
  做一次线性扫描，成本随兄弟作用域数增长。

修复：身份作用域按挂载引用计数回收（``ensure_scope`` +1 /
``release_scope`` -1，归零且无后代、记账归零即随链摘除），段查找走
``by_path`` 索引。本文件锁住「run 结束后树回到空」「同会话多 run 共享
作用域」「在飞 run 不被回收」三条不变量。
"""
from __future__ import annotations

from typing import Iterator, List

from app.services.geocompute import (
    ExecutionNode,
    ExecutionPlan,
    NodeCategory,
    ResourceBudget,
)
from app.services.geocompute.api import run_plan_sync
from app.services.geocompute.budgets import (
    BudgetLimits,
    ResourceGovernor,
    ScopeKind,
)


def _plan(node_id: str = "n1") -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="scope-lifecycle",
        nodes=[ExecutionNode(
            node_id=node_id, category=NodeCategory.FILTER,
            parameters={"predicate": {"op": "eq", "field": "kind", "value": "a"},
                        "features": [
                            {"type": "Feature", "geometry": None,
                             "properties": {"kind": "a", "v": i}}
                            for i in range(3)
                        ]},
        )],
        budget=ResourceBudget(max_rows=1000),
    )


def _walk(scope) -> Iterator[object]:
    yield scope
    for child in scope.children:
        yield from _walk(child)


def _paths(gov: ResourceGovernor) -> List[str]:
    return [s.path for s in _walk(gov._root)]  # noqa: SLF001 - 树形状即契约


def _mount_chain(gov: ResourceGovernor, org: str = "org-a",
                 project: str = "proj-a", session: str = "sess-a") -> str:
    """按生产挂载顺序建 tenant → project → session，返回最深作用域路径。"""
    parent = gov.ensure_scope(
        "global:root", ScopeKind.TENANT, org,
        limits=BudgetLimits(max_concurrency=8),
    )
    parent = gov.ensure_scope(
        parent, ScopeKind.PROJECT, project,
        limits=BudgetLimits(max_concurrency=4),
    )
    return gov.ensure_scope(
        parent, ScopeKind.SESSION, session,
        limits=BudgetLimits(max_concurrency=4),
    )


class TestIdentityScopeLifecycle:
    def test_run_reclaims_identity_scopes(self):
        """run 收尾后 tenant/project/session 全部回收（树回到空）。"""
        gov = ResourceGovernor()
        run = run_plan_sync(
            _plan(), session_id="sess-life", project_id="proj-life",
            caller={"user_id": "u-life", "org_id": "org-life"}, governor=gov,
        )
        assert run.status.value == "completed"
        assert gov._root.children == []  # noqa: SLF001 - 无界累积的回归锁
        assert _paths(gov) == ["global:root"]

    def test_sequential_runs_reuse_then_reclaim(self):
        """同会话两个连续 run：作用域复用（不重复建），末个 run 后回收。"""
        gov = ResourceGovernor()
        for i in range(2):
            run = run_plan_sync(
                _plan(f"n-seq{i}"), session_id="sess-seq", governor=gov,
            )
            assert run.status.value == "completed"
        assert gov._root.children == []  # noqa: SLF001

    def test_shared_mount_survives_until_last_release(self):
        """同作用域多次挂载：任一次归还都不回收，最后一次归还才回收。"""
        gov = ResourceGovernor()
        path = _mount_chain(gov)
        gov.ensure_scope(path.rsplit("/", 1)[0], ScopeKind.SESSION, "sess-a")
        gov.release_scope(path)
        assert any(s.path.startswith("session:") for s in _walk(gov._root)), \
            "仍有其他 run 挂载时会话作用域必须在位"
        gov.release_scope(path)
        assert gov._root.children == []  # noqa: SLF001

    def test_inflight_execution_scope_blocks_reclaim(self):
        """在飞 execution 作用域（后代）阻止身份作用域被回收。"""
        gov = ResourceGovernor()
        session_path = _mount_chain(gov)
        exec_path = gov.create_scope(
            session_path, ScopeKind.EXECUTION, "gexec-inflight",
            limits=BudgetLimits(max_concurrency=2),
        )
        gov.reserve(exec_path, nodes=1, concurrency=1)
        gov.release_scope(session_path)
        kept = [s for s in _walk(gov._root) if s.path.startswith("session:")]
        assert kept, "在飞 run 的记账链不能被回收"
        # 收尾顺序与 executor 一致：先归还占用，再摘 execution 作用域。
        gov.release(exec_path, nodes=1, concurrency=1)
        gov.teardown_scope(exec_path)
        gov.release_scope(session_path)
        assert gov._root.children == []  # noqa: SLF001

    def test_residual_usage_blocks_reclaim(self):
        """记账未归零（归还泄漏）的作用域不回收 —— 故障可见，不静默丢账。"""
        gov = ResourceGovernor()
        session_path = _mount_chain(gov)
        gov.charge(session_path, rows=5)
        gov.release_scope(session_path)
        kept = [s for s in _walk(gov._root) if s.path.startswith("session:")]
        assert kept, "残留记账的作用域必须保留（泄漏信号）"

    def test_release_scope_unknown_path_is_noop(self):
        """对已回收/不存在路径归还：不抛、不误摘其他作用域。"""
        gov = ResourceGovernor()
        gov.release_scope("global:root/session:nope")
        assert gov._root.children == []  # noqa: SLF001

    def test_root_never_reclaimed(self):
        """根作用域永不被回收（链上第一段）。"""
        gov = ResourceGovernor()
        session_path = _mount_chain(gov)
        gov.release_scope(session_path)
        assert gov._find("global:root") is gov._root  # noqa: SLF001

    def test_stale_chain_snapshot_does_not_detach_recreated_scope(self):
        """过期链快照（并发归还 + 重新挂载）不得摘掉同 path 的新作用域。"""
        gov = ResourceGovernor()
        session_path = _mount_chain(gov)
        stale = gov._chain(session_path)
        gov.release_scope(session_path)          # 归还 → 整链回收
        assert gov._root.children == []  # noqa: SLF001
        fresh_path = _mount_chain(gov)           # 同 id 重新挂载 → 新对象
        fresh_session = gov._find(fresh_path)
        assert fresh_session is not None and fresh_session is not stale[-1]
        # 拿过期链再走一遍回收逻辑：新作用域必须完好（索引与列表一致）。
        gov._reclaim_chain(stale)  # noqa: SLF001 - 并发快照的模拟
        assert gov._find(fresh_path) is fresh_session  # noqa: SLF001
        root = gov._root  # noqa: SLF001
        tenant = root.by_path["tenant:org-a"]  # noqa: SLF001
        assert {c.path for c in tenant.children} == set(tenant.by_path), (
            "children 列表与 by_path 索引必须同步"
        )


class TestChainLookup:
    def test_child_index_mirrors_children_list(self):
        """``by_path`` 索引与 ``children`` 列表同步（段查找走索引的契约）。"""
        gov = ResourceGovernor()
        for i in range(5):
            gov.create_scope("global:root", ScopeKind.SESSION, f"s{i}")
        root = gov._root  # noqa: SLF001
        assert {c.path for c in root.children} == set(root.by_path)
        assert len(root.by_path) == 5

    def test_deep_chain_resolves_with_many_siblings(self):
        """兄弟作用域很多时，链解析仍精确命中（索引查找，不是扫描）。"""
        gov = ResourceGovernor()
        for i in range(200):
            gov.create_scope("global:root", ScopeKind.SESSION, f"s{i:03d}")
        target = "global:root/session:s199"
        gov.charge(target, rows=7)
        assert gov.usage(target) == (7, 0, 0)
        assert gov.usage("global:root") == (7, 0, 0)
        assert gov._find(target) is not None  # noqa: SLF001
        assert gov._find("global:root/session:s999") is None  # noqa: SLF001

    def test_teardown_removes_only_target_child(self):
        """execution 摘除只摘自己，兄弟与父作用域不受影响。"""
        gov = ResourceGovernor()
        gov.create_scope("global:root", ScopeKind.SESSION, "keep")
        victim = gov.create_scope("global:root", ScopeKind.SESSION, "drop")
        gov.teardown_scope(victim)
        assert {c.path for c in gov._root.children} == {"session:keep"}  # noqa: SLF001
        assert gov._find(victim) is None  # noqa: SLF001
