"""数据准备工作台词表 —— 角色 / 原因码 / 建议码的单一真相（M2 契约 §1–§3）。

设计边界（与既有四套角色词表的关系，见 planning/semantic-data-onboarding/
BASELINE.md「三套字段角色推断并存」节；本模块是增量超集，不重写既有消费者）：

- **细粒度提案角色**（PROPOSAL_ROLES）：本模块为「字段角色候选提案」定义的
  细粒度词表 —— 坐标拆 x/y/lon/lat、地址独立、分母独立。这是提案层
  （propose）的工作词表，供用户复核与准备计划（FieldMappingStep）使用；
- **descriptor 写回**：FieldEntry.roles 的值域由 SemanticFieldRole
  （app/lib/gis/semantic_profile.py）权威定义，本模块只提供
  ``to_semantic_roles`` 单向投影（诚实缺省：无对应的角色不虚构映射，
  如 address 在 SemanticFieldRole 中无对应，写回时缺席）；
- **既有推断引擎**：derive_semantic_profile / data_fabric field_roles /
  explorer auto_field_mapping 一律不改 —— 差异在 planning 文档披露。

置信分级（数值口径，与 FieldProposal.needs_clarification 联动）：
    >= 0.85  high   —— 名称 + dtype + 值域（或唯一性）多证据一致，可自动采纳
    0.5–0.85 medium —— 需要用户复核（一屏映射 UI 默认展开）
    <  0.5   不构成候选（低于提案阈值，绝不进 candidates）
"""
from __future__ import annotations

import enum
from typing import Dict, FrozenSet, Tuple

from app.lib.gis.semantic_profile import SemanticFieldRole

#: 细粒度提案角色（closed 词表；M2 契约 §1）。
PROPOSAL_ROLES: FrozenSet[str] = frozenset({
    "id",            # 主键 / 要素标识
    "name",          # 名称 / 标签
    "address",       # 地址（提案层专用；descriptor 无对应）
    "x",             # 投影/局部坐标东向
    "y",             # 投影/局部坐标北向
    "lon",           # 地理经度
    "lat",           # 地理纬度
    "time",          # 时间维度
    "category",      # 类别维度
    "value",         # 数值度量（泛值）
    "admin_code",    # 行政区划代码/名称
    "denominator",   # 归一化分母（人口/面积等）
    "weight",        # 权重
})

#: 提案角色 → SemanticFieldRole（descriptor 写回投影；缺失 = 无诚实对应）。
_TO_SEMANTIC_ROLE: Dict[str, str] = {
    "id": SemanticFieldRole.FEATURE_ID.value,
    "name": SemanticFieldRole.LABEL.value,
    # address：SemanticFieldRole 无地址角色 —— 写回缺席（提案/准备层保留）。
    "lon": SemanticFieldRole.GEOGRAPHIC_COORDINATE.value,
    "lat": SemanticFieldRole.GEOGRAPHIC_COORDINATE.value,
    "x": SemanticFieldRole.GEOGRAPHIC_COORDINATE.value,
    "y": SemanticFieldRole.GEOGRAPHIC_COORDINATE.value,
    "time": SemanticFieldRole.TEMPORAL_DIMENSION.value,
    "category": SemanticFieldRole.CATEGORY.value,
    "value": SemanticFieldRole.CONTINUOUS_MEASURE.value,
    "admin_code": SemanticFieldRole.ADMIN_DIMENSION.value,
    "denominator": SemanticFieldRole.NORMALIZATION_DENOMINATOR.value,
    "weight": SemanticFieldRole.WEIGHT_MEASURE.value,
}


def to_semantic_roles(proposal_role: str) -> Tuple[str, ...]:
    """提案角色 → SemanticFieldRole 值元组（0 或 1 个；不虚构映射）。"""
    mapped = _TO_SEMANTIC_ROLE.get(str(proposal_role))
    return (mapped,) if mapped else ()


class ReasonCode(str, enum.Enum):
    """提案原因码（稳定词表；UI 层负责双语渲染，不进业务逻辑）。"""

    NAME_EXACT = "name_exact"               # 归一化字段名与词表整词相等
    SYNONYM_ZH = "synonym_zh"               # 中文同义词命中
    SYNONYM_EN = "synonym_en"               # 英文同义词命中
    DTYPE_CONSISTENT = "dtype_consistent"   # dtype 与角色一致（数值角色+number）
    VALUE_RANGE_LAT = "value_range_lat"     # 值域 ⊆ [-90, 90]
    VALUE_RANGE_LON = "value_range_lon"     # 值域 ⊆ [-180, 180]
    RANGE_PLANE_PROJECTED = "range_plane_projected"  # 值域呈投影量级（>|180|）
    TIME_PARSED = "time_parsed"             # 时间样本/提示命中
    CATEGORY_CARDINALITY = "category_cardinality"    # 低基数字符串
    NUMERIC_MEASURE = "numeric_measure"     # 数值度量语义
    ID_UNIQUENESS = "id_uniqueness"         # 唯一性 = 行数
    USER_HINT = "user_hint"                 # 用户/Agent 提示
    RANGE_CONFLICT = "range_conflict"       # 值域与角色矛盾（被拒原因）
    AMBIGUOUS_TIE = "ambiguous_tie"         # 等证据竞争（被拒原因）


#: 双语同义词词表（role → (中文 pattern 词, 英文 pattern 词)）。
#: 纪律：中文词按「整词优先、子串从严」登记 —— 行政区划必须带「行政/区划/
#: adcode」全语义，防「编号」「学校代码」误入 admin_code（负例见测试语料）。
ROLE_SYNONYMS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "id": {
        # 裸「代码」不登记（过宽：会吞掉「行政区划代码」——admin_code 更长
        # 词优先匹配，但短词兜底命中仍是误判面，负例见测试语料）。
        "zh": ("编号", "序号", "标识", "学号"),
        "en": ("id", "uuid", "fid", "gid", "objectid", "identifier"),
    },
    "name": {
        "zh": ("名称", "名字", "标题", "学校", "校区"),
        "en": ("name", "title", "label"),
    },
    "address": {
        "zh": ("地址", "住址"),
        "en": ("address", "addr"),
    },
    "x": {
        "zh": ("东坐标", "横坐标", "x坐标"),
        "en": ("x", "easting", "utm_x", "coord_x"),
    },
    "y": {
        "zh": ("北坐标", "纵坐标", "y坐标"),
        "en": ("y", "northing", "utm_y", "coord_y"),
    },
    "lon": {
        "zh": ("经度",),
        "en": ("lon", "lng", "longitude"),
    },
    "lat": {
        "zh": ("纬度", "latitude"),
        "en": ("lat", "latitude"),
    },
    "time": {
        "zh": ("日期", "时间", "年份", "月份"),
        "en": ("date", "time", "year", "month", "datetime", "timestamp"),
    },
    "category": {
        "zh": ("类别", "类型", "分类", "等级"),
        "en": ("category", "type", "kind", "class", "grade"),
    },
    "value": {
        "zh": ("数量", "个数", "数值", "得分", "评分", "指数"),
        "en": ("value", "count", "score", "index", "amount", "total"),
    },
    "admin_code": {
        "zh": ("行政区划代码", "区划代码", "行政代码", "行政区划码", "区县代码"),
        "en": ("adcode", "admin_code", "district_code", "admin1_code"),
    },
    "denominator": {
        "zh": ("人口", "分母", "常住人口", "户籍人口"),
        "en": ("population", "denominator", "pop_total", "residents"),
    },
    "weight": {
        "zh": ("权重", "吨位"),
        "en": ("weight",),
    },
}

#: 行政区名称字段（admin 名称列，如「区县名」「所属区县」）—— 归 admin_code
#: 角色（语义等价：行政区维度；descriptor 同投影 ADMIN_DIMENSION）。
ADMIN_NAME_TERMS: Tuple[str, ...] = (
    "区县", "所属区", "所属市", "省份", "城市名", "区名", "县名",
    "province_name", "city_name", "district_name", "admin_name",
)

#: 数值型角色（dtype 必须数值，否则名称证据不成立 —— 与
#: semantic_profile._dtype_allows 同纪律）。
NUMERIC_PROPOSAL_ROLES: FrozenSet[str] = frozenset({
    "x", "y", "lon", "lat", "value", "denominator", "weight",
})

#: 字符串型角色（字符串/混合 dtype 方可，数值唯一列仍可 id）。
_STRING_PROPOSAL_ROLES: FrozenSet[str] = frozenset({
    "name", "address", "admin_code", "category", "time",
})


class SuggestionCode(str, enum.Enum):
    """修复建议码（收据 → 准备计划可执行动作的桥；closed 词表）。"""

    NONE = "none"
    SWITCH_XY = "switch_xy"                  # AxisSwapStep
    FILTER_ROWS = "filter_rows"              # FilterRowsStep（越界坐标）
    DECLARE_CRS = "declare_crs"              # 用户声明 CRS（requires_input）
    PICK_TIMEZONE = "pick_timezone"          # 声明时区（披露，不改写）
    CONFIRM_ROLE = "confirm_role"            # 字段映射复核（requires_input）
    DESIGNATE_KEY = "designate_key"          # 指定主键（PK 缺失/重复）
    PROVIDE_DENOMINATOR = "provide_denominator"  # 补分母数据集（角色级缺口）
    REPAIR_GEOMETRY = "repair_geometry"      # 走既有修复管线（quality 域）


class FindingSeverity(str, enum.Enum):
    """检查收据严重度（与 QualityReport 四态对齐：error→blocked）。"""

    OK = "ok"
    WARN = "warn"
    BLOCKED = "blocked"


#: 收据新增码（quality.py 既有码直接透传，不复制；M2 契约 §3）。
FINDING_AXIS_ORDER_SUSPECT = "axis_order_suspect"
FINDING_PK_DUPLICATED = "pk_duplicated"
FINDING_PK_MISSING = "pk_missing"
FINDING_DENOMINATOR_ABSENT = "denominator_absent"


def is_proposal_role(role: str) -> bool:
    return str(role) in PROPOSAL_ROLES
