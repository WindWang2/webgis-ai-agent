"""G44 域常量 —— 单一登记点（边界被测试钉住；调用方不自造第二套）。"""
from __future__ import annotations

# ─── 合同规模门（有界纪律）────────────────────────────────────────────────
MAX_FIELDS_PER_CONTRACT = 64          # 字段规则组上限
MAX_CROSS_RULES = 64                  # 跨字段/唯一性规则总数上限
MAX_ENUM_VALUES = 256                 # 单枚举值数上限
MAX_ENUM_TOTAL_BYTES = 32 * 1024      # 全部枚举串总字节闸
MAX_RULES_JSON_BYTES = 64 * 1024      # 合同 rules JSON 序列化上限
MAX_LABEL_LEN = 64                    # 字段标签/说明片段长度上限
MAX_FIELD_NAME_LEN = 64               # 与 feature_edit.MAX_RENAME_LENGTH 对齐
MAX_CONTRACTS_PER_BINDING = 32        # 每 (session, ref) 保留的合同组行数上限（历史裁剪披露）
MAX_UNIQUE_FIELDS = 4                 # 组合唯一键字段数上限
MAX_DATETIME_FRACTION_DIGITS = 6      # datetime 小数秒精度上限（超出拒绝，不截断）
# 完整评估：物化 finding 条数上限（溢出只计数并披露，不丢总数）
MAX_MATERIALIZED_FINDINGS = 2_000
# 评估必须覆盖的要素数上限 —— 与 feature_edit.MAX_EDITABLE_FEATURES 同源；
# 超限数据集不可激活强执行合同（样本不可冒充完整）。
MAX_ASSESSABLE_FEATURES = 20_000
# 单条 finding 的 value 投影长度（回执有界）
FINDING_VALUE_PROJECTION = 64
# 评估分页
FINDINGS_PAGE_DEFAULT = 20
FINDINGS_PAGE_MAX = 100

# ─── 状态机 ────────────────────────────────────────────────────────────────
CONTRACT_STATUSES = frozenset({"draft", "active", "retired", "superseded"})
ASSESSMENT_STATUSES = frozenset(
    {"queued", "running", "completed", "cancelled", "failed", "incomplete"}
)
#: 评估收据可用于激活的充分条件之一：status=completed（另需零违反 + 指纹复核）
ACTIVATABLE_ASSESSMENT_STATUS = "completed"

# ─── 严格标量类型词表 ─────────────────────────────────────────────────────
FIELD_TYPE_NAMES = frozenset(
    {"string", "number", "integer", "boolean", "date", "datetime"}
)
#: 可参与有序比较（compare 规则）的类型 —— 同类型才可比
ORDERED_COMPARABLE = frozenset({"string", "number", "integer", "date", "datetime"})

# ─── presence / null / empty-string 三态政策 ─────────────────────────────
EMPTY_STRING_POLICIES = frozenset({"reject", "allow"})
#: 唯一键 null/missing 政策：ignore（含 null/缺失键的行不参与唯一性）| participate
#: （null≡缺失归一为 None 参与比较：两行同 null 键即冲突）
UNIQUE_NULL_POLICIES = frozenset({"ignore", "participate"})

# ─── 跨字段封闭规则词表 ──────────────────────────────────────────────────
CONDITION_OPERATORS = frozenset({"eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte"})
COMPARE_OPERATORS = frozenset({"lt", "lte", "gt", "gte"})

# ─── 违规类别（封闭集；validator 输出只从这里取）─────────────────────────
VIOLATION_CODES = frozenset({
    "type_mismatch",           # 值类型与声明不符（bool≠number；string "1" ≠ number）
    "required_missing",        # required 字段缺键
    "null_not_allowed",        # nullable=false 而值为 null
    "empty_string_rejected",   # empty_string=reject 而值为 ""
    "enum_violation",          # 声明枚举外
    "number_range",            # 数值区间（闭/开）
    "non_finite_number",       # NaN/Inf
    "not_integer",             # integer 声明但非整数
    "string_length",           # 长度（码点）
    "invalid_date",            # 非 ISO 日期 / 闰年等日历错误
    "invalid_datetime",        # 非 ISO datetime
    "datetime_offset_required",  # 无显式 offset
    "datetime_precision",      # 小数秒精度超出支持（拒绝而非截断）
    "unique_conflict",         # 组合唯一键冲突
    "conditional_required",    # 条件必填未满足
    "compare_violation",       # 同类型有序比较失败
    "unknown_rule_field",      # 规则引用的字段未在字段规则组声明（合同自身错误）
    "rule_not_evaluable",      # 规则不可求值（类型面缺失等），确定性失败
})

# ─── 执行链 finding codes（并入 feature_edit.FINDING_CODES 的封闭集）─────
EDIT_FINDING_CONTRACT_CODES = frozenset({
    "contract_violation",        # 候选态/恢复态违反 active 合同（detail 携带违规分页引用）
    "contract_state_unavailable",  # 合同 DB 不可读 —— fail-closed，不盲写
})

# ─── 激活/绑定 ────────────────────────────────────────────────────────────
#: 绑定有效性判定：descriptor.content_revision ≥ 激活时 revision 视为同源演化；
#: revision 回退（ref 被替换/重置）→ binding_stale（显式披露，不随机重绑）。
BINDING_OK = "ok"
BINDING_STALE = "stale"
BINDING_MISSING = "missing"
