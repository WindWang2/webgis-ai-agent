"""Feature-edit 契约常量（/goal 03，ADR 见 docs/adr/）。

单一登记点：编辑词表、finding codes、规模/时间边界、journal/plan 持久键。
所有边界在这里声明并被测试钉住；调用方不得自造平行的第二套常量。
"""
from __future__ import annotations

from app.services.feature_edit.wire_types import EditFinding

from typing import Any

# ─── 持久键（map_state 有界键，随会话生命周期，写方必须持会话锁） ──────────
JOURNALS_STATE_KEY = "_feature_edit_journals"   # {ref_id: journal_dict}
PLANS_STATE_KEY = "_feature_edit_plans"         # {plan_id: plan_dict}
POLICY_STATE_KEY = "_feature_edit_policy"       # {"mode": ...}

# ─── 边界（有界纪律：宁可拒绝也不无界增长） ────────────────────────────────
MAX_EDITABLE_FEATURES = 20_000      # 数据集规模门（> 拒绝 dataset_too_large_to_edit）
MAX_PLAN_TARGETS = 200              # 预览目标要素上限
MAX_SAFE_COMMIT_TARGETS = 50        # 免确认提交的目标要素上限
MAX_OPS_PER_PLAN = 8                # 单 plan 操作数上限
MAX_UNDO_HISTORY = 128              # 每 journal 条目 FIFO 上限
MAX_JOURNALS_PER_SESSION = 4        # 每会话活跃 journal 数（LRU 逐出最久未写）
JOURNAL_BYTE_BUDGET = 64 * 1024     # 每 journal 序列化字节预算（超预算丢最旧）
MAX_ACTIVE_PLANS = 8                # 并发 pending plan 上限
PLAN_TTL_S = 600                    # plan 生存期（10 分钟）
MAX_RENAME_LENGTH = 64              # 字段名长度上限
MAX_MOVE_METERS = 10_000.0          # move_by 单次位移防呆上限（10km）
MAX_SNAP_TOLERANCE_M = 500.0        # snap 容差上限
# R1 P2-2：几何 plan 目标数收紧 —— journal 单条需内嵌 before/after 全量几何
# （undo 保真），20×2048 顶点会击穿 64KB 预算；10×1024 上限 ≈ 250KB 最坏
# 单条（append_entry 的字节闸保底 ≥1 条，预算语义见 ADR）。
MAX_GEOMETRY_TARGETS = 10
# G14：结构性编辑（增/删/分割/合并）的规模门 —— journal 单条需内嵌出生/
# 消失要素的完整快照（undo 保真），上限与字节预算（下方）联合保证有界。
MAX_CREATED_PER_PLAN = 50           # create_features 单 plan 新增要素上限
MAX_DELETED_PER_PLAN = 50           # delete_features 单 plan 删除要素上限
MAX_MERGE_PARENTS = 10              # merge_features 单次合并父要素上限
MAX_JOURNAL_ENTRY_BYTES = 256 * 1024  # 单 journal entry 序列化字节上限（entry_too_large）

# ─── 稳定要素身份 ──────────────────────────────────────────────────────────
WFID_PROP = "_wfid"                 # 物化稳定 id 的 properties 键（系统保留）
RESERVED_FIELD_NAMES = frozenset({WFID_PROP})

# 与前后端既有 FEATURE_ID_KEYS 链同源（backend layer.py `_find_feature_by_id`
# ≡ frontend layer-data.ts FEATURE_ID_KEYS）；物化前的目标解析走这条链。
# G24/P0：`_wfid` 置于链首（与 feature_pages / 前端三侧同步）—— 编辑创建
# 的要素仅有 _wfid，缺位会让链内解析在这些要素上失配。WFID_PROP 本身即
# 链首键，物化前后语义连续（identity.resolve_feature_ids 的 WFID 寻址
# 分支仍优先，行为不变）。
FEATURE_ID_KEYS: tuple = (WFID_PROP, "id", "OBJECTID", "fid", "osm_id", "@id", "featureId", "feature_id")

# ─── 编辑词表（封闭集；schema 层拒绝未知 op） ─────────────────────────────
ATTRIBUTE_OPS = frozenset({"set_fields", "rename_field"})
GEOMETRY_OPS = frozenset({"repair_geometry", "move_by", "snap_to_ref"})
# G14 结构性操作：改变要素集合成员关系（出生/消失/血缘）。恒不 safe ——
# 一律需要用户确认（提案卡/owner REST），与坐标变更同一信任边界。
STRUCTURAL_OPS = frozenset(
    {"create_features", "delete_features", "replace_geometry", "split_feature", "merge_features"}
)
EDIT_OPS = ATTRIBUTE_OPS | GEOMETRY_OPS | STRUCTURAL_OPS

REPAIR_FIXES = frozenset({"make_valid", "close_ring", "dedupe_vertices"})

# policy 模式（owner 经 REST 设置；模型/工具不可写）
POLICY_MODES = frozenset({"confirm_all", "allow_safe_attribute"})
DEFAULT_POLICY_MODE = "confirm_all"

# plan 状态机
PLAN_STATUSES = frozenset({"pending", "applied", "rejected", "expired", "conflicted"})

# journal entry kind
# 物化事实记在 apply entry 的 op_summary.materialized（R1 P3-5：不单列 kind）
ENTRY_KINDS = frozenset({"apply", "undo", "redo"})

# ─── finding codes（封闭集：preview/commit 的否定性结果一律从这里取） ──────
FINDING_CODES = frozenset({
    "enum_violation",            # 字段 distinct 词表外的值
    "type_mismatch",             # 值类型与字段既有类型不符
    "unknown_field",             # set_fields 目标字段不存在
    "feature_not_found",         # 目标要素在当前 revision 不存在
    "fingerprint_mismatch",      # plan 绑定的指纹与当前载荷不符（他人已改）
    "dataset_not_editable",      # 非 ref-backed geojson 数据集（inline/raster/fabric）
    "dataset_too_large_to_edit", # 超规模门
    "dataset_read_only",         # 数据源声明只读（保留码）
    "no_snap_candidate",         # 容差内无吸附候选
    "unit_required",             # 位移/容差缺单位（本词表单位恒米，缺数值即拒绝）
    "crs_assumption_required",   # 需要显式 CRS 假设而未提供（防御：数据恒 4326）
    "geometry_invalid_after",    # 操作后几何非法
    "op_limit_exceeded",         # 操作数超上限
    "target_limit_exceeded",     # 目标要素数超上限
    "invalid_field_name",        # 字段名非法/保留/过长
    "reserved_field",            # 试图写入保留字段（_wfid）
    "invalid_op",                # 未知/畸形操作
    "geometry_too_complex",      # 几何顶点数超编辑上限（diff/undo 保真不可承受）
    "invalid_target",            # 目标解析失败（无 selection 且无显式 ids）
    "undo_unavailable",          # 无可撤销条目或链已被 FIFO 丢弃
    "plan_expired",              # plan TTL 已过
    "plan_state_invalid",        # plan 状态机非法迁移
    "policy_denied",             # policy 拒绝（非 safe 而走自动通道）
    # ── G14 结构性编辑 finding codes ──
    "entry_too_large",           # 单 journal entry 序列化超预算（undo 快照不可承受）
    "geometry_type_mismatch",    # replace_geometry/split/merge 的几何类型与数据集/目标不符
    "split_not_dividing",        # 分割线未真正贯穿目标几何（<2 部分）
    "merge_disjoint",            # 合并结果非单一部分（要素不相邻/不相交）
    "merge_type_mixed",          # 合并目标几何基类型不一致
    "topology_violation",        # 声明的拓扑规则被违反（带定位与受影响对）
    "reserved_id",               # 试图在 properties 里提供保留身份字段（_wfid）
    "invalid_geometry",          # 输入几何畸形（无法解析/空坐标）
    "merge_conflict",            # field_policy=require_identical 时字段值不一致
    "dataset_read_only_source",  # 源只读，需先派生编辑副本
})

# ─── 数据集级拓扑声明（envelope "topology" 键；G14 provisioning 写入） ─────
TOPOLOGY_RULES = frozenset({"no_overlap", "no_self_intersection", "no_duplicate_points"})
# no_overlap 判定容差：线性米；重叠面积超过 tolerance_m² 才算违规（米制经
# Geod 在要素局部纬度换算为平方度比较）。
DEFAULT_TOPOLOGY_TOLERANCE_M = 1.0


def finding(code: str, message: str, **detail: Any) -> EditFinding:
    """构造一个有界 finding（code 必须来自封闭集 —— fail-closed）。"""
    if code not in FINDING_CODES:
        raise ValueError(f"unknown finding code: {code}")
    out: EditFinding = {"code": code, "message": message[:200]}
    if detail:
        out["detail"] = {k: v for k, v in list(detail.items())[:8]}
    return out


# ─── 类型判定（属性类型校验的既集类型面） ─────────────────────────────────
_FIELD_TYPE_NAMES = frozenset({"string", "number", "boolean", "null"})


def json_field_type(value: Any) -> str:
    """值 → 集合类型名（GeoJSON 属性面只认 string/number/boolean/null）。"""
    if value is None:
        return "null"
    if isinstance(value, bool):        # bool 是 int 子类，先判
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "unsupported"               # list/dict 不进编辑词表（拒绝 type_mismatch）
