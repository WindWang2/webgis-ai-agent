"""Data Vocabulary V3 —— 数据资产统一词表（契约层，零 I/O）。

V3 之前同一概念存在多套互不通用的词表（审计结论）：
- artifact_type：21 个细粒度注册类型（lib/gis/artifacts.py）vs DB 的
  ``vector|raster|analysis`` vs OutputSemanticType 的 10 值；
- 生命周期：会话 5 态（valid/stale/expired/superseded/failed）vs DB 无态
  vs catalog 2 态 vs layer 4 态 vs promotion 5 态；
- 质量：ProjectDataset.quality_status 7 值 vs 各检查点自造状态。

本模块是**唯一**词表定义处：
- ``ArtifactCategory``（粗类）：V3 契约级类型，覆盖目标清单的 15 个基类，
  允许受控扩展（``register_category``），细粒度语义仍归 21 类型注册表；
- ``LogicalRole``：数据在分析中扮演的角色（工作流/Agent 可消费）；
- ``LifecycleState``：有限生命周期状态机（合并既有 5 态语义，不推翻）；
- ``QualityStatus``：质量四态 + unchecked；
- ``PersistenceTier`` / ``MaterializationPolicy``：存储层级与物化策略。

映射函数把既有词表投影到本词表 —— 既有系统不需要迁移，V3 消费方拿到
统一语义。禁止在本模块 import 任何 app.services/*（保持契约层零依赖）。
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Tuple


class VocabularyError(ValueError):
    """词表受控扩展被拒绝（重复/非法值）。"""


class ArtifactCategory(str, Enum):
    """V3 契约级粗类型（§四 的 15 基类）。细粒度语义 = 21 类型注册表。"""

    VECTOR = "vector"
    RASTER = "raster"
    TABLE = "table"
    POINT_CLOUD = "point_cloud"
    NETWORK = "network"
    TIMESERIES = "timeseries"
    MATRIX = "matrix"
    MODEL = "model"
    STATISTICS = "statistics"
    CHART_DATA = "chart_data"
    REPORT = "report"
    MAP_EXPORT = "map_export"
    ARCHIVE = "archive"
    METADATA = "metadata"
    COLLECTION = "collection"


# 受控扩展注册表（默认 = 全部基类；测试/下游可注册扩展粗类，不可覆盖）。
_EXTENSION_CATEGORIES: Dict[str, str] = {}


def register_category(category: str) -> None:
    """注册扩展粗类（幂等；与基类或已注册值冲突 → VocabularyError）。"""
    key = str(category or "").strip()
    if not key or key != key.lower() or not key.replace("_", "").isalnum():
        raise VocabularyError(f"illegal extension category: {category!r}")
    if key in {c.value for c in ArtifactCategory} or key in _EXTENSION_CATEGORIES:
        raise VocabularyError(f"duplicate category: {key}")
    _EXTENSION_CATEGORIES[key] = key


def all_categories() -> List[str]:
    return [c.value for c in ArtifactCategory] + sorted(_EXTENSION_CATEGORIES)


def coerce_category(value: object) -> Optional[str]:
    """任意词表值 → 粗类 token（基类或受控扩展；未知 → None，不虚构）。"""
    if isinstance(value, ArtifactCategory):
        return value.value
    token = str(value or "").strip().lower()
    if token in {c.value for c in ArtifactCategory} or token in _EXTENSION_CATEGORIES:
        return token
    return None


class LogicalRole(str, Enum):
    """数据角色（§五）：工作流准入判断与 Agent 检索的语义轴。"""

    SOURCE = "source"
    REFERENCE = "reference"
    BOUNDARY = "boundary"
    MASK = "mask"
    OBSERVATION = "observation"
    TRAINING = "training"
    VALIDATION = "validation"
    PREDICTION = "prediction"
    CONSTRAINT = "constraint"
    FACTOR = "factor"
    INTERMEDIATE = "intermediate"
    DERIVED = "derived"
    RESULT = "result"
    VISUALIZATION = "visualization"
    EXPORT = "export"
    TEMPORARY = "temporary"


class LifecycleState(str, Enum):
    """统一生命周期（§八，按现有架构调整：保留 superseded 态）。

    declared → ingesting → available → profiling → ready →
    materializing → derived → stale/superseded → archived → deleting →
    deleted；任意态 → error。
    """

    DECLARED = "declared"
    INGESTING = "ingesting"
    AVAILABLE = "available"
    PROFILING = "profiling"
    READY = "ready"
    MATERIALIZING = "materializing"
    DERIVED = "derived"
    STALE = "stale"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    DELETING = "deleting"
    DELETED = "deleted"
    ERROR = "error"


# 合法迁移表（含自环幂等；未列出的迁移非法）。
_LIFECYCLE_TRANSITIONS: Dict[LifecycleState, FrozenSet[LifecycleState]] = {
    LifecycleState.DECLARED: frozenset(
        {LifecycleState.INGESTING, LifecycleState.AVAILABLE, LifecycleState.DELETING, LifecycleState.ERROR}
    ),
    LifecycleState.INGESTING: frozenset(
        {LifecycleState.AVAILABLE, LifecycleState.DELETING, LifecycleState.ERROR}
    ),
    LifecycleState.AVAILABLE: frozenset(
        {
            LifecycleState.PROFILING,
            LifecycleState.READY,
            LifecycleState.MATERIALIZING,
            LifecycleState.STALE,
            LifecycleState.SUPERSEDED,
            LifecycleState.ARCHIVED,
            LifecycleState.DELETING,
            LifecycleState.ERROR,
        }
    ),
    LifecycleState.PROFILING: frozenset(
        {
            LifecycleState.READY,
            LifecycleState.AVAILABLE,
            LifecycleState.STALE,
            LifecycleState.DELETING,
            LifecycleState.ERROR,
        }
    ),
    LifecycleState.READY: frozenset(
        {
            LifecycleState.MATERIALIZING,
            LifecycleState.DERIVED,
            LifecycleState.STALE,
            LifecycleState.SUPERSEDED,
            LifecycleState.ARCHIVED,
            LifecycleState.DELETING,
            LifecycleState.ERROR,
        }
    ),
    LifecycleState.MATERIALIZING: frozenset(
        {
            LifecycleState.READY,
            LifecycleState.DERIVED,
            LifecycleState.AVAILABLE,
            LifecycleState.DELETING,
            LifecycleState.ERROR,
        }
    ),
    LifecycleState.DERIVED: frozenset(
        {
            LifecycleState.STALE,
            LifecycleState.SUPERSEDED,
            LifecycleState.ARCHIVED,
            LifecycleState.DELETING,
            LifecycleState.ERROR,
        }
    ),
    LifecycleState.STALE: frozenset(
        {
            LifecycleState.READY,  # 上游重算后复活（revalidate）
            LifecycleState.AVAILABLE,
            LifecycleState.SUPERSEDED,
            LifecycleState.ARCHIVED,
            LifecycleState.DELETING,
            LifecycleState.ERROR,
        }
    ),
    LifecycleState.SUPERSEDED: frozenset({LifecycleState.ARCHIVED, LifecycleState.DELETING}),
    LifecycleState.ARCHIVED: frozenset({LifecycleState.DELETING, LifecycleState.ERROR}),
    LifecycleState.DELETING: frozenset({LifecycleState.DELETED, LifecycleState.ERROR}),
    LifecycleState.DELETED: frozenset(),
    LifecycleState.ERROR: frozenset(
        {
            LifecycleState.INGESTING,  # 重试
            LifecycleState.AVAILABLE,  # 修复后恢复
            LifecycleState.DELETING,
        }
    ),
}


def can_transition(current: object, target: object) -> bool:
    """生命周期迁移合法性（current == target 视为幂等合法）。"""
    try:
        cur = LifecycleState(current)  # type: ignore[arg-type]
        tgt = LifecycleState(target)  # type: ignore[arg-type]
    except ValueError:
        return False
    if cur is tgt:
        return True
    return tgt in _LIFECYCLE_TRANSITIONS.get(cur, frozenset())


def terminal_states() -> FrozenSet[LifecycleState]:
    """无出边的终态（deleted）。"""
    return frozenset(s for s, outs in _LIFECYCLE_TRANSITIONS.items() if not outs)


def gc_eligible_states() -> FrozenSet[LifecycleState]:
    """可被 GC 考虑的派生态（仍在引用保护之下，见 gc planner）。"""
    return frozenset(
        {
            LifecycleState.STALE,
            LifecycleState.SUPERSEDED,
            LifecycleState.ARCHIVED,
            LifecycleState.DELETED,
            LifecycleState.ERROR,
        }
    )


class QualityStatus(str, Enum):
    """质量四态（§七）+ unchecked（未评估是合法初值，不虚构结论）。"""

    UNCHECKED = "unchecked"
    VALID = "valid"
    WARNING = "warning"
    REPAIRABLE = "repairable"
    BLOCKED = "blocked"

    @classmethod
    def from_issues(cls, has_blocker: bool, has_repairable: bool, has_warning: bool) -> "QualityStatus":
        if has_blocker:
            return cls.BLOCKED
        if has_repairable:
            return cls.REPAIRABLE
        if has_warning:
            return cls.WARNING
        return cls.VALID


class PersistenceTier(str, Enum):
    """存储层级（§八：temporary/session/workspace/persistent）。"""

    EPHEMERAL = "ephemeral"          # 一次请求内有效（preview、stream）
    SESSION = "session"              # 会话内有效（session store / Redis TTL）
    WORKSPACE = "workspace"          # 项目工作空间内持久
    PERSISTENT = "persistent"        # 用户明确长期保存


class MaterializationPolicy(str, Enum):
    """物化策略（§十二）：决定产物落到哪一层、可否被 GC/复用。"""

    EPHEMERAL = "ephemeral"
    CACHED = "cached"
    WORKSPACE_PERSISTENT = "workspace_persistent"
    USER_PERSISTENT = "user_persistent"
    EXPORTED = "exported"


_POLICY_TO_TIER: Dict[MaterializationPolicy, PersistenceTier] = {
    MaterializationPolicy.EPHEMERAL: PersistenceTier.EPHEMERAL,
    MaterializationPolicy.CACHED: PersistenceTier.SESSION,
    MaterializationPolicy.WORKSPACE_PERSISTENT: PersistenceTier.WORKSPACE,
    MaterializationPolicy.USER_PERSISTENT: PersistenceTier.PERSISTENT,
    MaterializationPolicy.EXPORTED: PersistenceTier.PERSISTENT,
}


def policy_tier(policy: MaterializationPolicy) -> PersistenceTier:
    return _POLICY_TO_TIER[policy]


# ── 既有词表 → V3 投影 ──────────────────────────────────────────────

# 21 细粒度类型（lib/gis/artifacts.py SEED_ARTIFACT_TYPES）→ 粗类。
# 未列出的细类型按 ref 前缀/几何族兜底，绝不虚构。
_FINE_TO_CATEGORY: Dict[str, ArtifactCategory] = {
    "feature_collection": ArtifactCategory.VECTOR,
    "point_feature_set": ArtifactCategory.VECTOR,
    "line_feature_set": ArtifactCategory.VECTOR,
    "polygon_feature_set": ArtifactCategory.VECTOR,
    "poi_feature_set": ArtifactCategory.VECTOR,
    "admin_boundary_set": ArtifactCategory.VECTOR,
    "density_surface": ArtifactCategory.RASTER,
    "raster_surface": ArtifactCategory.RASTER,
    "terrain_surface": ArtifactCategory.RASTER,
    "remote_sensing_index": ArtifactCategory.RASTER,
    "change_set": ArtifactCategory.VECTOR,
    "grid_aggregate": ArtifactCategory.VECTOR,
    "hotspot_result": ArtifactCategory.VECTOR,
    "proximity_zone": ArtifactCategory.VECTOR,
    "service_area": ArtifactCategory.VECTOR,
    "admin_aggregate_table": ArtifactCategory.TABLE,
    "stats_table": ArtifactCategory.TABLE,
    "od_table": ArtifactCategory.TABLE,
    "chart_spec": ArtifactCategory.CHART_DATA,
    "od_matrix": ArtifactCategory.MATRIX,
    "network_graph": ArtifactCategory.NETWORK,
}

# DB artifact_type（vector|raster|analysis，provenance/fingerprint.py）→ 粗类。
_DB_TYPE_TO_CATEGORY: Dict[str, ArtifactCategory] = {
    "vector": ArtifactCategory.VECTOR,
    "raster": ArtifactCategory.RASTER,
    "analysis": ArtifactCategory.VECTOR,  # analysis 行缺更细证据时的保守投影
}

# 会话账本 5 态（artifact_registry A_*）→ V3 生命周期。
_SESSION_STATUS_TO_LIFECYCLE: Dict[str, LifecycleState] = {
    "valid": LifecycleState.READY,
    "stale": LifecycleState.STALE,
    "expired": LifecycleState.DELETED,   # 探测缺失 = 载荷不可用（诚实语义）
    "superseded": LifecycleState.SUPERSEDED,
    "failed": LifecycleState.ERROR,
}

# promotion ContentStatus → 生命周期（project_artifact_promotion.py）。
_PROMOTION_STATUS_TO_LIFECYCLE: Dict[str, LifecycleState] = {
    "promoted": LifecycleState.READY,
    "already_promoted": LifecycleState.READY,
    "no_session_context": LifecycleState.AVAILABLE,
    "session_expired": LifecycleState.STALE,
    "store_unavailable": LifecycleState.ERROR,
}


def category_for_fine_type(fine_type: object) -> Optional[ArtifactCategory]:
    """21 细类型 → 粗类；未知 → None。"""
    return _FINE_TO_CATEGORY.get(str(fine_type or ""))


def category_for_geometry_kind(geometry_kind: object) -> Optional[ArtifactCategory]:
    """几何族兜底（细类型未注册时）：point/line/polygon → vector。"""
    kind = str(geometry_kind or "")
    if kind in ("point", "line", "polygon"):
        return ArtifactCategory.VECTOR
    if kind == "raster":
        return ArtifactCategory.RASTER
    if kind == "table":
        return ArtifactCategory.TABLE
    if kind == "network":
        return ArtifactCategory.NETWORK
    return None


def category_from_any(
    fine_type: object = None,
    db_type: object = None,
    geometry_kind: object = None,
) -> Optional[ArtifactCategory]:
    """多证据归约：细类型 > DB 类型 > 几何族。全未知 → None（不虚构）。"""
    return (
        category_for_fine_type(fine_type)
        or _DB_TYPE_TO_CATEGORY.get(str(db_type or ""))
        or category_for_geometry_kind(geometry_kind)
    )


def lifecycle_from_session_status(status: object) -> Optional[LifecycleState]:
    return _SESSION_STATUS_TO_LIFECYCLE.get(str(status or ""))


def lifecycle_from_promotion_status(status: object) -> Optional[LifecycleState]:
    return _PROMOTION_STATUS_TO_LIFECYCLE.get(str(status or ""))


def lifecycle_from_db_state(
    *, has_payload: bool, content_fingerprint: object = None, deleted: bool = False
) -> LifecycleState:
    """DB Artifact（无状态列）→ 生命周期投影。

    - 有内容指纹且载荷已物化 → ready；
    - 行存在但载荷仍指向会话 ref（未晋升）→ available（payload 可用性
      由 ref 探测决定，这里不谎称 ready）；
    - 已删除 → deleted。
    """
    if deleted:
        return LifecycleState.DELETED
    if content_fingerprint:
        return LifecycleState.READY
    return LifecycleState.AVAILABLE if has_payload else LifecycleState.DECLARED


def default_role_for_category(category: ArtifactCategory) -> LogicalRole:
    """无显式角色时的保守缺省（下游显式赋角色后覆盖）。"""
    if category in (ArtifactCategory.CHART_DATA, ArtifactCategory.REPORT, ArtifactCategory.MAP_EXPORT):
        return LogicalRole.VISUALIZATION
    return LogicalRole.DERIVED


def lifecycle_transition_pairs() -> List[Tuple[str, str]]:
    """全部合法迁移（诊断/文档用）。"""
    out: List[Tuple[str, str]] = []
    for src, outs in _LIFECYCLE_TRANSITIONS.items():
        for dst in outs:
            out.append((src.value, dst.value))
    return out


# 「未归类」是合法粗类（诚实未知原则：证据不足时不虚构 vector/raster）。
# 直接入注册表而非运行期 register_category —— 与基类同级的稳定词表值。
_EXTENSION_CATEGORIES.setdefault("unknown", "unknown")
