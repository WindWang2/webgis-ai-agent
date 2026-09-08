"""GeoCompute V6 公平调度（wave 8）：加权轮转 + 突发共享 + 防饿死。

设计（01-architecture.md）：
- **可重建状态**：轮转依据 = ``MAX(dispatch_seq) GROUP BY tenant``（store
  提供）—— coordinator 崩溃/换主后公平状态从 DB 重建，无隐藏内存态；
- **starvation-free**：同容量下轮转按「租户最近派发序」交错 —— 两个租户
  竞争 1 个槽位时必然交替；等待时间不改变次序（不靠 aging 补丁）；
- **突发共享**：轮转只约束选取次序、不约束总量 —— 空租户的份额被其余
  租户自然借用的同时，轮转位仍保留（新租户立刻进入交错序列）；
- **确定性**：同输入同输出（测试可重放）；priority 只在租户内部排序生效
  （跨租户公平优先 —— 抢占语义另行处理）。
"""
from __future__ import annotations

from typing import Any, Optional


def fair_pick(
    candidates: list[dict[str, Any]],
    *,
    slots: int,
    last_dispatch: Optional[dict[str, int]] = None,
) -> list[dict[str, Any]]:
    """从候选中按加权轮转选取至多 ``slots`` 个。

    ``candidates``：store.scan_dispatchable 投影（含 id/tenant_key/priority）。
    ``last_dispatch``：tenant_key → 最近派发序（缺省视为 -1，新租户最先）。

    算法：租户队列内部按 (-priority, id) 排序；租户按 (最近派发序, 键名)
    排序成**固定环**，游标逐格推进、空队列跳过但游标不移除 —— 经典 DRR
    写法（round1 M5：重算 remaining + 取模会让租户队列中途耗尽时产生
    系统性跳位偏袒）。同一轮内确定性成立。
    """
    slots = max(0, int(slots))
    if slots == 0 or not candidates:
        return []
    last = last_dispatch or {}
    by_tenant: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(
        candidates, key=lambda r: (-int(r.get("priority") or 0), int(r.get("id") or 0))
    ):
        by_tenant.setdefault(row.get("tenant_key") or "", []).append(row)
    tenant_cycle = sorted(
        by_tenant, key=lambda t: (last.get(t, -1), t)
    )
    picked: list[dict[str, Any]] = []
    cursor = 0
    while len(picked) < slots:
        remaining_any = any(by_tenant[t] for t in tenant_cycle)
        if not remaining_any:
            break
        tenant = tenant_cycle[cursor % len(tenant_cycle)]
        cursor += 1  # 空队列也推进游标（跳过但不移位 —— 无漂移）
        if by_tenant[tenant]:
            picked.append(by_tenant[tenant].pop(0))
    return picked


def matches_profiles(
    required: Optional[list[str]], available: Optional[frozenset[str]]
) -> bool:
    """通道能力匹配（wave 7）：run 需要的 profile 是否有存活通道。

    ``available=None`` 表示「本进程可执行一切 profile」（eager 模式 /
    coordinator 直跑语义）—— 永远匹配。
    """
    if not required or available is None:
        return True
    return set(required) <= set(available)
