"""WP4 · 任务数据候选发现与供给计划存储（生产数据面接线）。

把三处既有只读事实源组成候选证据包：
- 会话联邦目录（``data_catalog.DataCatalog.search``——session/upload/fabric
  三通道，owner 作用域强制）；
- 目录条目 → ``CoverageCandidate`` 的保守投影（role_class 关键词推导，
  推导不出 → ``unknown``——unknown 不得进入数值统计资格）；
- 供给计划存储（会话作用域、有界 LRU；与 DatasetSemanticStore 同风格，
  进程内单例）。

红线：候选只来自用户已有资产、本地已配置目录、已治理远程服务；不开放
任意 crawler/URL egress；temporal/license 无证据 → 显式空（unknown ≠ 编造）。
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_catalog.catalog import (
    CatalogEntry,
    CatalogFilter,
    get_data_catalog,
)
from app.services.task_data_supply.coverage import CoverageCandidate
from app.services.task_data_supply.requirement import (
    TaskDataRequirement,
    TaskScope,
    task_requirement,
)
from app.services.task_data_supply.supply_plan import SupplyPlan

logger = logging.getLogger(__name__)

#: LogicalRole（catalog 词表）→ DATA_ROLES（workflow 词表）保守投影。
#: 推导不出 → 不声明服务角色（候选仍可见，但按「无证据」处理）。
_LOGICAL_ROLE_MAP = {
    "boundary": ("boundary",),
    "reference": ("reference",),
    "constraint": ("constraint",),
    "mask": ("constraint",),
    "observation": ("subject", "measure"),
    "source": ("subject",),
    "prediction": ("subject",),
    "result": ("subject",),
    "factor": ("criteria",),
    "baseline": ("baseline",),
}

_NUMERIC_MARKERS = ("raster", "cog", "dem", "tiff", "geotiff", "coverage")
_VECTOR_MARKERS = ("parquet", "geojson", "feature", "vector", "table",
                   "shapefile", "gpkg")
_DISPLAY_MARKERS = ("tile", "wms", "wmts", "pmtiles", "xyz")

_MAX_PLANS_PER_SESSION = 10
_MAX_SESSIONS = 200


def derive_role_class(entry: CatalogEntry) -> str:
    """目录条目 → service_qualification role_class（保守关键词推导）。

    推导不出 → ``unknown``（coverage 评估中 unknown 不得进入数值统计
    资格——不能把 unknown 自动当成功）。
    """
    blob = " ".join(filter(None, [
        entry.category, entry.artifact_subtype, entry.source_type,
        entry.name])).lower()
    if any(m in blob for m in _DISPLAY_MARKERS):
        return "display_only"
    if any(m in blob for m in _NUMERIC_MARKERS):
        return "numeric_raster"
    if any(m in blob for m in _VECTOR_MARKERS):
        return "vector_features"
    return "unknown"


def entry_to_candidate(entry: CatalogEntry) -> CoverageCandidate:
    """目录条目 → 覆盖评估候选证据包（保守投影；无证据字段显式空）。"""
    scope = entry.scope or "session"
    if scope == "fabric":
        channel = ("data_fabric_stac" if "stac" in
                   (entry.source_type or "").lower()
                   else "data_fabric_catalog")
    else:
        channel = "local"  # 会话/上传资产：已在本地，获取零成本
    serves = _LOGICAL_ROLE_MAP.get((entry.logical_role or "").lower(), ())
    cost_hint: Dict[str, Any] = {}
    if entry.size_bytes:
        cost_hint = {"bytes": int(entry.size_bytes)}
    return CoverageCandidate(
        candidate_id=entry.entry_id,
        channel=channel,
        crs=entry.crs or "EPSG:4326",
        serves_roles=serves,
        declared_bbox=list(entry.extent) if entry.extent else None,
        temporal_coverage=None,        # 目录条目无时间证据 → 显式 unknown
        version=(entry.stable_identity or "")[:64],
        feature_count=entry.feature_count,
        fields=tuple(entry.fields or ()),
        geometry_kind="unknown",
        role_class=derive_role_class(entry),
        license="",                    # 无许可证据 → 空（评估侧披露）
        attribution="",
        cost_hint=cost_hint or None,
        # fabric 通道需携带 source_ref（sync id 约定的 source_id 部分），
        # 供执行面解析 CatalogItemModel；其余通道保持 scope 标注
        source=(entry.source_ref if scope == "fabric" and entry.source_ref
                else f"catalog:{scope}"),
    )


def _parse_bbox_arg(bbox: Any) -> Optional[List[float]]:
    """"minx,miny,maxx,maxy" | list → 校验后的 bbox；坏形状 None。"""
    if bbox in (None, ""):
        return None
    if isinstance(bbox, str):
        parts = [p.strip() for p in bbox.split(",")]
        if len(parts) != 4:
            return None
        bbox = parts
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            vals = [float(b) for b in bbox]
        except (TypeError, ValueError):
            return None
        if any(v != v or v in (float("inf"), float("-inf")) for v in vals):
            return None
        if vals[1] > vals[3]:
            return None
        return vals  # minx>maxx 允许（跨日期线，TaskScope 再校验其余）
    return None


def _parse_time_arg(time_range: Any) -> Optional[List[str]]:
    """"2024-01-01,2024-12-31" | list → [start, end]；坏形状 None。"""
    if time_range in (None, ""):
        return None
    if isinstance(time_range, str):
        parts = [p.strip() for p in time_range.split(",")]
        if len(parts) != 2:
            return None
        time_range = parts
    if isinstance(time_range, (list, tuple)) and len(time_range) == 2:
        out = [str(t).strip() for t in time_range]
        if all(out):
            return out
    return None


async def discover_task_candidates(
    *,
    session_id: str,
    task_scope: TaskScope,
    roles: Tuple[str, ...] = (),
    keyword: str = "",
    limit: int = 24,
) -> Tuple[List[CoverageCandidate], Dict[str, Any]]:
    """会话联邦目录 → 候选证据包（有界；来源故障诚实披露）。

    返回 (candidates, meta)；meta 含 sources_queried 与条目遗漏说明。
    """
    from app.services.dataset_semantics.task_scope import _normalize_bbox

    filt_kwargs: Dict[str, Any] = {}
    norm_bbox = _normalize_bbox(task_scope.bbox) if (
        task_scope.bbox is not None
        and task_scope.bbox[0] <= task_scope.bbox[2]) else None
    if norm_bbox is not None:
        filt_kwargs["bbox"] = norm_bbox  # 粗筛：普通 bbox 才能传目录过滤
    if keyword:
        filt_kwargs["keyword"] = str(keyword)[:64]
    # CatalogFilter.role 是单数等值轴（LogicalRole）；一个 DATA_ROLE 可映射
    # 多个 LogicalRole（如 subject ← observation/source/prediction/result），
    # 单值反查有歧义——仅当映射唯一时才下推该轴，否则全量查询后按
    # serves_roles 后置过滤（保守：映射不到的角色不冒充匹配）
    if len(roles) == 1:
        matches = [lr for lr, drs in _LOGICAL_ROLE_MAP.items()
                   if roles[0] in drs]
        if len(matches) == 1:
            filt_kwargs["role"] = matches[0]

    result = await get_data_catalog().search(
        session_id=session_id,
        filter_=CatalogFilter(**filt_kwargs),
        limit=max(1, min(int(limit), 50)),
        owner=session_id,
    )
    candidates: List[CoverageCandidate] = []
    skipped_invalid: List[str] = []
    not_serving = 0
    for entry in result.entries[:50]:
        try:
            cand = entry_to_candidate(entry)
        except ValueError as e:
            skipped_invalid.append(f"{entry.entry_id}: {e}")
            continue
        # R1 评审 C P3-6：不映射请求角色的候选**不静默丢弃**——
        # 全量返回（带 serves_roles），由覆盖评估只评 serving 并在矩阵
        # unknown_reasons 诚实披露其余未评估
        if roles and not (set(cand.serves_roles) & set(roles)):
            not_serving += 1
        candidates.append(cand)
    meta = {
        "sources_queried": [s.model_dump() for s in result.sources_queried],
        "total_matched": result.total_matched,
        "returned": len(candidates),
        "truncated": result.truncated,
        "skipped_invalid": skipped_invalid[:4],
        "not_serving_requested_roles": not_serving,
        "bbox_coarse_filter": norm_bbox is not None,
        "note": ("跨日期线任务框不走目录 bbox 粗筛（全量枚举候选，"
                 "由覆盖评估精确判定）" if norm_bbox is None
                 and task_scope.bbox is not None else ""),
    }
    return candidates, meta


def build_requirement_from_args(
    *,
    place: str = "",
    bbox: Any = None,
    time_range: Any = None,
    roles: Tuple[Tuple[str, Tuple[str, ...]], ...] = (),
    provenance: str = "",
) -> TaskDataRequirement:
    """工具/路由参数 → 需求合同（角色→消费方法缺省映射显式化）。"""
    scope = TaskScope(
        place=place or "",
        bbox=_parse_bbox_arg(bbox),
        time_range=_parse_time_arg(time_range))
    demands: List[Dict[str, Any]] = []
    for role, methods in list(roles)[:16]:
        demands.append({
            "role": role,
            "consumption_methods": tuple(methods) or ("numeric_stats",),
        })
    prov = [{"source": provenance[:32] or "tool"}]
    return task_requirement(scope, demands, provenance=prov)


class TaskSupplyStore:
    """会话作用域供给计划存储（进程内、**按会话分桶**的 LRU、owner 隔离）。

    R1 评审 B P1-5：全局单一 LRU 会让任一会话连建 N 个计划就驱逐其他
    会话的待执行计划（可用性攻击）——改为每会话独立配额 + 会话数上限。
    """

    def __init__(self) -> None:
        self._buckets: "OrderedDict[str, OrderedDict[str, SupplyPlan]]" = (
            OrderedDict())

    def _bucket(self, sid: str) -> "OrderedDict[str, SupplyPlan]":
        b = self._buckets.get(sid)
        if b is None:
            b = OrderedDict()
            self._buckets[sid] = b
            while len(self._buckets) > _MAX_SESSIONS:
                self._buckets.popitem(last=False)
        else:
            self._buckets.move_to_end(sid)
        return b

    def save(self, session_id: str, plan: SupplyPlan) -> None:
        sid = str(session_id)[:128]
        b = self._bucket(sid)
        b[plan.plan_id] = plan
        b.move_to_end(plan.plan_id)
        while len(b) > _MAX_PLANS_PER_SESSION:
            b.popitem(last=False)

    def get(self, session_id: str, plan_id: str) -> Optional[SupplyPlan]:
        """owner 严格隔离：跨会话取他人计划 → None（不泄漏存在性）。"""
        b = self._buckets.get(str(session_id)[:128])
        return b.get(str(plan_id)[:96]) if b is not None else None

    def drop(self, session_id: str, plan_id: str) -> bool:
        b = self._buckets.get(str(session_id)[:128])
        return b.pop(str(plan_id)[:96], None) is not None if b else False

    def list_ids(self, session_id: str) -> List[str]:
        b = self._buckets.get(str(session_id)[:128])
        return list(b.keys()) if b else []


_STORE = TaskSupplyStore()


def get_task_supply_store() -> TaskSupplyStore:
    return _STORE


__all__ = [
    "TaskSupplyStore", "get_task_supply_store",
    "discover_task_candidates", "build_requirement_from_args",
    "entry_to_candidate", "derive_role_class",
]
