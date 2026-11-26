"""内置领域模板（WP6）：四个可实例化的作者模型原型。

模板是**原型**不是单例：每次 ``template_documents()`` / ``from_template``
调用都返回全新 ``ModelDocument`` 实例（同一模板可多次发布/实例化互不
串扰）。全部模板满足：

- ``source = {"kind": "template", "ref": "<template_id>"}`` 且
  ``template_id`` 填模板 slug（审计面：草稿从模板来）；
- ``validate_document`` 零 error 级 finding（发布门可过）；
- 只引用既有 registry 已接线能力（analysis ⊆ _WIRED = category_breakdown
  / admin_aggregation / rate_aggregation；transform ⊆ EXECUTABLE_TRANSFORM_OPS
  中 geocompute 真实接线的 buffer/overlay/filter；cartography 真实渲染；
  不引入未接线能力假装可执行）。

运行时能力边界（诚实披露，逐模板 docstring 记录）：

- 真实执行阶梯 = cartography（matplotlib）> analysis 真实聚合（_WIRED）>
  geocompute in-process transform。表产物（admin_aggregate_table）不能喂
  任何 required=True 端口（descriptor 契约 feature_count=0），只能进
  output 汇点（required=False）或链尾 —— 本文件所有模板遵守（聚合输出
  一律接 output 汇点，绝不接 analysis/cartography 计算端口）。
- 栅格算子、时序交叉表（crosstab）在当前 runtime **未接线**：env_sample_stats
  用矢量采样点等效替代栅格统计、two_period_compare 用两期同构聚合+并排
  汇点等效替代交叉表 —— 理由在各模板 docstring。
- facility_service_area 的 overlay 第二输入通道与 env_sample_stats 的
  filter 谓词载体在当前 registry 受限（详见各 docstring），结构合法、
  静态验证零 error，执行面按披露的边界工作。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from app.schemas.model_authoring_schema import (
    BoundedValue,
    ModelDocument,
    ModelEdge,
    ModelLayoutPos,
    ModelNode,
    ModelNodeParams,
    ModelParamSpec,
)

#: 模板 slug（= ModelDocument.template_id = source.ref；词法同节点 slug）。
TEMPLATE_FACILITY_ADMIN_COUNT = "facility_admin_count"
TEMPLATE_FACILITY_SERVICE_AREA = "facility_service_area"
TEMPLATE_ENV_SAMPLE_STATS = "env_sample_stats"
TEMPLATE_TWO_PERIOD_COMPARE = "two_period_compare"

TEMPLATE_IDS = (
    TEMPLATE_FACILITY_ADMIN_COUNT,
    TEMPLATE_FACILITY_SERVICE_AREA,
    TEMPLATE_ENV_SAMPLE_STATS,
    TEMPLATE_TWO_PERIOD_COMPARE,
)


# ── 模板 1：设施按行政区计数（真实 driver 可全链跑通）────────────────────

def _facility_admin_count() -> ModelDocument:
    """facility_admin_count —— 设施点按行政区（辖区字段）计数 + 主题图。

    领域语义：主体（subject）是设施点；admin_aggregation 按
    ``admin_field``（默认 "district"）分组 count，产出「行政区 → 设施数」
    统计表（进 output 汇点）；另一分支 data_input → cartography 按类别
    字段着色渲染设施分布图（进 output 汇点）。

    科学边界（诚实声明）：

    - 本模板只产 **计数**（count）。计数不是率、不是密度：行政区面积
      不相同时比较「每区设施数」会误导演示为公平性结论；需要率/密度
      必须显式接入 rate_aggregation + 真实分母（面积/人口），本模板
      刻意不含分母输入 —— 宁可结论窄，不冒充归一化结论。
    - 密度（数目/面积）与数目不混用：模板不声明任何面积字段，聚合词
      表只有 count，从词表面杜绝密度/数目混装。

    算子选择理由：admin_aggregation 在 runtime science 适配器 _WIRED
    真实聚合词表内（data_fabric compute_aggregates 真实执行）；参数
    ``admin_field`` 是 science 侧 ``_group_field`` 的真实读取键（优先级
    field > group_by > admin_field）。cartography 是 driver 独立分派
    kind（真实 matplotlib 渲染）。

    执行面：全链真实（buffer 前缀为空也不需要 —— 本模板零 transform），
    是四个模板中唯一被 tests 断言「真实 driver SUCCEEDED」的模板。
    """
    return ModelDocument(
        name="设施按行政区计数",
        description=(
            "设施点按行政区字段计数（count），统计表与类别主题图双汇点。"
            "科学边界：只产计数，不产率/密度（无分母输入；比较每区绝对数 "
            "不能当作公平性/可达性结论）。"),
        source={"kind": "template", "ref": TEMPLATE_FACILITY_ADMIN_COUNT},
        template_id=TEMPLATE_FACILITY_ADMIN_COUNT,
        params=[
            ModelParamSpec(
                name="admin_field", kind="field", input_node="facilities",
                label="行政区字段", required=False,
                default=BoundedValue(value="district"),
                description="设施点属性中承载行政区/辖区名的字段名"),
        ],
        nodes=[
            ModelNode(
                node_id="facilities", kind="data_input", role="subject",
                title="设施点",
                description=(
                    "设施点 FeatureCollection；须含行政区字段（默认 district）"
                    "与类别字段（category）。坐标应为 WGS84 经纬度或声明的"
                    "投影 CRS —— 本模板无距离运算，单位语义不随坐标改变。")),
            ModelNode(
                node_id="admin_stats", kind="analysis",
                capability="admin_aggregation",
                title="按行政区计数",
                params=ModelNodeParams(values={
                    "admin_field": BoundedValue(value="$param:admin_field"),
                })),
            ModelNode(
                node_id="render", kind="cartography",
                title="设施分布专题图",
                params=ModelNodeParams(values={
                    "title": BoundedValue(value="设施分布（按类别着色）"),
                    "field": BoundedValue(value="category"),
                })),
            ModelNode(node_id="stats_out", kind="output",
                      title="行政区计数表"),
            ModelNode(node_id="map_out", kind="output", title="设施分布图"),
        ],
        edges=[
            ModelEdge(from_node="facilities", from_port="subject",
                      to_node="admin_stats", to_port="in"),
            ModelEdge(from_node="admin_stats", from_port="out",
                      to_node="stats_out", to_port="in"),
            ModelEdge(from_node="facilities", from_port="subject",
                      to_node="render", to_port="in"),
            ModelEdge(from_node="render", from_port="out",
                      to_node="map_out", to_port="in"),
        ],
        output_node_ids=["stats_out", "map_out"],
        layout={
            "facilities": ModelLayoutPos(x=40, y=160),
            "admin_stats": ModelLayoutPos(x=280, y=60),
            "stats_out": ModelLayoutPos(x=520, y=60),
            "render": ModelLayoutPos(x=280, y=280),
            "map_out": ModelLayoutPos(x=520, y=280),
        },
    )


# ── 模板 2：设施服务区（buffer + overlay；执行边界诚实披露）──────────────

def _facility_service_area() -> ModelDocument:
    """facility_service_area —— 设施 buffer 服务区 + 行政边界叠加（双汇点）。

    领域语义：设施点（facility）做 ``distance_m``（默认 800 m）buffer 得
    服务区面；与行政边界（boundary）做 ``overlay``（真实算子词表
    ``how=intersection``，overlay_smart 支持 intersection/union/difference/
    symmetric_difference/identity —— 模板不虚构 ``keep`` 等参数键）。
    主干统计（category_breakdown）消费 buffer 后特征集；服务区∩边界的
    叠加分支喂 cartography 汇点。

    执行面（诚实披露）：当前 geocompute 内联通道只内联 transform 节点的
    **首个**输入（``parameters["features"]``），overlay 算子的第二输入
    （``parameters["other"]`` 或第二路 payloads）无法经作者面承载 ——
    transform 端口投影是单 ``in`` 端口、有界参数载体（BoundedValue）只收
    标量/短标量列表，装不下第二幅 FeatureCollection。因此 overlay 节点
    声明为 ``optional=True`` 分支：当前 registry 下该分支诚实 SKIPPED
    （实例投影可见），主干 buffer→统计链真实执行；待 overlay 第二输入
    通道接线后该分支自动生效，模板结构无需变更。

    科学边界：buffer 单位显式为米（``unit="m"``）—— 输入坐标必须携带
    投影米制 CRS 或由 runtime 的 CRS 归一门处理；WGS84 经纬度上直接
    buffer(米) 是度/米混淆错误（geocompute 已有 CRS 归一守门，模板在
    义务面再次声明）。统计仍是计数（count），不含率/密度结论。
    """
    return ModelDocument(
        name="设施服务区",
        description=(
            "设施点 buffer 服务区（米制），主干类别计数 + 服务区∩行政区 "
            "叠加分支。叠加分支依赖 overlay 第二输入通道（当前 registry "
            "未接线 → optional 分支诚实跳过；接线后自动生效）。"),
        source={"kind": "template", "ref": TEMPLATE_FACILITY_SERVICE_AREA},
        template_id=TEMPLATE_FACILITY_SERVICE_AREA,
        params=[
            ModelParamSpec(
                name="distance_m", kind="number",
                label="服务区半径（米）", required=False,
                default=BoundedValue(value=800),
                minimum=1.0, maximum=5000.0, unit="m"),
        ],
        nodes=[
            ModelNode(
                node_id="facility", kind="data_input", role="facility",
                title="设施点",
                description=(
                    "设施点 FeatureCollection（含 category 类别字段）；"
                    "坐标须为投影米制 CRS 或声明 CRS 由归一门处理。")),
            ModelNode(
                node_id="buffered", kind="transform", transform_op="buffer",
                title="服务区 buffer",
                params=ModelNodeParams(values={
                    "distance": BoundedValue(value="$param:distance_m"),
                    "unit": BoundedValue(value="m"),
                })),
            ModelNode(
                node_id="boundary", kind="data_input", role="boundary",
                title="行政区边界",
                description="行政区面要素（叠加的第二个输入）。"),
            ModelNode(
                node_id="intersect_zone", kind="transform",
                transform_op="overlay", optional=True,
                title="服务区∩行政区",
                description=(
                    "overlay(how=intersection)。当前 registry 下第二输入"
                    "通道未接线：本节点 optional，失败/跳过在实例投影中"
                    "诚实披露，不影响主干统计。"),
                params=ModelNodeParams(values={
                    "how": BoundedValue(value="intersection"),
                })),
            ModelNode(
                node_id="by_category", kind="analysis",
                capability="category_breakdown",
                title="服务区内类别计数",
                params=ModelNodeParams(values={
                    "field": BoundedValue(value="category"),
                })),
            ModelNode(
                node_id="render", kind="cartography",
                title="服务区专题图",
                params=ModelNodeParams(values={
                    "title": BoundedValue(value="设施服务区（buffer 800m）"),
                    "field": BoundedValue(value="category"),
                })),
            ModelNode(node_id="stats_out", kind="output",
                      title="服务区类别计数表"),
            ModelNode(node_id="map_out", kind="output", title="服务区图"),
        ],
        edges=[
            ModelEdge(from_node="facility", from_port="facility",
                      to_node="buffered", to_port="in"),
            ModelEdge(from_node="buffered", from_port="out",
                      to_node="by_category", to_port="in"),
            ModelEdge(from_node="by_category", from_port="out",
                      to_node="stats_out", to_port="in"),
            ModelEdge(from_node="buffered", from_port="out",
                      to_node="intersect_zone", to_port="in"),
            ModelEdge(from_node="boundary", from_port="boundary",
                      to_node="intersect_zone", to_port="in"),
            ModelEdge(from_node="intersect_zone", from_port="out",
                      to_node="render", to_port="in"),
            ModelEdge(from_node="render", from_port="out",
                      to_node="map_out", to_port="in"),
        ],
        output_node_ids=["stats_out", "map_out"],
        layout={
            "facility": ModelLayoutPos(x=40, y=40),
            "buffered": ModelLayoutPos(x=220, y=40),
            "boundary": ModelLayoutPos(x=220, y=340),
            "intersect_zone": ModelLayoutPos(x=400, y=220),
            "by_category": ModelLayoutPos(x=400, y=60),
            "stats_out": ModelLayoutPos(x=600, y=60),
            "render": ModelLayoutPos(x=580, y=220),
            "map_out": ModelLayoutPos(x=780, y=220),
        },
    )


# ── 模板 3：环境采样分级统计（矢量采样点等效替代「栅格统计」）────────────

def _env_sample_stats() -> ModelDocument:
    """env_sample_stats —— 环境采样点分级统计（矢量等效替代栅格统计）。

    领域语义：环境监测采样点（subject，属性含数值字段如浓度值与分级
    level），经 ``filter``（属性谓词）筛出达标/超标子集后按分级字段做
    category_breakdown 计数，产出统计表 + 分级专题图。

    为何不是「栅格统计」：runtime 当前**无栅格算子接线**（geocompute
    raster 通道不进 workflow transform 词表；science 适配器只接 stats
    三能力）。矢量采样点是栅格抽样的合法等效物 —— 像元值本就来自采样
    面的离散化；用采样点等级计数替代栅格直方图，语义透明且全部落在
    已接线执行阶梯上。若未来栅格算子接线，本模板应被显式替换而非
    混入未接线能力。

    filter 谓词词表（真实词汇，来自 data_fabric predicate AST）：
    ``{"op": "gt", "field": "<数值字段>", "value": <阈值>}``（op ∈ gt/ge/
    lt/le/eq/ne/between/in/like/is_null/and/or/not）。**执行边界诚实
    披露**：作者面有界参数载体（BoundedValue）只收标量/短标量列表，
    装不下 dict 形态谓词 AST —— 因此当前 registry 下 filter 节点无法
    经作者面注入谓词，执行会在该节点诚实失败（FILTER node requires
    parameters.predicate）；本模板结构合法、静态验证零 error，谓词
    载体接线前不承诺 filter hop 可执行。

    CRS 义务（title/description 声明）：本模板只做属性过滤与计数、不做
    距离/面积运算 —— 采样点坐标声明为 WGS84 即可；若用户后续给本链
    追加 buffer 等距离算子，必须先换投影米制 CRS（度/米混淆是科学
    错误，不是样式问题）。

    科学边界：分级（level）必须来自数据自身的显式字段或可复算阈值
    （记录在 filter 谓词里）；禁止「看起来像超标」的无阈值计数结论。
    """
    return ModelDocument(
        name="环境采样分级统计",
        description=(
            "环境采样点：filter 属性谓词筛选 → 按分级字段计数 + 分级专题图。"
            "矢量等效替代栅格统计（runtime 无栅格算子接线）；filter 谓词"
            "载体（dict AST）当前不经作者面可注入，执行边界见节点披露。"),
        source={"kind": "template", "ref": TEMPLATE_ENV_SAMPLE_STATS},
        template_id=TEMPLATE_ENV_SAMPLE_STATS,
        params=[
            ModelParamSpec(
                name="level_field", kind="field", input_node="samples",
                label="分级字段", required=False,
                default=BoundedValue(value="level"),
                description="采样点属性中的分级/类别字段名"),
        ],
        nodes=[
            ModelNode(
                node_id="samples", kind="data_input", role="subject",
                title="环境采样点",
                description=(
                    "采样点 FeatureCollection：属性含数值字段（如浓度值）与"
                    "分级字段（默认 level）。坐标 WGS84 即可（本模板无距离"
                    "运算）；追加距离算子前必须先换投影米制 CRS。")),
            ModelNode(
                node_id="qualified", kind="transform", transform_op="filter",
                title="阈值筛选",
                description=(
                    "属性谓词筛选（data_fabric predicate AST 词表，如 "
                    '{"op":"gt","field":"value","value":75}）。载体边界：'
                    "BoundedValue 不收 dict —— 谓词接线前本节点执行面诚实"
                    "失败，不假装过滤。")),
            ModelNode(
                node_id="by_level", kind="analysis",
                capability="category_breakdown",
                title="分级计数",
                params=ModelNodeParams(values={
                    "field": BoundedValue(value="$param:level_field"),
                })),
            ModelNode(
                node_id="render", kind="cartography",
                title="采样分级专题图",
                params=ModelNodeParams(values={
                    "title": BoundedValue(value="环境采样分级"),
                    "field": BoundedValue(value="level"),
                })),
            ModelNode(node_id="stats_out", kind="output",
                      title="分级计数表"),
            ModelNode(node_id="map_out", kind="output", title="分级专题图"),
        ],
        edges=[
            ModelEdge(from_node="samples", from_port="subject",
                      to_node="qualified", to_port="in"),
            ModelEdge(from_node="qualified", from_port="out",
                      to_node="by_level", to_port="in"),
            ModelEdge(from_node="qualified", from_port="out",
                      to_node="render", to_port="in"),
            ModelEdge(from_node="by_level", from_port="out",
                      to_node="stats_out", to_port="in"),
            ModelEdge(from_node="render", from_port="out",
                      to_node="map_out", to_port="in"),
        ],
        output_node_ids=["stats_out", "map_out"],
        layout={
            "samples": ModelLayoutPos(x=40, y=160),
            "qualified": ModelLayoutPos(x=240, y=160),
            "by_level": ModelLayoutPos(x=440, y=60),
            "stats_out": ModelLayoutPos(x=640, y=60),
            "render": ModelLayoutPos(x=440, y=280),
            "map_out": ModelLayoutPos(x=640, y=280),
        },
    )


# ── 模板 4：两期对比（两期同构聚合 + 并排汇点等效替代交叉表）────────────

def _two_period_compare() -> ModelDocument:
    """two_period_compare —— 两期（基期/对比期）同构行政区计数并排对比。

    领域语义：baseline（基期）与 comparison_time（对比期）两幅同构
    设施/要素点集，各自经 admin_aggregation 按 ``admin_field`` 计数，
    产出两个独立统计表汇点（并排呈现）+ 一幅基期分布图。

    为何不是「交叉表（crosstab）」：runtime 当前**无交叉表算子接线**
    （science 适配器 _WIRED 只有单 group_by 聚合；「行政区 × 期次」
    二维交叉需要 crosstab/透视能力，未接线）。等效方案：两期**同构**
    （同 group 字段、同算子、同参数）聚合 + 两个独立汇点 —— 上层并排
    读取即可做差；同构性由模板结构保证（两节点参数来自同一 $param），
    不需要事后对齐。交叉表接线后本模板应显式升级，而不是把两次独立
    聚合谎称为一张交叉表。

    科学边界：两期数据必须**同源同口径**（同一采集/清洗规则）——模板
    无法静态验证口径一致性，实例化者须对两幅输入负责；计数对比不构成
    率/密度结论（无分母），「基期增长 X%」类表述需要显式 rate_aggregation
    + 真实分母支持，本模板刻意不给。

    执行面：两聚合分支 + cartography 全部落在已接线阶梯（真实执行），
    与 facility_admin_count 同款链路 ×2。
    """
    return ModelDocument(
        name="两期对比统计",
        description=(
            "基期/对比期同构行政区计数，双统计表汇点并排 + 基期分布图。"
            "交叉表（crosstab）未接线的等效方案：同 group 字段同参数的"
            "两期独立聚合，上层并排做差。"),
        source={"kind": "template", "ref": TEMPLATE_TWO_PERIOD_COMPARE},
        template_id=TEMPLATE_TWO_PERIOD_COMPARE,
        params=[
            ModelParamSpec(
                name="admin_field", kind="field", input_node="baseline",
                label="行政区字段", required=False,
                default=BoundedValue(value="district"),
                description="两期共用的行政区/辖区字段名（同构聚合的键）"),
        ],
        nodes=[
            ModelNode(
                node_id="baseline", kind="data_input", role="baseline",
                title="基期数据",
                description=(
                    "基期要素点（含行政区字段）；与对比期必须同源同口径。")),
            ModelNode(
                node_id="comparison", kind="data_input",
                role="comparison_time", title="对比期数据",
                description="对比期要素点（字段结构与基期一致）。"),
            ModelNode(
                node_id="base_agg", kind="analysis",
                capability="admin_aggregation", title="基期计数",
                params=ModelNodeParams(values={
                    "admin_field": BoundedValue(value="$param:admin_field"),
                })),
            ModelNode(
                node_id="comp_agg", kind="analysis",
                capability="admin_aggregation", title="对比期计数",
                params=ModelNodeParams(values={
                    "admin_field": BoundedValue(value="$param:admin_field"),
                })),
            ModelNode(
                node_id="render", kind="cartography", title="基期分布图",
                params=ModelNodeParams(values={
                    "title": BoundedValue(value="基期分布（按行政区着色）"),
                    "field": BoundedValue(value="$param:admin_field"),
                })),
            ModelNode(node_id="base_out", kind="output", title="基期计数表"),
            ModelNode(node_id="comp_out", kind="output",
                      title="对比期计数表"),
            ModelNode(node_id="map_out", kind="output", title="基期分布图"),
        ],
        edges=[
            ModelEdge(from_node="baseline", from_port="baseline",
                      to_node="base_agg", to_port="in"),
            ModelEdge(from_node="comparison", from_port="comparison_time",
                      to_node="comp_agg", to_port="in"),
            ModelEdge(from_node="base_agg", from_port="out",
                      to_node="base_out", to_port="in"),
            ModelEdge(from_node="comp_agg", from_port="out",
                      to_node="comp_out", to_port="in"),
            ModelEdge(from_node="baseline", from_port="baseline",
                      to_node="render", to_port="in"),
            ModelEdge(from_node="render", from_port="out",
                      to_node="map_out", to_port="in"),
        ],
        output_node_ids=["base_out", "comp_out", "map_out"],
        layout={
            "baseline": ModelLayoutPos(x=40, y=60),
            "comparison": ModelLayoutPos(x=40, y=260),
            "base_agg": ModelLayoutPos(x=260, y=60),
            "comp_agg": ModelLayoutPos(x=260, y=260),
            "base_out": ModelLayoutPos(x=480, y=60),
            "comp_out": ModelLayoutPos(x=480, y=260),
            "render": ModelLayoutPos(x=260, y=430),
            "map_out": ModelLayoutPos(x=480, y=430),
        },
    )


#: 工厂表（slug → 全新原型实例的工厂）。
_FACTORIES: Dict[str, Callable[[], ModelDocument]] = {
    TEMPLATE_FACILITY_ADMIN_COUNT: _facility_admin_count,
    TEMPLATE_FACILITY_SERVICE_AREA: _facility_service_area,
    TEMPLATE_ENV_SAMPLE_STATS: _env_sample_stats,
    TEMPLATE_TWO_PERIOD_COMPARE: _two_period_compare,
}

#: 内置模板工厂清单（每次调用返回全新 ModelDocument 实例；原型非单例）。
BUILTIN_TEMPLATES: List[Callable[[], ModelDocument]] = [
    _facility_admin_count,
    _facility_service_area,
    _env_sample_stats,
    _two_period_compare,
]


def template_documents() -> List[ModelDocument]:
    """全部内置模板 → 全新 ModelDocument 实例列表（每次调用新实例）。"""
    return [factory() for factory in BUILTIN_TEMPLATES]


def from_template(
    template_id: str, *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> ModelDocument:
    """按 slug 实例化模板原型（可选覆盖 name/description）。

    ``template_id`` / ``source``（kind=template, ref=slug）**不被覆盖**
    —— 模板来源是审计事实，改名不改血统。未知 slug → ``ValueError``
    （fail-closed，绝不静默回落到任意模板）。
    """
    factory = _FACTORIES.get(str(template_id or ""))
    if factory is None:
        raise ValueError(
            f"unknown template_id {template_id!r}; "
            f"known: {list(_FACTORIES)}")
    doc = factory()
    updates: Dict[str, str] = {}
    if name is not None:
        updates["name"] = str(name)[:120]
    if description is not None:
        updates["description"] = str(description)[:2000]
    if updates:
        doc = doc.model_copy(update=updates)
    return doc


__all__ = [
    "TEMPLATE_IDS",
    "TEMPLATE_FACILITY_ADMIN_COUNT",
    "TEMPLATE_FACILITY_SERVICE_AREA",
    "TEMPLATE_ENV_SAMPLE_STATS",
    "TEMPLATE_TWO_PERIOD_COMPARE",
    "BUILTIN_TEMPLATES",
    "template_documents",
    "from_template",
]
