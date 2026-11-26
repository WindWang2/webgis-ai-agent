"""Data Fusion Authoring —— 多源空间实体融合工作台 · 封闭词表与预算（单一事实源）。

域定位（与 G38 data_join_authoring 的职责边界）：
- G38 关联 = 以左表为骨架的**属性富集**（left/inner join，不做实体消歧，
  一行进一行出，不产出融合成果版本）；
- 本域 = 对**同一地理实体的多来源观测**建立可证明的匹配、冲突、合并、
  质量与审阅语义，产出不可变融合数据集版本（ref:data-fusion/<run_id>）。

本模块是域内所有词表、typed 错误码与预算常量的唯一登记处；
API 路由与 Pi 工具共用 ``app.services.data_fusion_authoring.service``
（单一编排面），词表漂移由单元测试锁定。

设计原则（与 G38/G31 同款，另加融合域特有红线）：
- 词表封闭、错误码 fail-closed（未知码在构造时即抛错）；
- 预算先估算后强制（每阶段真实计数，绝不以前缀截断冒充完成）；
- **空间邻近 ≠ 实体同一**：距离只是证据之一，任何单一证据不得越过
  歧义保护自动合并；
- **缺数据 ≠ 零值**：缺失按 missing 语义记录，绝不静默补 0/补空串；
- **冲突不可被静默覆盖**：matched 对上的字段/几何冲突保留多来源值与
  审阅决策，采纳必须可追溯到规则或人工决定；
- 来源信任是计划内**带理由的声明**（priority order），不是绝对正确性。
"""
from __future__ import annotations

#: 融合计划契约版本（plan JSON 中的 schema_version）。
FUSION_PLAN_SCHEMA_VERSION = 1

#: 匹配/裁决方法版本（语义变化时前进 —— 收据与幂等复用据此判旧）。
METHOD_VERSION = 1

# ─────────────────────────── 封闭词表 ───────────────────────────

#: 实体种类：
#:   point_poi —— 点状 POI 实体（学校/医院等）：完整匹配+合并语义；
#:   area_link —— 面（行政区/地块）：以标识+重叠做**配对关联**，
#:                几何采纳走声明政策（绝不自动 dissolve）；
#:   line_link —— 线（道路段等）：标识+重叠配对关联，同 area_link。
#: 后两类本版 = 「仅关联不合并几何」：输出保留被采纳侧几何，其余侧
#: 几何进 provenance；无法可靠配对的几何一律保留原样，不强拆/合并。
ENTITY_KINDS = frozenset({"point_poi", "area_link", "line_link"})

#: 几何采纳政策（配对融合后的输出几何来源）：
#:   primary_source —— 计划声明的优先来源（source_a 优先级高于 source_b
#:                     时即 a）；点实体同时以两源距离记录几何偏差证据；
#:   newest_temporal —— 依时间字段最新一侧（须声明 temporal_field，
#:                      缺失按 unresolved 进入审阅）；
#:   manual —— 逐对人工决定（decision.geometry_side ∈ {a,b}）。
GEOMETRY_ADOPTIONS = frozenset({"primary_source", "newest_temporal", "manual"})

#: 名称归一化变换闭合集（显式、计入 digest、绝不自动清洗）：
#:   trim —— 去首尾空白；casefold —— 大小写归一；
#:   collapse_spaces —— 连续空白折叠为单个空格；
#:   strip_punctuation —— 去除标点（Unicode P 类）；
#:   remove_bracketed —— 去除括号内内容（含全半角括号）。
NAME_NORMALIZERS = frozenset({
    "trim", "casefold", "collapse_spaces", "strip_punctuation", "remove_bracketed",
})

#: 证据种类（评分权重必须覆盖恰好这些键；0 权重允许显式声明）：
#:   external_id —— 映射唯一标识精确匹配（硬证据）；
#:   name —— 归一化名称相似度；
#:   address —— 归一化地址相似度（可选字段，缺字段=证据缺失）；
#:   category —— 类别一致性（词表映射后）；
#:   distance —— 空间距离得分（ point_poi 必须 >0；area/line 用重叠率）。
EVIDENCE_KINDS = frozenset({
    "external_id", "name", "address", "category", "distance",
})

#: 类别比对结果：
#:   equal —— 各自经 category_map 映射到同一规范词表值；
#:   different —— 均可映射但规范值不同；
#:   unknown —— 一侧或两侧缺失/不在词表（诚实计 unknown，
#:               绝不折算成 different 或 equal）。
CATEGORY_MATCH_VALUES = frozenset({"equal", "different", "unknown"})

#: 候选对状态：
#:   matched —— 自动合并（≥auto_match_min 且无歧义/无硬冲突）；
#:   needs_review —— 进入审阅带（分值/冲突/歧义任一触发）；
#:   ambiguous —— top-2 差距 < ambiguous_margin 或一对多（不自动合并）；
#:   unmatched —— 双侧均无足够证据（不配对，输出保留单源要素）；
#:   rejected —— 人工否决的候选（不合并；同键重跑保持否决）。
PAIR_STATUSES = frozenset({
    "matched", "needs_review", "ambiguous", "unmatched", "rejected",
})

#: 字段冲突判定（配对内逐映射字段）：
#:   identical —— 归一化后同值；
#:   conflict —— 两侧均有值且归一化后不同（保留双值+审阅决策）；
#:   missing_a / missing_b —— 单侧缺失（缺数据 ≠ 零值）；
#:   missing_both —— 双侧缺失（输出字段置缺失，不编造）。
FIELD_COMPARISONS = frozenset({
    "identical", "conflict", "missing_a", "missing_b", "missing_both",
})

#: 字段裁决规则（确定性、封闭；Agent 仅可建议，写入按授权）：
#:   same_value —— 归一化同值 → 保留单值（来源并列记录）；
#:   trust_priority —— 按计划来源优先序取先（冲突保留他源值于 provenance）；
#:   newest_temporal —— 依时间字段最新一侧取值（须声明 temporal_field；
#:                       时间缺失 → unresolved，绝不静默回退优先序）；
#:   keep_multi —— 保留多值（有序去重列表；类别/别名场景）；
#:   manual —— 人工决定（review decision 显式给值）。
#: 未声明规则的字段冲突 → unresolved（进审阅，不猜）。
ADJUDICATION_RULES = frozenset({
    "same_value", "trust_priority", "newest_temporal", "keep_multi", "manual",
})

#: 裁决结果：
#:   resolved_rule —— 规则确定（唯一确定值）；resolved_manual —— 人工决定；
#:   unresolved —— 无法自动确定（值保持缺失+进审阅队列）。
ADJUDICATION_OUTCOMES = frozenset({"resolved_rule", "resolved_manual", "unresolved"})

#: 运行状态（与 durable job 终态对齐）。
RUN_STATUSES = frozenset({"pending", "running", "completed", "failed", "cancelled"})

#: 计划状态。
PLAN_STATUSES = frozenset({"draft", "archived"})

#: 提交政策（run 完成后组装融合成果版本的门）：
#:   allow_unresolved —— 未决审阅项以单源要素输出并披露（质量报告计数）；
#:   require_resolved —— 存在未决项即 typed 阻断提交。
COMMIT_POLICIES = frozenset({"allow_unresolved", "require_resolved"})

#: 输出要素来源标记（`_fusion.origin`）：
#:   merged —— 配对/簇融合产物；
#:   single_a / single_b —— 未配对单源要素（真实实体，保留）；
#:   withheld_review —— 配对处于未决审阅，两侧要素暂按单源输出并披露。
OUTPUT_ORIGINS = frozenset({"merged", "single_a", "single_b", "withheld_review"})

#: 非法几何政策：exclude_count（排除并计数披露）/ block（typed 阻断）。
#: 绝不静默 make_valid（与 G38 同款语义）。
INVALID_GEOMETRY_POLICIES = frozenset({"exclude_count", "block"})

# ─────────────────────────── typed 错误码 ───────────────────────────

ERROR_CODES = frozenset({
    "invalid_plan",              # 计划结构/词表违规
    "unsupported_feature",       # 本版显式不支持的能力（typed 披露）
    "field_not_found",           # 引用了源中不存在的字段
    "field_mapping_conflict",    # 输出字段重名 / 保留字段被覆盖
    "type_conflict",             # 映射字段两侧类型不可比
    "weights_invalid",           # 证据权重非法（覆盖不全/不归一/为负）
    "threshold_invalid",         # 阈值关系非法（review_min > auto_min 等）
    "budget_exceeded",           # 行/对/字节/时长超预算（估算或运行时）
    "stale_source",              # 执行前源内容/版本漂移（预览失效）
    "source_unresolvable",       # 源锚解析/授权失败（包装 G18 typed 码）
    "source_geometry_missing",   # 需要几何的实体种类但源缺几何
    "crs_unresolvable",          # 未知投影/不可信坐标帧/两侧 CRS 不可比
    "invalid_geometry",          # 非法几何按政策阻断
    "entity_kind_unsupported",   # 源几何形态与声明实体种类不符
    "empty_result",              # 契约化空结果（非失败；计数可对账）
    "not_owned",                 # 越权访问他人计划/运行（404 语义）
    "stale_plan_revision",       # 计划 CAS 冲突
    "stale_review_revision",     # 审阅决定 CAS 冲突
    "run_not_found",
    "decision_not_found",        # 审阅对象（pair/字段）不存在或不可决
    "receipt_missing",           # 终态但收据缺失/内容不可读（typed 失效）
    "run_not_active",            # 对非活跃运行执行取消
    "result_unavailable",        # 结果尚未发布/不可分页
    "commit_blocked",            # require_resolved 政策下仍有未决项
    "refresh_not_safe",          # 增量刷新不安全（变更面过大 → 全量重算）
    "render_ref_over_budget",    # 输出超过渲染投影上限（数据本体仍可交付）
    "internal_error",
})

# ─────────────────────────── 预算（初始值；可被计划内更严格值覆盖） ───────────────────────────
#
# 约定：所有上限「执行前估算、执行中强制」；任何阶段超限 → typed
# budget_exceeded（绝不截断前缀后宣称完成）。与仓库既有闸取更严者。

#: 单源行数上限（两侧独立计）。
DEFAULT_MAX_SOURCE_ROWS = 50_000

#: 候选对总数上限（blocking 后去重对数；分配前估算，运行中强制）。
DEFAULT_MAX_CANDIDATE_PAIRS = 1_000_000

#: 单实体候选上限（超出 = 高密集区；溢出实体强制 needs_review 并披露）。
DEFAULT_MAX_CANDIDATES_PER_ENTITY = 50

#: 最终输出要素上限。
DEFAULT_MAX_OUTPUT_FEATURES = 100_000

#: 临时内存预算（估计值；超限 typed 拒绝）。
DEFAULT_TEMP_BYTES_BUDGET = 256 * 1024 * 1024

#: 执行时长上限（秒）。
DEFAULT_DEADLINE_S = 300.0

#: 预览/诊断样本上限（每侧/每类）。
DIAGNOSTIC_SAMPLE_LIMIT = 50

#: 审阅队列分页：单页上限/默认。
REVIEW_PAGE_MAX_ITEMS = 200
REVIEW_PAGE_DEFAULT_ITEMS = 50

#: 结果分页：单页上限/默认。
RESULT_PAGE_MAX_ROWS = 200
RESULT_PAGE_DEFAULT_ROWS = 50

#: 每字段样本值上限（诊断卡）。
DIAG_FIELD_SAMPLE_LIMIT = 3

#: 输出字段数上限（含 provenance/保留字段）。
MAX_OUTPUT_FIELDS = 256

#: 渲染投影（session FC ref）要素上限 —— 与 G24 MAX_MATERIALIZE_FEATURES
#: 同口径；超过时「加入地图」typed 披露 render_ref_over_budget，
#: 数据本体（GeoJSON 包 + DataObject）不受影响。
RENDER_REF_MAX_FEATURES = 10_000

#: 计划 JSON 字节上限（与 G38 同款 ≤32KB）。
MAX_PLAN_BLOB_BYTES = 32 * 1024

#: 预览证据 JSON 字节上限（行内投影）。
MAX_PREVIEW_BLOB_BYTES = 8 * 1024

#: 计划内来源数：v1 固定 pairwise（2 源）；多源用「融合成果作为下一
#: 计划的源」组合（诚实组合语义，不装作 N 路融合）。
SOURCES_PER_PLAN = 2

#: 增量刷新安全阈：受影响实体占比超过该值 → typed refresh_not_safe
#:（要求全量重算），绝不静默放大变更面。
INCREMENTAL_AFFECTED_RATIO_CAP = 0.35

# ── 硬顶（计划内预算不可越过；与仓库既有闸同向） ──
HARD_MAX_SOURCE_ROWS = 200_000
HARD_MAX_CANDIDATE_PAIRS = 4_000_000
HARD_MAX_OUTPUT_FEATURES = 200_000
HARD_MAX_TEMP_BYTES = 1024 * 1024 * 1024
HARD_MAX_DEADLINE_S = 900.0

#: 保留字段前缀：融合 provenance 全部写入 ``_fusion`` 成员（数据集字段
#: 名不受污染；导出交付时随包说明）。源字段不得以该前缀命名。
PROVENANCE_KEY = "_fusion"

#: 保留源字段名（计划映射不得覆盖源自身字段名；输出字段以映射别名为准）。
RESERVED_SOURCE_FIELD = PROVENANCE_KEY
