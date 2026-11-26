"""WP1 · 任务数据需求合同（Task Data Requirement Contract，TDR-v1）。

GOAL-20261126-02：把「用户意图 → 需要什么数据」组织成**有证据、可追溯、
有界**的任务级需求投影——不是新的资格状态机，也不是第二个取数计划。

与其他权威的关系（红线）：
- 五态资格 ``QUALIFICATION_STATES``（gis_harness/data_qualification.py）
  保持权威不变；本模块的 ``DEMAND_STATES`` 是**供给侧**状态（这条需求
  被满足了没有），与数据本身的资格判定正交；
- 任务范围 ``task_scope``（dataset_semantics/task_scope.py）保持权威；
  本模块在其上**增补**时间窗与几何实体两个声明位（原模块只有 place/bbox，
  勘察断链 #6），并复用 ``scope_fingerprint`` 作空间粒度指纹；
- workflow_v4 ``AcquisitionPlan``（角色×通道布尔声明）保持权威；本模块
  的 ``requirement_from_workflow_v4`` 是它的**消费投影**，不反向修改。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from app.services.dataset_semantics.task_scope import (
    scope_fingerprint,
    task_scope_from_dict,
)

#: TDR 合同版本（有界输出内声明，消费方可据此演进）。
TDR_VERSION = "tdr-v1"

#: 供给侧需求状态（封闭词表；与五态资格正交，映射见 module docstring）。
#: - satisfied：该角色已绑定合格 ref（来自 role resolution bound）；
#: - candidate_found：有候选但尚未批准/绑定（WP2/3 填充）；
#: - coverage_gap：有候选但覆盖不足（WP2 判定后回填）；
#: - not_searched：尚未搜索（external/unresolved 的诚实映射——规划期
#:   「声明了目录通道」≠「已经找到数据」）；
#: - source_unreachable / not_authorized：服务失败 / 无权限（typed 映射）；
#: - not_available：已搜索且确认所有已配置通道都不存在该数据；
#: - stale_version：候选存在但版本/时间口径不匹配；
#: - display_only：仅有展示级来源（WMS/tiles），不得进入数值统计；
#: - degraded：缺失已按 missing_policy=degrade 接受并强制披露；
#: - unknown：事实不足，无法判定（未知 ≠ 不存在）。
DEMAND_STATES = (
    "satisfied", "candidate_found", "coverage_gap", "not_searched",
    "source_unreachable", "not_authorized", "not_available",
    "stale_version", "display_only", "degraded", "unknown",
)

#: 目标消费方法（封闭词表；WP2 覆盖评估与资格映射的判据输入）。
CONSUMPTION_METHODS = (
    "numeric_stats",       # 数值统计（表/矢量属性聚合）
    "rate",                # 率（必须带分母）
    "density",             # 密度（点/面）
    "accessibility",       # 可达性（设施+网络）
    "temporal_comparison", # 时间比较（两期以上）
    "map_display",         # 地图展示
    "geometry_skeleton",   # 几何骨架（边界/分区）
)

_ROLES = (
    "subject", "measure", "boundary", "denominator", "network",
    "elevation", "hazard", "receptor", "population", "facility",
    "criteria", "constraint", "reference", "baseline", "target_time",
    "comparison_time",
)

_GEOMETRY_KINDS = ("point", "line", "polygon", "raster", "table",
                   "network", "unknown")

_TIME_GRANULARITIES = ("instant", "daily", "monthly", "yearly", "period")

_MAX_DEMANDS = 16
_MAX_GEOMETRY_VERTICES = 2000
_MAX_PROVENANCE = 8

_REASON_STATE = {
    "satisfied": "",
    "candidate_found": "SUPPLY_DEMAND_CANDIDATE_FOUND",
    "coverage_gap": "SUPPLY_DEMAND_COVERAGE_GAP",
    "not_searched": "SUPPLY_DEMAND_NOT_SEARCHED",
    "source_unreachable": "SUPPLY_DEMAND_SOURCE_UNREACHABLE",
    "not_authorized": "SUPPLY_DEMAND_NOT_AUTHORIZED",
    "not_available": "SUPPLY_DEMAND_NOT_AVAILABLE",
    "stale_version": "SUPPLY_DEMAND_STALE_VERSION",
    "display_only": "SUPPLY_DEMAND_DISPLAY_ONLY",
    "degraded": "SUPPLY_DEMAND_DEGRADED",
    "unknown": "SUPPLY_DEMAND_UNKNOWN",
}


def _scope_rejection(detail: str) -> ValueError:
    return ValueError(f"REQUIREMENT_SCOPE_INVALID: {detail}")


def _validate_bbox(bbox: Any) -> Tuple[Optional[List[float]], bool]:
    """bbox → ([minx, miny, maxx, maxy], crosses_dateline)。

    跨日期线约定：minx > maxx 表示跨越 ±180° 经线（如 [170,-20,-170,20]）。
    与既有 ``task_scope._normalize_bbox`` 不同——那里把 minx>maxx 视为
    非法（普通 bbox 场景正确）；本合同需要显式支持跨线任务范围，因此
    自行校验：分量有限 + miny≤maxy +（minx>maxx 视为跨线而非错误）。
    跨线 bbox 传入既有 preflight 闸时会被诚实判 UNVERIFIABLE（既有闸
    不认识跨线形状——这是如实披露，不是本模块绕闸）。
    """
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise _scope_rejection(f"bbox 形状错误（需 [minx,miny,maxx,maxy]）: "
                               f"{type(bbox).__name__}/{len(bbox) if hasattr(bbox, '__len__') else '?'}")
    try:
        out = [float(b) for b in bbox]
    except (TypeError, ValueError):
        raise _scope_rejection("bbox 分量不可解析为数值")
    if not all(math.isfinite(b) for b in out):
        raise _scope_rejection("bbox 含 NaN/±Inf 分量")
    if out[1] > out[3]:
        raise _scope_rejection("bbox miny > maxy")
    crosses = out[0] > out[2]
    return out, crosses


def _validate_time_range(time_range: Any) -> Optional[List[str]]:
    """time_range → 有序 ISO 日期字符串对；坏输入类型化拒绝。"""
    if time_range is None:
        return None
    if (not isinstance(time_range, (list, tuple)) or len(time_range) != 2):
        raise ValueError(
            f"REQUIREMENT_TIME_INVALID: time_range 需 [start, end]，"
            f"得到长度 {len(time_range) if hasattr(time_range, '__len__') else '?'}")
    parsed: List[Any] = []
    for raw in time_range:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                f"REQUIREMENT_TIME_INVALID: 时间端点必须是非空字符串，"
                f"得到 {raw!r}")
        s = raw.strip()
        try:
            if len(s) == 10:
                parsed.append(date.fromisoformat(s))
            else:
                parsed.append(datetime.fromisoformat(s.replace("Z", "+00:00")))
        except ValueError:
            raise ValueError(
                f"REQUIREMENT_TIME_INVALID: 无法解析 ISO 日期/时间 {raw!r}")
    if parsed[0] > parsed[1]:
        raise ValueError(
            f"REQUIREMENT_TIME_INVALID: start({time_range[0]}) 晚于 "
            f"end({time_range[1]})")
    return [str(time_range[0]).strip(), str(time_range[1]).strip()]


def _geometry_summary(geometry: Any) -> Dict[str, Any]:
    """GeoJSON 几何 → 有界摘要（顶点数/bbox/类型）——不保留全量坐标。"""
    if geometry is None:
        return {}
    if not isinstance(geometry, dict) or "type" not in geometry:
        raise _scope_rejection("geometry 需为 GeoJSON dict（含 type）")
    gtype = str(geometry["type"])
    coords = geometry.get("coordinates")
    if not isinstance(coords, (list, tuple)):
        raise _scope_rejection("geometry.coordinates 缺失或非列表")

    vertices: List[List[float]] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if (len(node) >= 2 and isinstance(node[0], (int, float))
                    and isinstance(node[1], (int, float))):
                x, y = float(node[0]), float(node[1])
                if not (math.isfinite(x) and math.isfinite(y)):
                    raise _scope_rejection("geometry 含非有限坐标")
                vertices.append([x, y])
                return
            for child in node:
                walk(child)

    walk(coords)
    if not vertices:
        raise _scope_rejection("geometry 无有效顶点")
    if len(vertices) > _MAX_GEOMETRY_VERTICES:
        raise _scope_rejection(
            f"geometry 顶点数 {len(vertices)} 超上限 {_MAX_GEOMETRY_VERTICES}"
            "（任务需求合同只接受概化范围，不接收全量数据）")
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    lon_span = max(xs) - min(xs)
    return {
        "type": gtype[:24],
        "vertex_count": len(vertices),
        "bbox": [round(b, 6) for b in bbox],
        "crosses_dateline": lon_span > 180.0,
    }


@dataclass
class TaskScope:
    """任务空间-时间范围声明（place / bbox / geometry / time_range）。"""
    place: str = ""
    bbox: Optional[List[float]] = None
    geometry: Optional[Dict[str, Any]] = None
    time_range: Optional[List[str]] = None

    def __post_init__(self) -> None:
        self.place = str(self.place or "").strip()[:128]
        if self.bbox is not None:
            self.bbox, self.crosses_dateline = _validate_bbox(self.bbox)
        else:
            self.crosses_dateline = False
            if self.geometry is None and not self.place:
                pass  # 空范围是合法的诚实声明（unknown 粒度）
        if self.geometry is not None:
            self._geometry_summary = _geometry_summary(self.geometry)
        else:
            self._geometry_summary = {}
        self.time_range = _validate_time_range(self.time_range)

    def normalized(self) -> Dict[str, Any]:
        """归一化声明 dict（place 归一；跨线 bbox 原样保留）。"""
        out: Dict[str, Any] = {}
        if self.place:
            out["place"] = self.place
        if self.bbox is not None:
            out["bbox"] = list(self.bbox)
        if self.time_range is not None:
            out["time_range"] = list(self.time_range)
        if self.geometry is not None:
            out["geometry_summary"] = dict(self._geometry_summary)
        return out

    def spatial_fingerprint(self) -> str:
        """空间范围指纹——复用既有 ``scope_fingerprint``（place+非跨线
        bbox 同粒度可比）；跨线 bbox 既有指纹不认识，此时指纹只由 place
        （或空）决定，几何差异由 requirement_id 覆盖（真实且如实）。"""
        norm = task_scope_from_dict({
            "place": self.place,
            "bbox": (list(self.bbox) if self.bbox is not None
                     and not self.crosses_dateline else None),
        })
        if not norm:
            return scope_fingerprint()
        return scope_fingerprint(place=norm.get("place"),
                                 bbox=norm.get("bbox"))

    def to_preflight_dict(self) -> Dict[str, Any]:
        """既有 preflight 闸的投影（{place?, bbox?}）。

        跨线 bbox 原样传入：既有闸对不可解析 bbox 如实回报
        PREFLIGHT_SCOPE_UNVERIFIABLE（declared-but-unparseable 路径），
        不会静默当作未声明。
        """
        out: Dict[str, Any] = {}
        if self.place:
            out["place"] = self.place
        if self.bbox is not None:
            out["bbox"] = list(self.bbox)
        return out

    def to_bounded_dict(self) -> Dict[str, Any]:
        out = self.normalized()
        if self.bbox is not None:
            out["crosses_dateline"] = self.crosses_dateline
        return out


@dataclass
class RoleDemand:
    """一个数据角色的任务级需求声明（要什么、给谁用、门槛是什么）。"""
    role: str
    required: bool = True
    geometry_kind: str = "unknown"
    consumption_methods: Tuple[str, ...] = ()
    required_fields: Tuple[str, ...] = ()
    admin_level: str = ""            # 行政区口径（如 "district"）
    resolution_meters: Optional[float] = None   # 最低空间粒度
    time_granularity: str = ""       # ⊆ _TIME_GRANULARITIES
    needs_denominator: bool = False  # 率/公平性结论必须分母
    license_attribution_required: bool = False
    license_redistribution_required: bool = False
    state: str = "not_searched"      # ⊆ DEMAND_STATES
    reason_code: str = ""
    disclosure: str = ""
    bound_ref: str = ""              # satisfied 时：绑定的 ref
    degraded: bool = False           # missing_policy=degrade 已接受缺失
    provenance: Tuple[str, ...] = ()  # 该需求的来源链（user/recipe/family）

    def __post_init__(self) -> None:
        self.role = str(self.role or "").strip()[:64]
        if self.role not in _ROLES:
            raise ValueError(
                f"REQUIREMENT_ROLE_UNKNOWN: {self.role!r} ∉ DATA_ROLES 词表"
                f"（合法值：{','.join(_ROLES)}）")
        self.state = str(self.state or "not_searched")
        if self.state not in DEMAND_STATES:
            raise ValueError(
                f"REQUIREMENT_STATE_UNKNOWN: {self.state!r} ∉ DEMAND_STATES")
        methods: List[str] = []
        for m in self.consumption_methods:
            m = str(m).strip()[:32]
            if m and m not in CONSUMPTION_METHODS:
                raise ValueError(
                    f"REQUIREMENT_CONSUMPTION_UNKNOWN: {m!r} ∉ "
                    f"CONSUMPTION_METHODS（合法值："
                    f"{','.join(CONSUMPTION_METHODS)}）")
            if m and m not in methods:
                methods.append(m)
        self.consumption_methods = tuple(methods)
        fields: List[str] = []
        for f_ in self.required_fields:
            f_ = str(f_).strip()[:64]
            if f_ and f_ not in fields:
                fields.append(f_)
        self.required_fields = tuple(fields)
        if self.resolution_meters is not None:
            try:
                self.resolution_meters = float(self.resolution_meters)
            except (TypeError, ValueError):
                raise ValueError(
                    "REQUIREMENT_RESOLUTION_INVALID: resolution_meters "
                    f"不可解析: {self.resolution_meters!r}")
            if not math.isfinite(self.resolution_meters) or (
                    self.resolution_meters < 0):
                raise ValueError(
                    "REQUIREMENT_RESOLUTION_INVALID: resolution_meters "
                    "需为非负有限数值")
        if self.time_granularity:
            self.time_granularity = str(self.time_granularity)[:16]
            if self.time_granularity not in _TIME_GRANULARITIES:
                raise ValueError(
                    f"REQUIREMENT_TIME_GRANULARITY_UNKNOWN: "
                    f"{self.time_granularity!r} ∉ "
                    f"{','.join(_TIME_GRANULARITIES)}")
        self.geometry_kind = str(self.geometry_kind or "unknown")[:16]
        if self.geometry_kind not in _GEOMETRY_KINDS:
            raise ValueError(
                f"REQUIREMENT_GEOMETRY_KIND_UNKNOWN: "
                f"{self.geometry_kind!r} ∉ {','.join(_GEOMETRY_KINDS)}")
        self.admin_level = str(self.admin_level or "")[:32]
        self.reason_code = str(self.reason_code or "")[:64]
        self.disclosure = str(self.disclosure or "")[:300]
        self.bound_ref = str(self.bound_ref or "")[:96]
        prov: List[str] = []
        for p in self.provenance:
            p = str(p).strip()[:80]
            if p and p not in prov:
                prov.append(p)
        self.provenance = tuple(prov)

    def satisfied(self) -> bool:
        return self.state == "satisfied"

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "required": self.required,
            "geometry_kind": self.geometry_kind,
            "consumption_methods": list(self.consumption_methods)[:4],
            "required_fields": list(self.required_fields)[:8],
            "admin_level": self.admin_level,
            "resolution_meters": self.resolution_meters,
            "time_granularity": self.time_granularity,
            "needs_denominator": self.needs_denominator,
            "license_attribution_required": self.license_attribution_required,
            "license_redistribution_required":
                self.license_redistribution_required,
            "state": self.state,
            "reason_code": self.reason_code,
            "disclosure": self.disclosure[:200],
            "bound_ref": self.bound_ref,
            "degraded": self.degraded,
            "provenance": list(self.provenance)[:4],
        }


@dataclass
class TaskDataRequirement:
    """整任务的数据需求合同（范围 + 角色×门槛 + 追溯）。"""
    task_scope: TaskScope
    items: List[RoleDemand] = field(default_factory=list)
    provenance: List[Dict[str, str]] = field(default_factory=list)
    confirmed_by_user: bool = False
    confirmation_ref: str = ""
    requirement_id: str = ""

    def __post_init__(self) -> None:
        if len(self.items) > _MAX_DEMANDS:
            raise ValueError(
                f"REQUIREMENT_TOO_MANY_DEMANDS: {len(self.items)} > "
                f"{_MAX_DEMANDS}")
        seen: Dict[str, RoleDemand] = {}
        for it in self.items:
            if it.role in seen:
                prev = seen[it.role]
                merged_fields = list(dict.fromkeys(
                    list(prev.required_fields) + list(it.required_fields)))
                merged_methods = list(dict.fromkeys(
                    list(prev.consumption_methods)
                    + list(it.consumption_methods)))
                merged_prov = list(dict.fromkeys(
                    list(prev.provenance) + list(it.provenance)))
                seen[it.role] = RoleDemand(
                    role=it.role,
                    required=prev.required or it.required,
                    geometry_kind=(it.geometry_kind
                                   if it.geometry_kind != "unknown"
                                   else prev.geometry_kind),
                    consumption_methods=tuple(merged_methods),
                    required_fields=tuple(merged_fields),
                    admin_level=it.admin_level or prev.admin_level,
                    resolution_meters=(it.resolution_meters
                                       if it.resolution_meters is not None
                                       else prev.resolution_meters),
                    time_granularity=(it.time_granularity
                                      or prev.time_granularity),
                    needs_denominator=(prev.needs_denominator
                                       or it.needs_denominator),
                    license_attribution_required=(
                        prev.license_attribution_required
                        or it.license_attribution_required),
                    license_redistribution_required=(
                        prev.license_redistribution_required
                        or it.license_redistribution_required),
                    state=it.state if it.state != "not_searched" else prev.state,
                    reason_code=it.reason_code or prev.reason_code,
                    disclosure=it.disclosure or prev.disclosure,
                    bound_ref=it.bound_ref or prev.bound_ref,
                    degraded=prev.degraded or it.degraded,
                    provenance=tuple(merged_prov),
                )
            else:
                seen[it.role] = it
        self.items = list(seen.values())
        self.provenance = [dict(p) for p in self.provenance][:_MAX_PROVENANCE]
        self.confirmation_ref = str(self.confirmation_ref or "")[:96]
        if not self.requirement_id:
            self.requirement_id = self._compute_id()

    def _compute_id(self) -> str:
        payload = {
            "version": TDR_VERSION,
            "scope": self.task_scope.normalized(),
            "items": [
                {k: v for k, v in it.__dict__.items()
                 if k not in ("state", "reason_code", "disclosure",
                              "bound_ref", "degraded")}
                for it in self.items
            ],
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          default=str)
        return "tdr-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]

    def item(self, role: str) -> Optional[RoleDemand]:
        for it in self.items:
            if it.role == role:
                return it
        return None

    def missing_obligations(self) -> List[Dict[str, Any]]:
        """required 且未满足的角色 → 有界缺口清单（WP3 供给计划的输入）。"""
        out: List[Dict[str, Any]] = []
        for it in self.items:
            if not it.required or it.satisfied():
                continue
            out.append({
                "role": it.role,
                "state": it.state,
                "reason_code": (it.reason_code
                                or _REASON_STATE.get(it.state, "")),
                "degraded": it.degraded,
                "needs_denominator": it.needs_denominator,
                "consumption_methods": list(it.consumption_methods)[:4],
                "disclosure": it.disclosure[:200],
            })
        return out

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "version": TDR_VERSION,
            "requirement_id": self.requirement_id,
            "spatial_fingerprint": self.task_scope.spatial_fingerprint(),
            "task_scope": self.task_scope.to_bounded_dict(),
            "items": [it.to_bounded_dict() for it in self.items],
            "missing_obligations": self.missing_obligations(),
            "provenance": [
                {k: str(v)[:64] for k, v in p.items() if k in
                 ("source", "family", "recipe", "note")}
                for p in self.provenance
            ],
            "confirmed_by_user": self.confirmed_by_user,
            "confirmation_ref": self.confirmation_ref,
        }


# ── 投影入口 ─────────────────────────────────────────────────────────────

_STATUS_TO_STATE = {
    # 规划期事实裁决（workflow_v4 resolve_data_roles 语义）：
    # external =「声明了目录/上传通道」≠「已找到」→ not_searched。
    "bound": "satisfied",
    "external": "not_searched",
    "unresolved": "not_searched",
    "degraded": "degraded",
}


def task_requirement(
    task_scope: TaskScope,
    demands: List[Dict[str, Any]],
    *,
    provenance: Optional[List[Dict[str, str]]] = None,
    confirmed_by_user: bool = False,
    confirmation_ref: str = "",
) -> TaskDataRequirement:
    """核心构造：范围 + 角色需求 dict 列表 → 需求合同（确定性）。"""
    items = [RoleDemand(**d) for d in demands[:_MAX_DEMANDS]]
    return TaskDataRequirement(
        task_scope=task_scope,
        items=items,
        provenance=list(provenance or []),
        confirmed_by_user=confirmed_by_user,
        confirmation_ref=confirmation_ref,
    )


def requirement_from_role_demands(
    demands: List[Dict[str, Any]],
    task_scope: TaskScope,
    *,
    provenance: Optional[List[Dict[str, str]]] = None,
    confirmed_by_user: bool = False,
    confirmation_ref: str = "",
) -> TaskDataRequirement:
    """普通角色需求 dict（如方法论族 data_role_demands）→ 需求合同。"""
    return task_requirement(
        task_scope, demands, provenance=provenance,
        confirmed_by_user=confirmed_by_user,
        confirmation_ref=confirmation_ref)


def requirement_from_workflow_v4(
    role_resolutions: List[Any],
    task_scope: TaskScope,
    *,
    provenance: Optional[List[Dict[str, str]]] = None,
    confirmed_by_user: bool = False,
    confirmation_ref: str = "",
) -> TaskDataRequirement:
    """workflow_v4 角色解析序列 → 需求合同（消费投影，不反向修改）。

    映射（诚实）：bound→satisfied（带 bound_ref）；external/unresolved→
    not_searched（规划期不可证伪 ≠ 已找到）；degraded→degraded（披露
    保留）。不复制 AcquisitionPlan——那是通道可行性声明，本合同是需求侧。
    """
    demands: List[Dict[str, Any]] = []
    for r in list(role_resolutions)[:_MAX_DEMANDS]:
        role = str(getattr(r, "role", "") or
                   (r.get("role") if isinstance(r, dict) else "") or "")
        if not role:
            continue
        status = str(getattr(r, "status", "") or
                     (r.get("status") if isinstance(r, dict) else "")
                     or "unresolved")
        demands.append({
            "role": role,
            "required": bool(getattr(r, "required", True)
                             if not isinstance(r, dict)
                             else r.get("required", True)),
            "state": _STATUS_TO_STATE.get(status, "unknown"),
            "reason_code": ("" if status == "bound" else
                            str(getattr(r, "reason_code", "") or
                                (r.get("reason_code")
                                 if isinstance(r, dict) else "") or "")),
            "disclosure": str(getattr(r, "disclosure", "") or
                              (r.get("disclosure")
                               if isinstance(r, dict) else "") or ""),
            "bound_ref": str(getattr(r, "bound_ref", "") or
                             (r.get("bound_ref")
                              if isinstance(r, dict) else "") or ""),
            "degraded": status == "degraded",
        })
    prov = list(provenance or [])
    prov.append({"source": "workflow_v4"})
    return task_requirement(
        task_scope, demands, provenance=prov,
        confirmed_by_user=confirmed_by_user,
        confirmation_ref=confirmation_ref)


__all__ = [
    "TDR_VERSION",
    "DEMAND_STATES",
    "CONSUMPTION_METHODS",
    "TaskScope",
    "RoleDemand",
    "TaskDataRequirement",
    "task_requirement",
    "requirement_from_role_demands",
    "requirement_from_workflow_v4",
]
