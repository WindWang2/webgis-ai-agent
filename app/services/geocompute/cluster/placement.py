"""GeoCompute V7 资源感知放置（wave 6-7，01-architecture.md §2.2）。

**诚实三层语义**（架构 round1 #3 CRITICAL 修订）—— Celery 共享队列消费
模型下「消息 → 特定 worker」不可强制绑定，因此：

1. **run 级准入 gating（强制）**：``eligible_workers`` 为空 → run 留队
   （+ waiting_resource 事件，scheduler 接线）。这是硬承诺；
2. **worker 侧准入守卫（强制 + 有界收敛）**：节点级 gpu/min_mem 校验在
   ``run_geocompute_node`` 内（capability.satisfies）—— 不满足有界重投后
   类型化 PLACEMENT_MISMATCH；
3. **rank（advisory）**：局部性/过配只影响统计投影与容量判断，不承诺绑定。

全部纯内存计算：输入 = live worker 投影（含 capability/profiles）+ run 的
ResourceRequest；O(candidates × workers)，两侧均有界（batch ≤32、
workers ≤256，超界由调用方截断计数）。
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.geocompute.cluster.capabilities import (
    WorkerCapabilityProfile,
    capability_from_row,
)
from app.services.geocompute.cluster.contracts import ResourceRequest

#: live worker 数防御上限（注册表被刷爆时的截断线；截断在 metrics 可见）。
MAX_PLACEMENT_WORKERS = 256


def request_from_run_row(row: dict[str, Any]) -> ResourceRequest:
    """run 行投影 → ResourceRequest（缺席/损坏 = V6 语义：只看 profiles）。"""
    raw = row.get("resource_request")
    if not isinstance(raw, dict):
        return ResourceRequest(
            required_profiles=list(row.get("required_profiles") or []),
        )
    try:
        req = ResourceRequest(**{
            k: v for k, v in raw.items() if k in ResourceRequest.model_fields
        })
    except Exception:  # noqa: BLE001 - 脏数据按缺席处理（诚实降级）
        req = ResourceRequest()
    profiles = set(req.required_profiles) | set(row.get("required_profiles") or [])
    req.required_profiles = sorted(profiles)
    return req


def eligible_workers(
    request: ResourceRequest,
    workers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """run 级准入 gating（强制层）。

    合格 = **队列消费真相（profiles）覆盖** ∧ capability 满足 envelope。
    覆盖判断绝不用 capability.capabilities 放宽 —— 那是「软件可用性」
    （round1 C2：raster 后端存在但不消费 raster_queue 的 worker 会通过
    检查 → 消息进无人消费的队列 → 必然 WORKER_LOSS）。capability 缺席
    （旧 worker / 损坏行）→ 只按 profiles 匹配（V6 兼容语义，fail-open）。
    """
    covered = set(request.required_profiles)
    eligible: list[dict[str, Any]] = []
    for w in workers[:MAX_PLACEMENT_WORKERS]:
        profiles = set((w.get("profiles") or {}).keys())
        if covered and not covered.issubset(profiles):
            continue
        profile = _parsed_capability(w)
        if profile is not None and not profile.satisfies(
            min_cpu=request.min_cpu,
            min_mem_mb=request.min_mem_mb,
            gpu=request.gpu,
        ):
            continue
        eligible.append(w)
    return eligible


def _parsed_capability(w: dict[str, Any]) -> Optional[WorkerCapabilityProfile]:
    """per-call 解析缓存：同一 worker dict 在 gating+rank 各阶段只解析一次
    （round2 Rm1 —— 最坏 32×256 次重复 pydantic 校验/tick）。"""
    cached = w.get("_parsed_cap")
    if cached is not None or "_cap_parsed" in w:
        return cached
    w["_cap_parsed"] = True
    w["_parsed_cap"] = cached = capability_from_row(w.get("capability"))
    return cached


def rank_workers(
    request: ResourceRequest,
    eligible: list[dict[str, Any]],
    *,
    owner_scope: Optional[str] = None,
    node_locality_keys: Optional[frozenset[str]] = None,
    locality_lookup: Optional[Any] = None,
    last_dispatch: Optional[dict[str, int]] = None,
) -> list[dict[str, Any]]:
    """advisory 排序（局部性 > zone 契合 > 资源过配小 > 公平垫底）。

    诚实边界（round2 RM2）：共享队列模型下生产 dispatch 不消费本排序
    （无绑定语义）—— 供放置策略扩展与统计投影使用；ADR-0119 D1 的
    强制层只有 run 级 gating 与 worker 侧守卫。

    ``locality_lookup(worker_id, owner_scope, keys) -> int``：由调用方注入
    （WorkerCacheRegistry.worker_holds 的绑定）—— 本模块不做 IO 依赖，
    缺席 = 局部性项为 0（纯 capability/公平排序）。zone（round1 m3）：
    request.zone 声明时偏好同 zone worker（advisory，不是过滤）。
    """
    keys = node_locality_keys or frozenset()

    def _score(w: dict[str, Any]) -> tuple[int, int, int, int, str]:
        locality = 0
        if keys and owner_scope and locality_lookup is not None:
            locality = locality_lookup(w.get("worker_id", ""), owner_scope, keys)
        zone_match = 0
        if request.zone:
            profile = _parsed_capability(w)
            if profile is not None and profile.zone == request.zone:
                zone_match = 1
        profile = _parsed_capability(w)
        if profile is None:
            overprovision = 0
        else:
            # 过配 = worker 远大于请求（挑选最贴合的，减少资源碎片）
            overprovision = (
                (max(0, profile.cpu_cores - request.min_cpu))
                + max(0, profile.mem_mb - request.min_mem_mb) // 1024
                + max(0, profile.gpu_count - request.gpu) * 8
            )
        fairness = int((last_dispatch or {}).get(w.get("worker_id", ""), 0))
        return (-locality, -zone_match, overprovision, fairness,
                str(w.get("worker_id", "")))

    return sorted(eligible[:MAX_PLACEMENT_WORKERS], key=_score)


def request_digest(req: ResourceRequest) -> dict[str, Any]:
    """ResourceRequest → DB 列投影（有界；≤1KB 钳制在写入侧）。"""
    return {
        "min_mem_mb": req.min_mem_mb,
        "min_cpu": req.min_cpu,
        "gpu": req.gpu,
        "zone": req.zone,
        "required_profiles": list(req.required_profiles),
    }
