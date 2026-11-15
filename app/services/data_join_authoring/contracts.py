"""G38 数据关联与空间富集作者工作台 —— 封闭词表与预算（单一事实源）。

本模块是域内所有词表、typed 错误码与预算常量的唯一登记处；
API 路由与 Pi 工具共用 ``app.services.data_join_authoring.service``（单一编排面），
词表漂移由单元测试 ``tests/unit/data_join_authoring/test_contract.py`` 锁定。

设计原则（与 G31 topology_workbench / G33 thematic_authoring 同款）：
- 词表封闭、错误码 fail-closed（未知码在构造时即抛错）；
- 预算先估算后强制（每阶段真实计数，绝不以前缀截断冒充完成）；
- 空键政策在进入任何连接原语之前确定（pandas merge 两侧 null 互配
  与 federation 首命中语义都不允许隐式决定本域语义）。
"""
from __future__ import annotations

#: 关联计划契约版本（plan JSON 中的 schema_version）。
JOIN_PLAN_SCHEMA_VERSION = 1

#: 执行方法版本（语义变化时前进 —— 收据与幂等复用据此判旧）。
METHOD_VERSION = 1

# ─────────────────────────── 封闭词表 ───────────────────────────

#: 关联模式：属性键关联 / 空间点面关联（本版仅 Point×Polygon/MultiPolygon）。
JOIN_MODES = frozenset({"attribute", "spatial_point_in_polygon"})

#: 保留侧：left（左全保留，未匹配右值置空）/ inner（仅匹配行）。
KEEP_SIDES = frozenset({"left", "inner"})

#: 声明输出基数。many_to_many 显式不提供（typed unsupported）——
#: 诊断阶段先估算乘积，超预算即阻断（J08），不静默展开。
OUTPUT_CARDINALITIES = frozenset({"one_to_one", "many_to_one", "one_to_many"})

#: 空键政策：默认 null/missing（含 None 与缺失字段）双侧不参与匹配，
#: null 对 null 不匹配。其它政策本版 typed unsupported。
NULL_POLICIES = frozenset({"never_match"})

#: 空间谓词（点×面）：
#:   within              —— 严格内部（shapely Polygon.contains(Point)）：
#:                          边界点不命中、洞内点不算面内；
#:   within_or_boundary  —— 边界包含（shapely Polygon.covers(Point)）：
#:                          边界点命中、洞内点仍不算面内。
SPATIAL_PREDICATES = frozenset({"within", "within_or_boundary"})

#: 非法几何政策：exclude_count（排除并计数披露）/ block（typed 阻断）。
#: 绝不静默 make_valid。
INVALID_GEOMETRY_POLICIES = frozenset({"exclude_count", "block"})

#: 多命中策略（空间路径；属性路径等价语义由基数声明承载）：
#:   require_unique  —— 任一左要素 >1 命中 → typed 阻断；
#:   expand_all      —— 全部展开（one_to_many 行级展开）；
#:   pick_by_field   —— 按显式优先字段确定性择一（同值/空值 → needs_decision）；
#:   aggregate       —— 闭合聚合集内聚合（count/sum/mean/min/max）。
MULTI_MATCH_POLICIES = frozenset({"require_unique", "expand_all", "pick_by_field", "aggregate"})

#: 择一方向。
PICK_DIRECTIONS = frozenset({"min", "max"})

#: 聚合函数闭合集（null 不当 0；sum/mean 分母 = valid_count）。
AGGREGATE_FUNCTIONS = frozenset({"count", "sum", "mean", "min", "max"})

#: 键变换闭合集（仅作用于字符串键；显式、计入 digest、绝不自动清洗）：
#:   trim —— 去首尾空白；casefold —— 大小写归一；zfill:N —— 左补零到 N 位。
KEY_TRANSFORMS = frozenset({"trim", "casefold", "zfill"})

#: 计划状态。
PLAN_STATUSES = frozenset({"draft", "archived"})

#: 运行状态（与 durable job 终态对齐）。
RUN_STATUSES = frozenset({"pending", "running", "completed", "failed", "cancelled"})

#: 输出种类：富集图层（保留左几何）/ 纯表（无几何）。
OUTPUT_KINDS = frozenset({"enriched_layer", "table"})

# ─────────────────────────── typed 错误码 ───────────────────────────

ERROR_CODES = frozenset({
    "invalid_plan",              # 计划结构/词表违规
    "unsupported_feature",       # 本版显式不支持的能力（typed 披露，不用默认值兜）
    "field_not_found",           # 引用了源中不存在的字段
    "type_conflict",             # 复合键两侧类型不可比 / 字段类型冲突
    "field_mapping_conflict",    # 输出字段重名 / 保留字段被覆盖
    "null_policy_unsupported",   # 空键政策不在封闭词表
    "cardinality_violation",     # 全量诊断发现基数违规（重复键等）
    "needs_decision",            # 择一同值/空值等需用户显式决策
    "budget_exceeded",           # 行/对/字节/时长超预算（估算或运行时）
    "stale_source",              # 执行前源内容/版本漂移（预览失效）
    "source_unresolvable",       # 源锚解析/授权失败（包装 G18 typed 码）
    "source_geometry_missing",   # 空间关联但源缺几何/非点面组合
    "crs_unresolvable",          # 未知投影/gcj02/bd09 等不可信坐标帧
    "invalid_geometry",          # 非法几何按政策阻断
    "empty_result",              # 契约化空结果（非失败；计数可对账）
    "not_owned",                 # 越权访问他人计划/运行（404 语义）
    "stale_plan_revision",       # 计划 CAS 冲突
    "run_not_found",
    "receipt_missing",           # 终态但收据缺失/内容不可读（typed 失效）
    "run_not_active",            # 对非活跃运行执行取消
    "result_unavailable",        # 结果尚未发布/不可分页
    "render_ref_over_budget",    # 输出超过渲染投影上限（数据本体仍可交付）
    "internal_error",
})

# ─────────────────────────── 预算（初始值；可被计划内更严格值覆盖） ───────────────────────────
#
# 约定：所有上限「执行前估算、执行中强制」；任何阶段超限 → typed
# budget_exceeded（绝不截断前缀后宣称完成）。与仓库既有闸取更严者：
# geocompute HARD_NODE_ROW_CAP=500k、G24 materialize 10k、G18 交付
# MAX_ROWS_PER_DELIVERY=200k 均不低于本域默认。

#: 单源行数上限（两侧独立计）。
DEFAULT_MAX_SOURCE_ROWS = 50_000

#: 诊断/预览每侧异常样本上限。
DIAGNOSTIC_SAMPLE_LIMIT = 50

#: 预览结果行上限（诊断预览的右值示例）。
PREVIEW_ROW_LIMIT = 50

#: 候选/匹配对总数上限（分配前估算，运行中强制）。
DEFAULT_MAX_JOIN_PAIRS = 1_000_000

#: 最终输出行数上限。
DEFAULT_MAX_OUTPUT_ROWS = 100_000

#: 临时内存预算（估计值：行数×均行字节；超限 typed 拒绝）。
DEFAULT_TEMP_BYTES_BUDGET = 256 * 1024 * 1024

#: 执行时长上限（秒）。
DEFAULT_DEADLINE_S = 300.0

#: 输出字段数上限（含 lineage/保留字段）。
MAX_OUTPUT_FIELDS = 256

#: 渲染投影（session FC ref）行数上限 —— 与 G24 MAX_MATERIALIZE_FEATURES
#: 同口径；超过时「加入地图」typed 披露 render_ref_over_budget，
#: 数据本体（GeoParquet/Parquet + DataObject）不受影响。
RENDER_REF_MAX_ROWS = 10_000

#: 结果分页：单页行数上限。
RESULT_PAGE_MAX_ROWS = 200

#: 结果分页：单页默认行数。
RESULT_PAGE_DEFAULT_ROWS = 50

#: 每字段样本值上限（诊断卡）。
DIAG_FIELD_SAMPLE_LIMIT = 3

#: 大桶（单键右侧行数）预警阈值：超过即计入乘积估计而非逐对枚举。
LARGE_BUCKET_THRESHOLD = 100

# ── 硬顶（计划内预算不可越过；与仓库既有闸同向：geocompute 节点行帽
#    500k、G18 交付行帽 200k —— 本域输出硬顶取 200k 同口径） ──────────
HARD_MAX_SOURCE_ROWS = 200_000
HARD_MAX_JOIN_PAIRS = 5_000_000
HARD_MAX_OUTPUT_ROWS = 200_000
HARD_TEMP_BYTES = 512 * 1024 * 1024
