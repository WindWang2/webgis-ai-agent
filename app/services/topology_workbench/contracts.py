"""Topology Workbench（G31）—— 契约常量与封闭词表。

单一登记点：规则类型词表、角色要求、严重级、运行/问题状态、typed 错误码、
规模/时间/输出预算。所有边界在此声明并被测试钉住；调用方不得自造平行常量。

与既有质量面的分工（DEDUP_MATRIX #1–#3）：
- ``data_quality`` DSL 保留单源规则；本模块的规则是**多源角色绑定**的
  业务拓扑约束，词表独立、互不复用判定函数；
- ``spatial_quality_service`` 的样例审计保留；本工作台在权威快照上运行。
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Tuple

# ─── 规则类型词表（封闭；新增必须同步 evaluators.EVALUATORS） ────────────

RULE_TYPE_POLYGONS_NO_OVERLAP = "polygons_no_overlap"
RULE_TYPE_AREA_COVERAGE = "area_coverage"
RULE_TYPE_OBJECTS_COVERED_BY_TARGET = "objects_covered_by_target"
RULE_TYPE_CROSS_LAYER_NO_OVERLAP = "cross_layer_no_overlap"
RULE_TYPE_LINE_ENDPOINTS_CONNECTED = "line_endpoints_connected"

RULE_TYPES: Tuple[str, ...] = (
    RULE_TYPE_POLYGONS_NO_OVERLAP,
    RULE_TYPE_AREA_COVERAGE,
    RULE_TYPE_OBJECTS_COVERED_BY_TARGET,
    RULE_TYPE_CROSS_LAYER_NO_OVERLAP,
    RULE_TYPE_LINE_ENDPOINTS_CONNECTED,
)

SEVERITIES: Tuple[str, ...] = ("info", "warn", "error")

#: 每类规则的源角色需求（角色名封闭）：
#:   required —— 必须绑定；optional —— 可选；complete —— 该角色要求**全量**
#:   快照（携带 scope 的绑定会被 typed 拒绝 —— 覆盖/包含/连接语义下局部
#:   选择会产生虚假缺口/虚假越界，任务书 T12）。
RULE_ROLE_SPECS: Dict[str, Dict[str, str]] = {
    RULE_TYPE_POLYGONS_NO_OVERLAP: {"checked": "required+scoped"},
    RULE_TYPE_AREA_COVERAGE: {
        "coverage": "required+complete",
        "aoi": "required+complete",
        "exclusions": "optional+complete",
    },
    RULE_TYPE_OBJECTS_COVERED_BY_TARGET: {
        "checked": "required+scoped",
        "target": "required+complete",
    },
    RULE_TYPE_CROSS_LAYER_NO_OVERLAP: {
        "source_a": "required+scoped",
        "source_b": "required+scoped",
    },
    RULE_TYPE_LINE_ENDPOINTS_CONNECTED: {
        "checked": "required+scoped",
        "connectors": "required+complete",
        "exit_zones": "optional+complete",
    },
}

#: 边界包含政策（objects_covered_by_target）：
#:   covered —— 与目标并集的并包含边界即可（默认，贴边合法）；
#:   within  —— 必须严格位于目标内部（贴边违规）。
BOUNDARY_POLICIES: FrozenSet[str] = frozenset({"covered", "within"})

#: 连接对象允许类型（line_endpoints_connected 的 connectors 角色）。
CONNECTOR_KINDS: FrozenSet[str] = frozenset({"points", "lines", "points+lines"})

# ─── 绑定与范围（源快照解析的封闭词表） ────────────────────────────────

BINDING_KINDS: FrozenSet[str] = frozenset({"session_ref", "project_artifact", "bbox"})

#: bbox 绑定仅允许用于 ``area_coverage`` 的 aoi 角色（显式矩形 AOI）。
BBOX_BINDABLE_ROLES: FrozenSet[str] = frozenset({"aoi"})

#: scope（对 checked/source_a/source_b 这类 scoped 角色可选的范围限定）。
#: 词表与 G24 selection_handles 的 scope 封闭词表同源（bbox/where/ids）。
SCOPE_KINDS: FrozenSet[str] = frozenset({"bbox", "where", "ids"})

#: bbox scope 的语义固定为粗超集相交（与 selection_handles 同语义并披露）。
BBOX_SCOPE_SEMANTICS = "bbox_intersects_coarse"

# ─── 运行/规则结果状态 ────────────────────────────────────────────────

#: run.status（作业层生命周期）
RUN_STATUSES: FrozenSet[str] = frozenset(
    {"pending", "running", "completed", "failed", "cancelled"}
)

#: 逐规则结果的封闭维度（运行状态与质量结论分离，任务书 §六）：
#:   applicability        applicable | blocked（几何类型/CRS/范围不支持）
#:   input_completeness   full | partial（scope/预算截断）
#:   computation          full | partial（候选/finding/时间预算触顶）
#:   outcome              violations_found | no_violations | unknown
RULE_RESULT_STATUSES: FrozenSet[str] = frozenset(
    {"applicable", "blocked"}
)
RULE_INPUT_COMPLETENESS: FrozenSet[str] = frozenset({"full", "partial"})
RULE_COMPUTATION_COMPLETENESS: FrozenSet[str] = frozenset({"full", "partial"})
RULE_OUTCOMES: FrozenSet[str] = frozenset(
    {"violations_found", "no_violations", "unknown"}
)

# ─── finding 状态（处理状态，与检测事实分离） ──────────────────────────

FINDING_STATUSES: FrozenSet[str] = frozenset(
    {"open", "accepted_exception", "resolved", "dismissed"}
)

# ─── 例外记录状态 ─────────────────────────────────────────────────────

EXCEPTION_STATUSES: FrozenSet[str] = frozenset({"active", "revoked", "stale"})

# ─── typed 错误码（REST 4xx / 工具失败面共用；fail-closed） ─────────────

ERROR_CODES: FrozenSet[str] = frozenset({
    "unknown_rule_type",          # 规则类型不在词表
    "unknown_severity",
    "invalid_params",             # 参数形状/类型非法（负容差、NaN、未知键）
    "missing_role",               # 规则必需角色未绑定
    "unknown_role",               # 规则词表外的角色名
    "scope_not_allowed",          # complete 角色携带 scope（T12）
    "invalid_binding",            # 绑定形状非法
    "source_unresolvable",        # 源不可读（过期 ref / 无修订 / 无权）
    "source_unauthorized",        # 鉴权拒绝（不泄露存在性由路由层 404 处理）
    "crs_unsupported",            # CRS 无法解析/转换
    "region_unsupported",         # 反经线 / 极区 / 超大范围 → typed blocked
    "geometry_type_mismatch",     # 角色几何类型不符（如 checked 非面）
    "budget_exceeded",            # 提交期即可判定的超限（要素数）
    "invalid_scope",
    "rule_limit_exceeded",
    "invalid_locator",
    "finding_not_found",
    "exception_invalid_reason",
    "exception_stale",
    "repair_unsupported",         # 该 finding 类型无确定性修复
    "plan_build_failed",
    "not_owned",                  # 资源非本人（路由层转 404）
})

# ─── 预算（有界纪律：宁可 partial 也绝不无界） ──────────────────────────

MAX_RULES_PER_SET = 32                 # 每规则集规则数上限
MAX_ROLES_PER_RULE = 4                 # 单规则角色数（词表已约束）
MAX_FEATURES_PER_SNAPSHOT = 50_000     # 单角色快照要素上限（提交期预算拒绝）
MAX_CANDIDATE_PAIRS_PER_RULE = 200_000  # 空间索引候选对上限（超出→partial）
MAX_FINDINGS_PER_RULE = 5_000          # 单规则 finding 上限（超出→partial+计数）
MAX_VERTICES_PER_GEOMETRY = 100_000    # 单几何顶点上限（超出→该要素剔除+披露）
MAX_WITNESS_BLOB_BYTES = 8 * 1024 * 1024   # 单运行 witness 集合字节上限
RULE_WALL_CLOCK_S = 120.0              # 单规则 wall-clock 预算（超出→partial）
#: 复用键必须覆盖的完整输入身份（缓存复用纪律，任务书 §六）：
#: 全部源内容指纹 + 规则 digest + scope + 计算 CRS + 方法版本。
METHOD_VERSION = "g31-eval-v1"
#: 复用键成分（computation_crs 为源内容纯函数，由内容指纹内蕴，不单列）
RUN_REUSE_KEY_FIELDS = ("input_digest", "ruleset_digest", "method_version")

#: 数值护栏：容差/阈值的量级边界（提交期校验，防呆但不猜业务）。
MAX_LINEAR_TOLERANCE_M = 500.0
MAX_AREA_THRESHOLD_M2 = 1e9

# ─── locator（快照内身份，与 FEATURE_ID_KEYS 同源链） ───────────────────

#: locator 形状（存 findings.locators）：{
#:   "role": 角色名, "source_kind": 绑定类型, "ref": 源标识,
#:   "content_revision": 快照 revision, "fingerprint": 快照内容指纹,
#:   "identity": {"kind": "wfid"|"natural_id"|"snapshot_index",
#:                "value": str, "snapshot_fingerprint": str(仅 index)},
#:   "feature_index": int
#: }
#: 快照 index locator 仅在该快照指纹下有效（T15：revision 前进 → stale）。
LOCATOR_IDENTITY_KINDS: FrozenSet[str] = frozenset(
    {"wfid", "natural_id", "snapshot_index"}
)

# ─── witness 存储形状 ──────────────────────────────────────────────────

#: 每运行的 witness 集合是单个 GeoJSON FeatureCollection blob（BlobStore
#: 内容寻址）；finding 行只存 key + sha256 + bbox + 分量索引。
WITNESS_GEOJSON_PRECISION = 7      # 坐标小数位（度；≈1cm 级，定位足够）
WITNESS_BLOB_PREFIX = "g31witness"


def typed_error(code: str, message: str, **detail: Any) -> Dict[str, Any]:
    """构造有界 typed 错误（code 必须来自封闭词表 —— fail-closed）。"""
    if code not in ERROR_CODES:
        raise ValueError(f"unknown error code: {code}")
    out: Dict[str, Any] = {"code": code, "message": str(message)[:300]}
    if detail:
        out["detail"] = {k: v for k, v in list(detail.items())[:8]}
    return out


__all__ = [
    "RULE_TYPES", "RULE_ROLE_SPECS", "SEVERITIES", "BOUNDARY_POLICIES",
    "CONNECTOR_KINDS", "BINDING_KINDS", "BBOX_BINDABLE_ROLES", "SCOPE_KINDS",
    "BBOX_SCOPE_SEMANTICS", "RUN_STATUSES", "RULE_RESULT_STATUSES",
    "RULE_INPUT_COMPLETENESS", "RULE_COMPUTATION_COMPLETENESS",
    "RULE_OUTCOMES", "FINDING_STATUSES", "EXCEPTION_STATUSES",
    "ERROR_CODES", "MAX_RULES_PER_SET", "MAX_ROLES_PER_RULE",
    "MAX_FEATURES_PER_SNAPSHOT", "MAX_CANDIDATE_PAIRS_PER_RULE",
    "MAX_FINDINGS_PER_RULE", "MAX_VERTICES_PER_GEOMETRY",
    "MAX_WITNESS_BLOB_BYTES", "RULE_WALL_CLOCK_S", "METHOD_VERSION",
    "RUN_REUSE_KEY_FIELDS", "MAX_LINEAR_TOLERANCE_M", "MAX_AREA_THRESHOLD_M2",
    "LOCATOR_IDENTITY_KINDS", "WITNESS_GEOJSON_PRECISION",
    "WITNESS_BLOB_PREFIX", "typed_error",
]
