"""GIS Artifact Runtime — ADR-0082.

会话内工具产物的事实记录层（per-session runtime state）：

- ``ArtifactRegistry``：每个工具产物一条 ``ArtifactRecord``。artifact_id
  直接复用既有 ref 字符串（``ref:geojson-…``）—— ref 已经是会话内唯一、
  且是 SessionPlan 行 / MapSpec source / layer provenance 的既有指针，
  因此 ``bound_ref`` 天然是本 registry 的兼容投影，零 schema 迁移；
- ``ArtifactGraph``：由 records **派生**的纯函数图（producer/consumer/
  lineage/replacement）—— 派生投影，不落盘、不建第二份真相；
- 生命周期：``valid → superseded``（rebind/replan 替换）``→ stale``
  （ref 存活但无活引用）``→ expired``（store 探测缺失）。session reset
  由 session store 的会话清理连带清除（registry 本身就是 session 数据）。

单一事实源不变式（ADR-0076/0080/0082）：
- SessionPlan 行仍持 ``bound_ref`` 字符串指针（canonical plan truth 不变）；
- MapSpec source 仍持 ``ref``（map truth 不变）；
- registry 只**记录**这些指针的血缘/状态并回答依赖问题（"哪些产物依赖
  X"），绝不反向驱动行状态或 spec —— 无第二 mutable truth。

并发：所有变更走 per-session lock（与 SessionPlan apply 同锁序）；在
apply 锁内只收集意图、锁外执行注册（见 session_plan 集成），避免非重入
锁自锁。注册是增值记录：任何失败降级为日志，绝不阻断工具结果路径。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ── 契约常量 ─────────────────────────────────────────────────────────
MAX_ARTIFACT_RECORDS = 128  # 会话内记录上限（超出先淘汰 superseded，再按 LRU）
LEDGER_PREFIX = "artifact-ledger"
LEDGER_ALIAS = "artifacts"

# 生命周期状态（有限集合）
A_VALID = "valid"
A_STALE = "stale"
A_EXPIRED = "expired"
A_SUPERSEDED = "superseded"
A_FAILED = "failed"
_TERMINAL_GC_STATUSES = (A_SUPERSEDED, A_STALE, A_EXPIRED, A_FAILED)

# ref 前缀 → artifact_type 推断（dispatch/chart seam 无 capability 上下文时）
_PREFIX_TYPE_MAP = {
    "geojson": "feature_collection",
    "heatmap": "density_surface",
    "raster": "raster_surface",
    "chart": "chart_spec",
}


def infer_artifact_type(
    ref: str,
    *,
    result: Optional[Dict[str, Any]] = None,
    capability_outputs: Optional[Sequence[str]] = None,
) -> str:
    """artifact_type 推断：capability 输出类型 > result 形状 > ref 前缀。"""
    if capability_outputs:
        first = str(capability_outputs[0])
        if first:
            return first
    if isinstance(result, dict) and result.get("type") == "heatmap_raster":
        return "density_surface"
    ref = str(ref or "")
    body = ref[4:] if ref.startswith("ref:") else ref
    head = body.split("-", 1)[0].split("/", 1)[0]
    return _PREFIX_TYPE_MAP.get(head, "feature_collection")


@dataclass
class ArtifactRecord:
    """单条产物记录（bounded、serializable；artifact_id = ref 字符串）。"""

    artifact_id: str
    artifact_type: str = "feature_collection"
    session_id: str = ""
    producer_capability: str = ""
    producer_tool: str = ""
    producer_node: str = ""  # DAG 侧节点（capability 行键）
    inputs: List[str] = field(default_factory=list)  # 上游 artifact_id（lineage 边）
    replaces: Optional[str] = None  # 本产物替换掉的旧 artifact_id
    revision: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: Optional[float] = None  # store TTL 已知时填（best-effort）
    bbox: Optional[List[float]] = None
    crs: str = ""
    feature_count: Optional[int] = None
    row_count: Optional[int] = None
    empty: bool = False
    status: str = A_VALID
    storage_ref: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["inputs"] = list(self.inputs)[:16]
        d["metadata"] = {
            str(k): v for k, v in list(self.metadata.items())[:12]
        }
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ArtifactRecord":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


# ── 派生图（纯函数；不落盘）─────────────────────────────────────────


class ArtifactGraph:
    """由 records 派生的血缘图。

    edges: (producer_id, consumer_id) —— consumer.inputs 含 producer。
    answers: consumers(x) / producers(x) / lineage(x)（上游闭包）/
    dependents(x)（下游闭包）/ replacement_chain(x)。
    """

    def __init__(self, records: Dict[str, ArtifactRecord]):
        self.records = records
        self._consumers: Dict[str, List[str]] = {}
        for aid, rec in records.items():
            for dep in rec.inputs:
                if dep and dep in records:
                    self._consumers.setdefault(dep, []).append(aid)

    def consumers(self, artifact_id: str) -> List[str]:
        """直接消费 X 的产物（下游一层）。"""
        return list(self._consumers.get(artifact_id, []))

    def producers(self, artifact_id: str) -> List[str]:
        rec = self.records.get(artifact_id)
        return [d for d in (rec.inputs if rec else []) if d in self.records]

    def lineage(self, artifact_id: str) -> List[str]:
        """X 的全部上游（传递闭包，环安全）。"""
        seen: set[str] = set()
        stack = list(self.producers(artifact_id))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.producers(cur))
        return sorted(seen)

    def dependents(self, artifact_id: str) -> List[str]:
        """依赖 X 的全部下游（传递闭包，环安全）。"""
        seen: set[str] = set()
        stack = list(self.consumers(artifact_id))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.consumers(cur))
        return sorted(seen)

    def replacement_chain(self, artifact_id: str) -> List[str]:
        """X 被替换的链条：X → 替换者 → 替换者的替换者…（最新在尾部）。"""
        chain: List[str] = []
        replaced_by = {
            rec.replaces: aid for aid, rec in self.records.items() if rec.replaces
        }
        cur: Optional[str] = artifact_id
        while cur and cur in replaced_by and len(chain) <= MAX_ARTIFACT_RECORDS:
            cur = replaced_by[cur]
            chain.append(cur)
        return chain

    def latest_for_capability(self, capability: str) -> Optional[ArtifactRecord]:
        """某 capability 当前最新产物（replacement 链尾 / updated_at 最大）。"""
        cands = [
            r for r in self.records.values() if r.producer_capability == capability
        ]
        if not cands:
            return None
        live = [r for r in cands if r.status == A_VALID]
        pool = live or cands
        return max(pool, key=lambda r: (r.updated_at, r.created_at))


def build_artifact_graph(records: Dict[str, ArtifactRecord]) -> ArtifactGraph:
    return ArtifactGraph(records)


# ── Registry（per-session 记录层；alias 持久化同 SessionPlan 模式）────


async def _load_records(session_id: str, backend: Any) -> Dict[str, ArtifactRecord]:
    ref_id = await backend.resolve_alias(session_id, LEDGER_ALIAS)
    if ref_id == LEDGER_ALIAS:
        return {}
    data = await backend.get(session_id, ref_id)
    if not isinstance(data, dict):
        return {}
    raw = data.get("artifacts")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, ArtifactRecord] = {}
    for aid, rec in raw.items():
        if isinstance(rec, dict):
            try:
                out[str(aid)] = ArtifactRecord.from_dict(rec)
            except Exception:  # noqa: BLE001 — 单条坏记录跳过
                continue
    return out


async def _save_records(
    session_id: str, records: Dict[str, ArtifactRecord], backend: Any
) -> None:
    payload = {
        "session_id": session_id,
        "updated_at": time.time(),
        "artifacts": {aid: rec.to_dict() for aid, rec in records.items()},
    }
    ref_id = await backend.resolve_alias(session_id, LEDGER_ALIAS)
    if ref_id != LEDGER_ALIAS:
        if await backend.overwrite(session_id, ref_id, payload):
            return
    new_ref = await backend.store(session_id, payload, prefix=LEDGER_PREFIX)
    await backend.set_alias(session_id, new_ref, LEDGER_ALIAS)


def _evict(records: Dict[str, ArtifactRecord]) -> Dict[str, ArtifactRecord]:
    """有界化：先淘汰 GC 态，再按 updated_at LRU。"""
    if len(records) <= MAX_ARTIFACT_RECORDS:
        return records
    gc = [
        aid
        for aid, r in records.items()
        if r.status in _TERMINAL_GC_STATUSES
    ]
    gc.sort(key=lambda aid: records[aid].updated_at)
    overflow = len(records) - MAX_ARTIFACT_RECORDS
    for aid in gc[:overflow]:
        del records[aid]
    if len(records) > MAX_ARTIFACT_RECORDS:
        lru = sorted(records.items(), key=lambda kv: kv[1].updated_at)
        for aid, _ in lru[: len(records) - MAX_ARTIFACT_RECORDS]:
            del records[aid]
    return records


def _descriptor_fields(descriptor: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(descriptor, dict):
        return {}
    out: Dict[str, Any] = {}
    bbox = descriptor.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            out["bbox"] = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
        except (TypeError, ValueError):
            pass
    fc = descriptor.get("feature_count")
    if isinstance(fc, int):
        out["feature_count"] = fc
        out["empty"] = fc == 0
    crs = descriptor.get("crs")
    if isinstance(crs, str) and crs:
        out["crs"] = crs[:64]
    return out


def _default_ttl() -> Optional[float]:
    """Redis 后端的会话数据 TTL（best-effort；内存 LRU 后端无过期）。"""
    try:
        from app.services.session_data_redis import DATA_TTL

        return float(DATA_TTL)
    except Exception:  # noqa: BLE001
        return None


async def register_artifact(
    session_id: str,
    *,
    artifact_id: str,
    artifact_type: Optional[str] = None,
    producer_capability: str = "",
    producer_tool: str = "",
    producer_node: str = "",
    inputs: Optional[Sequence[str]] = None,
    descriptor: Optional[Dict[str, Any]] = None,
    revision: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
    lock: Any = None,
) -> Optional[ArtifactRecord]:
    """注册/更新一条产物记录（幂等 upsert；同 capability 换 ref 自动 supersede）。

    返回写入后的记录；失败（锁降级/存储异常）返回 None —— 注册是增值
    记录，绝不阻断工具路径。``lock``：调用方已持有 per-session lock 时
    透传复用（避免非重入自锁）；否则内部获取。
    """
    from app.services.distributed_lock import session_lock_registry

    if not session_id or not artifact_id:
        return None
    if lock is not None and getattr(lock, "lost", False):
        return None  # 锁已丢失：不再写共享状态
    now = time.time()
    ttl = _default_ttl()

    async def _mutate(backend: Any) -> Optional[ArtifactRecord]:
        records = await _load_records(session_id, backend)
        existing = records.get(artifact_id)
        rec = existing or ArtifactRecord(
            artifact_id=artifact_id,
            session_id=session_id,
            created_at=now,
            storage_ref=artifact_id,
        )
        rec.artifact_type = artifact_type or rec.artifact_type or infer_artifact_type(
            artifact_id
        )
        if producer_capability:
            rec.producer_capability = producer_capability
        if producer_tool:
            rec.producer_tool = producer_tool
        if producer_node:
            rec.producer_node = producer_node
        rec.inputs = [str(i) for i in (inputs or rec.inputs) if i][:16]
        rec.revision = int(revision or rec.revision)
        rec.updated_at = now
        if ttl:
            rec.expires_at = now + ttl
        for k, v in _descriptor_fields(descriptor).items():
            setattr(rec, k, v)
        if metadata:
            merged = dict(rec.metadata)
            merged.update(metadata)
            rec.metadata = merged
        rec.status = A_VALID  # 重注册（重试成功）复活记录
        # 同 capability 换 ref → 旧产物 superseded（replacement 链）
        if producer_capability:
            prev = None
            for aid, other in records.items():
                if (
                    aid != artifact_id
                    and other.producer_capability == producer_capability
                    and other.status == A_VALID
                ):
                    if prev is None or other.updated_at > records[prev].updated_at:
                        prev = aid
            if prev is not None:
                records[prev].status = A_SUPERSEDED
                records[prev].updated_at = now
                rec.replaces = prev
        records[artifact_id] = rec
        await _save_records(session_id, _evict(records), backend)
        return rec

    try:
        if lock is not None:
            from app.services.session_data import session_data_manager as sdm

            return await _mutate(sdm)
        async with session_lock_registry.lock(session_id, fail_on_degraded=False):
            from app.services.session_data import session_data_manager as sdm

            return await _mutate(sdm)
    except Exception:  # noqa: BLE001 — 注册失败只影响记录，不影响产物
        logger.warning(
            "[ArtifactRegistry] register failed session=%s artifact=%s",
            session_id, artifact_id,
        )
        return None


async def register_tool_artifact(
    session_id: str,
    ref: str,
    *,
    tool: str = "",
    result: Optional[Dict[str, Any]] = None,
) -> Optional[ArtifactRecord]:
    """dispatch/chart seam 的便捷注册（无 capability 上下文；type 由推断得出）。"""
    if not ref or not str(ref).startswith("ref:"):
        return None
    return await register_artifact(
        session_id,
        artifact_id=ref,
        artifact_type=infer_artifact_type(ref, result=result),
        producer_tool=tool,
        metadata={"seam": "dispatch"} if result is None else {
            "seam": "dispatch",
            "result_type": str(result.get("type") or "")[:32],
        },
    )


async def get_artifact(
    session_id: str, artifact_id: str
) -> Optional[ArtifactRecord]:
    from app.services.session_data import session_data_manager

    if not session_id or not artifact_id:
        return None
    try:
        records = await _load_records(session_id, session_data_manager)
        return records.get(artifact_id)
    except Exception:  # noqa: BLE001
        return None


async def list_artifacts(session_id: str) -> List[ArtifactRecord]:
    from app.services.session_data import session_data_manager

    try:
        records = await _load_records(session_id, session_data_manager)
        return sorted(records.values(), key=lambda r: r.updated_at)
    except Exception:  # noqa: BLE001
        return []


async def mark_status(session_id: str, artifact_id: str, status: str) -> bool:
    from app.services.distributed_lock import session_lock_registry
    from app.services.session_data import session_data_manager

    if not session_id or not artifact_id:
        return False
    try:
        async with session_lock_registry.lock(session_id, fail_on_degraded=False):
            records = await _load_records(session_id, session_data_manager)
            rec = records.get(artifact_id)
            if rec is None:
                return False
            rec.status = status
            rec.updated_at = time.time()
            await _save_records(session_id, records, session_data_manager)
            return True
    except Exception:  # noqa: BLE001
        return False


async def load_records_for_plan(
    session_id: str,
    chapter: Optional[Dict[str, Any]],
    mapspec: Optional[Dict[str, Any]],
) -> set[str]:
    """当前活引用集合（章节行 + MapSpec sources + 组件 chartRef）。"""
    live: set[str] = set()

    def _add(ref: Any) -> None:
        if isinstance(ref, str) and ref.startswith("ref:"):
            live.add(ref)

    if isinstance(chapter, dict):
        for row in list(chapter.get("data_requirements") or []) + list(
            chapter.get("analysis_steps") or []
        ):
            if isinstance(row, dict):
                _add(row.get("bound_ref"))
    if isinstance(mapspec, dict):
        raw_sources = mapspec.get("sources")
        source_defs: Iterable[Dict[str, Any]]
        if isinstance(raw_sources, dict):
            source_defs = [v for v in raw_sources.values() if isinstance(v, dict)]
        else:
            source_defs = [s for s in (raw_sources or []) if isinstance(s, dict)]
        for src in source_defs:
            for key in ("ref", "ref_id", "image_ref", "imageRef", "result_ref"):
                _add(src.get(key))
        for comp in (mapspec.get("layout") or {}).get("components") or []:
            if isinstance(comp, dict):
                _add((comp.get("options") or {}).get("chartRef"))
    return live


async def sweep_statuses(
    session_id: str,
    *,
    chapter: Optional[Dict[str, Any]] = None,
    mapspec: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[str]]:
    """生命周期巡检（派生状态刷新，不删数据）：

    - expired：store 探测 ref 缺失（TTL/LRU 驱逐）；
    - stale：ref 存活但不在活引用集合（行/spec/组件都不再指向它）；
    - valid：ref 存活且仍被引用（superseded 保持不变）。

    返回 ``{"expired": [...], "stale": [...], "valid": [...]}``。
    """
    from app.services.distributed_lock import session_lock_registry
    from app.services.session_data import session_data_manager

    empty: Dict[str, List[str]] = {"expired": [], "stale": [], "valid": []}
    if not session_id:
        return empty
    try:
        records = await _load_records(session_id, session_data_manager)
    except Exception:  # noqa: BLE001
        return empty
    if not records:
        return empty

    if mapspec is None:
        try:
            from app.services.mapspec_store import mapspec_store

            mapspec = await mapspec_store.get_mapspec(session_id)
        except Exception:  # noqa: BLE001
            mapspec = None

    async def _probe(aid: str) -> Tuple[str, Optional[dict]]:
        try:
            return aid, await session_data_manager.get_ref_descriptor(session_id, aid)
        except Exception:  # noqa: BLE001
            return aid, None

    probed = dict(
        await asyncio.gather(*(_probe(aid) for aid in records.keys()))
    )
    live = await load_records_for_plan(session_id, chapter, mapspec)
    now = time.time()
    result: Dict[str, List[str]] = {"expired": [], "stale": [], "valid": []}
    changed = False
    for aid, rec in records.items():
        if rec.status == A_SUPERSEDED or rec.status == A_FAILED:
            continue  # 终态记录不巡检
        desc = probed.get(aid)
        if desc is None:
            if rec.status != A_EXPIRED:
                rec.status = A_EXPIRED
                rec.updated_at = now
                changed = True
            result["expired"].append(aid)
        elif aid not in live:
            if rec.status != A_STALE:
                rec.status = A_STALE
                rec.updated_at = now
                changed = True
            result["stale"].append(aid)
        else:
            if rec.status != A_VALID:
                rec.status = A_VALID
                rec.updated_at = now
                changed = True
            result["valid"].append(aid)
    if changed:
        try:
            async with session_lock_registry.lock(session_id, fail_on_degraded=False):
                fresh = await _load_records(session_id, session_data_manager)
                for aid, bucket in (
                    [(a, "expired") for a in result["expired"]]
                    + [(a, "stale") for a in result["stale"]]
                    + [(a, "valid") for a in result["valid"]]
                ):
                    if aid in fresh and fresh[aid].status not in (
                        A_SUPERSEDED,
                        A_FAILED,
                    ):
                        fresh[aid].status = bucket
                        fresh[aid].updated_at = now
                await _save_records(session_id, fresh, session_data_manager)
        except Exception:  # noqa: BLE001 — 状态刷新失败下次巡检重试
            logger.info("[ArtifactRegistry] sweep persist skipped session=%s", session_id)
    return result


async def collect_orphan_refs(
    session_id: str,
    *,
    chapter: Optional[Dict[str, Any]] = None,
    mapspec: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """GC：删除「GC 态且不在活引用集合」产物背后的 store ref。

    只删 registry 记录为 superseded/stale/expired/failed 且行/spec/组件
    均不引用的 ref —— 活引用（哪怕记录态未刷新）绝不删除。retry/replan
    产生的新 ref 天然在活集合（行已重绑）。
    """
    from app.services.distributed_lock import session_lock_registry
    from app.services.session_data import session_data_manager

    if not session_id:
        return []
    deleted: List[str] = []
    try:
        records = await _load_records(session_id, session_data_manager)
        if not records:
            return []
        if mapspec is None:
            try:
                from app.services.mapspec_store import mapspec_store

                mapspec = await mapspec_store.get_mapspec(session_id)
            except Exception:  # noqa: BLE001
                mapspec = None
        live = await load_records_for_plan(session_id, chapter, mapspec)
        orphans = [
            aid
            for aid, rec in records.items()
            if rec.status in _TERMINAL_GC_STATUSES and aid not in live
        ]
        if not orphans:
            return []
        async with session_lock_registry.lock(session_id, fail_on_degraded=False):
            # 锁内复检活引用（并发 rebind 保护）
            live_now = await load_records_for_plan(session_id, chapter, mapspec)
            for aid in orphans:
                if aid in live_now:
                    continue
                try:
                    if await session_data_manager.delete_ref(session_id, aid):
                        deleted.append(aid)
                        records[aid].status = A_EXPIRED
                        records[aid].updated_at = time.time()
                except Exception:  # noqa: BLE001 — 单个删除失败跳过
                    continue
            await _save_records(session_id, records, session_data_manager)
    except Exception:  # noqa: BLE001 — GC 失败下次再试
        logger.info("[ArtifactRegistry] orphan GC skipped session=%s", session_id)
        return deleted
    return deleted


async def artifact_dependency_report(session_id: str) -> Dict[str, Any]:
    """血缘快照（诊断/测试/未来 product-graph 输入；派生只读）。"""
    records = {
        r.artifact_id: r for r in await list_artifacts(session_id)
    }
    graph = build_artifact_graph(records)
    return {
        "artifacts": [
            {
                "artifact_id": r.artifact_id,
                "type": r.artifact_type,
                "capability": r.producer_capability,
                "tool": r.producer_tool,
                "inputs": r.inputs,
                "status": r.status,
                "feature_count": r.feature_count,
                "empty": r.empty,
                "bbox": r.bbox,
                "replaces": r.replaces,
            }
            for r in records.values()
        ],
        "consumers": {
            aid: graph.consumers(aid)
            for aid in records
            if graph.consumers(aid)
        },
    }
