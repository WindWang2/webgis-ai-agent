"""G05 task-level 场景库（corpus）。

场景 = 人工选取的不变量期望（不是录制当前输出）；family/场景矩阵见
planning/g05/TEST_MATRIX.md。本模块是唯一场景事实源；
fixtures/task_scenarios/*.json 由 scripts/g05_task_acceptance.py 从此处
pin 生成（逐字节一致性由 test_corpus 钉住）。

离线诚实语义（W1 实证，见 DECISIONS D11）：
- 意图 planner 的矢量任务 recipe（poi_distribution_overview /
  administrative_choropleth / point_density）恒含 mandatory
  poi_query（部分含 admin_boundary_query），其工具面只有网络/本机数据
  工具 → 离线沙箱无法全量完成 → 终验 fail-closed（BLOCKED_BY_DATA、
  task_complete=False）。**这是被钉扎的系统红线，不是场景缺陷**：
  正例场景断言『部分产品已交付 + 判据诚实』；负例场景断言『注入错误
  行为时判错面必须与正例可区分』。
- 栅格任务（raster_distribution）的 raster_source/ndvi 行同样仅网络
  工具（fetch_dem / compute_ndvi）→ 同为诚实降级。
"""
from __future__ import annotations

from typing import Callable, Dict, List

from .contracts import (
    AgentStep,
    DatasetSpec,
    Expectation,
    Face,
    ScenarioContract,
    StepKind,
    TurnSpec,
)

BUILDERS: List[Callable[[], ScenarioContract]] = []


def register(builder):
    BUILDERS.append(builder)
    return builder


def all_scenarios() -> List[ScenarioContract]:
    return [b() for b in BUILDERS]


def scenarios_by_id() -> Dict[str, ScenarioContract]:
    return {s.id: s for s in all_scenarios()}


def scenarios_by_family() -> Dict[str, List[ScenarioContract]]:
    out: Dict[str, List[ScenarioContract]] = {}
    for s in all_scenarios():
        out.setdefault(s.family, []).append(s)
    return out


def _step(call_id: str, kind: StepKind, **kw) -> AgentStep:
    return AgentStep(call_id=call_id, kind=kind, **kw)


def _exp(eid: str, face: Face, kind: str, path: str, op: str = "eq",
         value=None, expected: str = "pass", note: str = "",
         expected_verdict: str | None = None) -> Expectation:
    return Expectation(
        id=eid, face=face, kind=kind, path=path, op=op, value=value,
        expected_verdict=expected_verdict or expected, note=note,
    )


#: 每个场景共享的诚实边界声明（NL 理解不在离线 lane 判定范围）。
COMMON_NOT_EVALUATED = [
    "nl_understanding: Pi LLM 的 NL→tool-call 决策由 scripted steps 代替，"
    "语义理解正确性不在本 lane 判定（见 contracts.py 设计披露）",
    "final_text: 最终答复文本经 Pi transcript 持久化，dispatch 缝不可观测",
    "render_pixels: 前端真实渲染像素不可观测；渲染证据以 RenderWorkEnvelope"
    "投影与导出语义一致性（verify_export_semantic_consistency）为准",
]

#: 终验落块（terminal block 持久化）场景的 fail-closed 红线期望。
#: 适用：mandatory 行已被 product 谱系标记 available（artifact 校验可判）
#: 的查询形态（实证：点位类 2-mandatory 措辞）。
def _terminal_block_exps(prefix: str) -> List[Expectation]:
    return [
        _exp(f"{prefix}-verdict", Face.FINAL_ANSWER, "goal_verdict",
             "map_product.goal_satisfaction_verdict", op="in",
             value=["blocked", "failed"],
             note="离线数据源缺口 → 诚实 BLOCKED/FAILED，不得假成功"),
        _exp(f"{prefix}-task-incomplete", Face.FINAL_ANSWER, "task_complete",
             "map_product.task_complete", value=False),
    ]


#: 终验 pending（needs_execution，块按生产语义不持久化）场景的诚实期望。
#: 适用：存在从未执行的 mandatory 行（admin_boundary_query/admin_aggregation
#: 等）→ finalize 判 pending → 完成面键缺席 = not_evaluated（钉扎防回归：
#: 若未来该形态开始落块/谎报，检查翻红）。
def _pending_honest_exps(prefix: str) -> List[Expectation]:
    return [
        _exp(f"{prefix}-no-claim", Face.FINAL_ANSWER, "goal_verdict",
             "map_product.goal_satisfaction_verdict",
             expected_verdict="not_evaluated",
             note="mandatory 行 needs_execution → finalize pending，"
                  "完成块不持久化（生产语义），完成面诚实缺席"),
        _exp(f"{prefix}-needs-execution-visible", Face.METHOD,
             "tool_receipt", "plan.progress.mandatory_pending", op="ge",
             value=1,
             note="未执行能力行对计划可见（诚实披露，不是静默完成）"),
    ]


#: 兼容别名（terminal-block 期望集）。
_fail_closed_exps = _terminal_block_exps


# ══════════════════════════════════════════════════════════════════════
# F01 学校点位分布（诚实降级基线）
# ══════════════════════════════════════════════════════════════════════

@register
def f01_school_points_map() -> ScenarioContract:
    q = "看看成都小学都在哪里，标出点位"
    return ScenarioContract(
        id="f01-school-points-map",
        family="F01",
        title="成都小学点位分布 → 点位图产品（离线诚实降级）",
        description=(
            "意图→计划→数据存储→产品组装→结算全链。poi_query/admin_boundary"
            "_query 工具面离线不可达 → 终验 fail-closed；已交付部分产品"
            "（MapSpec 图层/组件/导出语义一致性）仍可断言。『不得假成功』红线。"),
        user_goal=q,
        datasets=[DatasetSpec(name="schools", dataset_kind="school_points")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "schools",
                                 "dataset_kind": "school_points"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:schools}}",
                                 "title": "成都市小学点位分布（模拟数据）"}),
            ],
        )],
        expectations=[
            _exp("f01-intent-scope", Face.INTENT, "intent_resolved",
                 "chapter_summary.recipe_id", op="exists"),
            _exp("f01-mapspec-exists", Face.MAPSPEC, "mapspec_present",
                 "mapspec.exists", value=True),
            _exp("f01-points-layer", Face.MAPSPEC, "mapspec_layer",
                 "mapspec.layer_types", op="contains", value="circle"),
            _exp("f01-components", Face.MAPSPEC, "mapspec_component",
                 "mapspec.component_types", op="exists"),
            _exp("f01-export-check", Face.RENDER_EXPORT, "export_status",
                 "export.status", op="in", value=["pass", "warning"]),
            _exp("f01-requirement-doc", Face.REQUIREMENT,
                 "requirement_state", "requirement.exists", value=True),
            *_fail_closed_exps("f01"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["baseline", "points"],
    )


# ══════════════════════════════════════════════════════════════════════
# F02 点图 vs 热力（方法选择；样本量资格）
# ══════════════════════════════════════════════════════════════════════

@register
def f02a_heatmap_method() -> ScenarioContract:
    q = "成都小学热力图"
    return ScenarioContract(
        id="f02a-heatmap-method",
        family="F02",
        title="小学热力图（样本充足 → 热力层资格通过）",
        user_goal=q,
        datasets=[DatasetSpec(name="dense", dataset_kind="school_points_dense")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "dense",
                                 "dataset_kind": "school_points_dense"}),
                _step("heat", StepKind.EXECUTE, tool="heatmap_data",
                      arguments={"geojson": "{{ref:dense}}",
                                 "render_type": "native"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:dense}}",
                                 "title": "成都市小学热力图（模拟数据）"}),
            ],
        )],
        expectations=[
            _exp("f02a-heatmap-layer", Face.MAPSPEC, "mapspec_layer",
                 "mapspec.layer_types", op="contains", value="heatmap",
                 note="40 点 ≥ HEATMAP_MIN_POINTS → 热力层有资格"),
            _exp("f02a-export", Face.RENDER_EXPORT, "export_status",
                 "export.status", op="in", value=["pass", "warning"]),
            *_pending_honest_exps("f02a"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["method", "heatmap"],
    )


@register
def f02b_sparse_heatmap_degraded() -> ScenarioContract:
    """负例（科学资格注入）：3 点 < 最低样本量 → 热力层必须被禁用降级。

    反转对照（test_task_failures）：把数据换成 dense 后『无热力层』期望
    必须翻红 —— 证明该负例不是恒绿。
    """
    q = "成都小学热力图"
    return ScenarioContract(
        id="f02b-sparse-heatmap-degraded",
        family="F02",
        title="稀疏样本热力请求 → 热力资格拒绝（invalid_method 注入）",
        negative=True,
        user_goal=q,
        datasets=[DatasetSpec(name="sparse", dataset_kind="school_points_sparse")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "sparse",
                                 "dataset_kind": "school_points_sparse"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:sparse}}",
                                 "title": "成都市小学热力图（模拟数据）"}),
            ],
        )],
        expectations=[
            _exp("f02b-no-heatmap", Face.METHOD, "sample_size",
                 "mapspec.layer_types", op="not_in", value=["heatmap"],
                 note="3 点 < HEATMAP_MIN_POINTS(10) → 热力层确定性禁用"),
            _exp("f02b-points-fallback", Face.MAPSPEC, "mapspec_layer",
                 "mapspec.layer_types", op="contains", value="circle",
                 note="降级为点图交付"),
            _exp("f02b-fallback-evidence", Face.METHOD, "sample_size",
                 "chapter_summary.fallback_count", op="ge", value=1,
                 note="降级决策必须留痕（fallbacks 数组）"),
            *_pending_honest_exps("f02b"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["method", "sample-size", "negative"],
    )


# ══════════════════════════════════════════════════════════════════════
# F03 行政区统计（真实离线聚合工具链）
# ══════════════════════════════════════════════════════════════════════

@register
def f03_admin_aggregation() -> ScenarioContract:
    q = "统计各区小学数量"
    return ScenarioContract(
        id="f03-admin-aggregation",
        family="F03",
        title="各区小学计数 → 行政聚合 + 分级统计图",
        description=(
            "真实离线聚合链：spatial_aggregate(points_ref, polygons_ref) 完成"
            " admin_aggregation 行（产物 ref 由 dispatch 卸载）；poi_query/"
            "admin_boundary_query 离线不可达 → 诚实降级。"),
        user_goal=q,
        datasets=[
            DatasetSpec(name="schools", dataset_kind="school_points"),
            DatasetSpec(name="districts", dataset_kind="district_polygons"),
        ],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "schools",
                                 "dataset_kind": "school_points"}),
                _step("store2", StepKind.STORE_DATA,
                      arguments={"name": "districts",
                                 "dataset_kind": "district_polygons"}),
                _step("profile", StepKind.EXECUTE, tool="spatial_stats",
                      arguments={"features": "{{ref:schools}}"}),
                _step("agg", StepKind.EXECUTE, tool="spatial_aggregate",
                      arguments={"points": "{{ref:schools}}",
                                 "polygons": "{{ref:districts}}"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:schools}}",
                                 "overlay_refs": ["{{ref:districts}}"],
                                 "title": "成都各区小学数量（模拟数据）"}),
            ],
        )],
        expectations=[
            _exp("f03-agg-row-done", Face.METHOD, "tool_receipt",
                 "plan.progress.status_counts.complete", op="ge", value=2,
                 note="spatial_stats→point_profile + spatial_aggregate→"
                      "admin_aggregation 两行真实工具完成"),
            _exp("f03-mapspec", Face.MAPSPEC, "mapspec_layer",
                 "mapspec.layer_count", op="ge", value=1),
            _exp("f03-export", Face.RENDER_EXPORT, "export_status",
                 "export.status", op="in", value=["pass", "warning"]),
            *_pending_honest_exps("f03"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["admin", "aggregation"],
    )


# ══════════════════════════════════════════════════════════════════════
# F04 定量密度（分母语义）
# ══════════════════════════════════════════════════════════════════════

@register
def f04a_density_with_denominator() -> ScenarioContract:
    q = "成都各区小学每平方公里密度"
    return ScenarioContract(
        id="f04a-density-denominator",
        family="F04",
        title="定量面密度（denominator_kind=area 显式分母）",
        user_goal=q,
        datasets=[
            DatasetSpec(name="schools", dataset_kind="school_points_dense"),
            DatasetSpec(name="districts", dataset_kind="district_polygons"),
        ],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "schools",
                                 "dataset_kind": "school_points_dense"}),
                _step("store2", StepKind.STORE_DATA,
                      arguments={"name": "districts",
                                 "dataset_kind": "district_polygons"}),
                _step("rate", StepKind.EXECUTE, tool="spatial_aggregate",
                      arguments={"points": "{{ref:schools}}",
                                 "polygons": "{{ref:districts}}",
                                 "denominator_kind": "area"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:schools}}",
                                 "title": "成都各区小学密度（模拟数据）"}),
            ],
        )],
        expectations=[
            _exp("f04a-agg-done", Face.METHOD, "tool_receipt",
                 "plan.progress.status_counts.complete", op="ge", value=1),
            _exp("f04a-export", Face.RENDER_EXPORT, "export_status",
                 "export.status", op="in", value=["pass", "warning"]),
            *_pending_honest_exps("f04a"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["density", "denominator"],
    )


@register
def f04b_visual_density_for_quantitative_ask() -> ScenarioContract:
    """负例（方法错配注入）：定量密度诉求被 script 注入为纯视觉热力。

    生产里这是 LLM 的方法选择错误；本场景注入同样的错误行为，断言
    完成判据不因视觉产品好看而放行（H01 资格链 BLOCKED_BY_METHOD 或
    诚实降级至少其一成立——不做假成功）。
    """
    q = "成都各区小学每平方公里密度"
    return ScenarioContract(
        id="f04b-visual-for-quantitative",
        family="F04",
        title="定量密度诉求 → 视觉热力交付（invalid_method 注入）",
        negative=True,
        user_goal=q,
        datasets=[DatasetSpec(name="dense", dataset_kind="school_points_dense")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "dense",
                                 "dataset_kind": "school_points_dense"}),
                # 注入错误方法：定量诉求只给视觉热力、无显式分母
                _step("heat", StepKind.EXECUTE, tool="heatmap_data",
                      arguments={"geojson": "{{ref:dense}}",
                                 "render_type": "native"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:dense}}",
                                 "title": "成都小学密度热力（模拟数据）"}),
            ],
        )],
        expectations=[
            *_pending_honest_exps("f04b"),
            _exp("f04b-heatmap-authored-anyway", Face.MAPSPEC, "mapspec_layer",
                 "mapspec.layer_types", op="contains", value="heatmap",
                 note="错误方法的产品仍被组装（可观测、可归因），但判据不放行"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["method", "density", "negative"],
    )


# ══════════════════════════════════════════════════════════════════════
# F05 时间变化
# ══════════════════════════════════════════════════════════════════════

@register
def f05_temporal_points() -> ScenarioContract:
    q = "成都2015到2025年小学设点变化，按年份看分布"
    return ScenarioContract(
        id="f05-temporal-points",
        family="F05",
        title="时序点位 → 时间语义保留在意图与产品链",
        user_goal=q,
        datasets=[DatasetSpec(name="temporal",
                              dataset_kind="temporal_school_points")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "temporal",
                                 "dataset_kind": "temporal_school_points"}),
                _step("stats", StepKind.EXECUTE, tool="spatial_stats",
                      arguments={"features": "{{ref:temporal}}"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:temporal}}",
                                 "title": "成都小学设点时序（模拟数据）"}),
            ],
        )],
        expectations=[
            _exp("f05-recipe", Face.INTENT, "intent_resolved",
                 "chapter_summary.recipe_id", op="exists"),
            _exp("f05-profile-done", Face.METHOD, "tool_receipt",
                 "plan.progress.status_counts.complete", op="ge", value=1),
            _exp("f05-export", Face.RENDER_EXPORT, "export_status",
                 "export.status", op="in", value=["pass", "warning"]),
            *_pending_honest_exps("f05"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED + [
            "temporal_chart: 时序图表组件的数值正确性属 science oracle 层"
            "（tests/science_oracles），本场景只断言时间语义进链不丢失",
        ],
        tags=["temporal"],
    )


# ══════════════════════════════════════════════════════════════════════
# F06 遥感栅格指数（离线波段运算）
# ══════════════════════════════════════════════════════════════════════

@register
def f06_raster_ndvi() -> ScenarioContract:
    q = "NIR和RED波段算NDVI出图"
    return ScenarioContract(
        id="f06-raster-ndvi",
        family="F06",
        title="NIR/RED → NDVI 波段运算（离线 raster_calculator）",
        user_goal=q,
        datasets=[
            DatasetSpec(name="nir", dataset_kind="raster_nir"),
            DatasetSpec(name="red", dataset_kind="raster_red"),
            DatasetSpec(name="schools", dataset_kind="school_points"),
        ],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("s1", StepKind.STORE_DATA,
                      arguments={"name": "nir", "dataset_kind": "raster_nir"}),
                _step("s2", StepKind.STORE_DATA,
                      arguments={"name": "red", "dataset_kind": "raster_red"}),
                _step("s3", StepKind.STORE_DATA,
                      arguments={"name": "schools",
                                 "dataset_kind": "school_points"}),
                _step("ndvi", StepKind.EXECUTE, tool="raster_calculator",
                      arguments={"raster_a": "{{ref:nir}}",
                                 "raster_b": "{{ref:red}}",
                                 "expression": "(A - B) / (A + B)"}),
                _step("stats", StepKind.EXECUTE, tool="spatial_stats",
                      arguments={"features": "{{ref:schools}}"}),
            ],
        )],
        expectations=[
            _exp("f06-bandmath-done", Face.METHOD, "tool_receipt",
                 "plan.progress.status_counts.complete", op="ge", value=2,
                 note="raster_calculator→band_math + spatial_stats→"
                      "point_profile 真实离线完成"),
            _exp("f06-recipe-raster", Face.INTENT, "intent_resolved",
                 "chapter_summary.recipe_id", value="raster_distribution"),
            *_pending_honest_exps("f06"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["raster", "ndvi"],
    )


# ══════════════════════════════════════════════════════════════════════
# F07 两数据空间连接
# ══════════════════════════════════════════════════════════════════════

@register
def f07_spatial_join() -> ScenarioContract:
    q = "把这些小学点和行政区关联起来，标注所属区"
    return ScenarioContract(
        id="f07-spatial-join",
        family="F07",
        title="点↔面空间连接（属性挂接）",
        user_goal=q,
        datasets=[
            DatasetSpec(name="schools", dataset_kind="school_points"),
            DatasetSpec(name="districts", dataset_kind="district_polygons"),
        ],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "schools",
                                 "dataset_kind": "school_points"}),
                _step("store2", StepKind.STORE_DATA,
                      arguments={"name": "districts",
                                 "dataset_kind": "district_polygons"}),
                _step("join", StepKind.EXECUTE, tool="spatial_join",
                      arguments={"left_layer": "{{ref:schools}}",
                                 "right_layer": "{{ref:districts}}",
                                 "predicate": "intersects"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:schools}}",
                                 "title": "小学所属区标注（模拟数据）"}),
            ],
        )],
        expectations=[
            _exp("f07-join-step-ok", Face.DATA, "tool_receipt",
                 "plan.progress.status_counts.complete", op="ge", value=1),
            _exp("f07-export", Face.RENDER_EXPORT, "export_status",
                 "export.status", op="in", value=["pass", "warning"]),
            *_pending_honest_exps("f07"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["join"],
    )


# ══════════════════════════════════════════════════════════════════════
# F08 没有经纬度（拒绝/澄清）
# ══════════════════════════════════════════════════════════════════════

@register
def f08_no_coordinates() -> ScenarioContract:
    """负例（数据缺陷）：表格无坐标 → 不得凭空出图（fail-closed）。"""
    q = "把这份小学名单画到地图上"
    return ScenarioContract(
        id="f08-no-coordinates",
        family="F08",
        title="无经纬度名单 → 出图请求诚实拒绝/降级",
        negative=True,
        user_goal=q,
        datasets=[DatasetSpec(name="table", dataset_kind="no_coord_table")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "table",
                                 "dataset_kind": "no_coord_table"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:table}}",
                                 "title": "小学名单地图（模拟数据）"},
                      allow_error=True),
            ],
        )],
        expectations=[
            *_pending_honest_exps("f08"),
            _exp("f08-no-fake-points", Face.MAPSPEC, "mapspec_layer",
                 "mapspec.layer_types", op="not_in", value=["circle"],
                 note="无坐标行不得被伪造成点位图层"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED + [
            "clarification_loop: 生产澄清闭环（requirement_ir clarify）的"
            "多轮对话行为由其专属测试覆盖；本场景断言 fail-closed 语义",
        ],
        tags=["negative", "no-coords"],
    )


# ══════════════════════════════════════════════════════════════════════
# F09 错误 CRS
# ══════════════════════════════════════════════════════════════════════

@register
def f09_wrong_crs() -> ScenarioContract:
    """缺口披露型注入：错误 CRS 坐标在分析链**不被拒绝**（实测
    spatial_stats 照常完成，仅 polar warning 日志）——钉扎缺口现状 +
    判据不放行；CRS 域校验接线属 data-qualification 方向。
    """
    q = "分析这些点位的分布"
    return ScenarioContract(
        id="f09-wrong-crs",
        family="F09",
        title="错误 CRS 坐标 → 分析放行（缺口披露）+ 判据不放行",
        negative=True,
        user_goal=q,
        datasets=[DatasetSpec(name="bad", dataset_kind="wrong_crs_points")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "bad",
                                 "dataset_kind": "wrong_crs_points"}),
                _step("stats", StepKind.EXECUTE, tool="spatial_stats",
                      arguments={"features": "{{ref:bad}}"},
                      allow_error=True),
            ],
        )],
        expectations=[
            # 现状钉扎：坏数据上的分析行照常 complete（缺口成为可观测事实）
            _exp("f09-analysis-passes-through", Face.METHOD, "crs",
                 "plan.progress.status_counts.complete", op="ge", value=1,
                 note="错误 CRS 未被分析链拒绝（warning-only）——现状钉扎，"
                      "CRS 域校验落地后本期望需重校准"),
            *_pending_honest_exps("f09"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED + [
            "crs_domain_validation_gap: 分析与产品路径均无 CRS 域校验接线"
            "（仅日志 warning）；修复归 data-qualification 方向。本场景与"
            " f18 分别钉扎分析路径与产品路径的同族缺口",
        ],
        tags=["negative", "crs", "gap-disclosure"],
    )


# ══════════════════════════════════════════════════════════════════════
# F10 nodata / 单位冲突
# ══════════════════════════════════════════════════════════════════════

@register
def f10_nodata_raster() -> ScenarioContract:
    q = "这片区域做植被指数分析"
    return ScenarioContract(
        id="f10-nodata-raster",
        family="F10",
        title="含 nodata 像元的栅格运算 → 缺测不进统计",
        user_goal=q,
        datasets=[
            DatasetSpec(name="nodata", dataset_kind="raster_nodata"),
            DatasetSpec(name="schools", dataset_kind="school_points"),
        ],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("s1", StepKind.STORE_DATA,
                      arguments={"name": "nodata",
                                 "dataset_kind": "raster_nodata"}),
                _step("s2", StepKind.STORE_DATA,
                      arguments={"name": "schools",
                                 "dataset_kind": "school_points"}),
                _step("calc", StepKind.EXECUTE, tool="raster_calculator",
                      arguments={"raster_a": "{{ref:nodata}}",
                                 "expression": "A * 1"}),
                _step("stats", StepKind.EXECUTE, tool="spatial_stats",
                      arguments={"features": "{{ref:schools}}"}),
            ],
        )],
        expectations=[
            _exp("f10-bandmath-done", Face.METHOD, "tool_receipt",
                 "plan.progress.status_counts.complete", op="ge", value=2),
            *_pending_honest_exps("f10"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED + [
            "nodata_pixel_accounting: nodata 掩膜数值口径由 raster_math "
            "单测与 science oracle 覆盖；本场景断言链路行为不炸、不假成功",
        ],
        tags=["raster", "nodata"],
    )


# ══════════════════════════════════════════════════════════════════════
# F11 单位冲突
# ══════════════════════════════════════════════════════════════════════

@register
def f11_unit_conflict() -> ScenarioContract:
    """缺口披露型注入：单位混标不改变任务判错面（与正例同为 pending
    诚实降级）——单位一致性检测器属 G01/G02；本场景防止该数据形态
    被误判为满足。"""
    q = "分析这些学校的规模"
    return ScenarioContract(
        id="f11-unit-conflict",
        family="F11",
        title="字段单位混标 → 缺口披露（判据不放行）",
        negative=True,
        user_goal=q,
        datasets=[DatasetSpec(name="mixed",
                              dataset_kind="unit_conflict_points")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "mixed",
                                 "dataset_kind": "unit_conflict_points"}),
                _step("stats", StepKind.EXECUTE, tool="spatial_stats",
                      arguments={"features": "{{ref:mixed}}"},
                      allow_error=True),
            ],
        )],
        expectations=[
            *_pending_honest_exps("f11"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED + [
            "unit_invariant_detection: 单位一致性检测器由 G01/G02 方向与 "
            "data_quality 管线覆盖；本场景钉扎任务级判据不放行",
        ],
        tags=["negative", "units", "gap-disclosure"],
    )


# ══════════════════════════════════════════════════════════════════════
# F12 多轮：改图/换色带/隐藏图层（用户决策持久性 + revision 单调）
# ══════════════════════════════════════════════════════════════════════

@register
def f12_multiturn_edit_chain() -> ScenarioContract:
    """任务书 WP-B.8 的多轮链：点位图 → 隐藏热力层 → 组件调整 → 再出产品。

    断言：用户/会话已落地图层的可见性决策跨 turn 存活；revision 单调；
    agent 再组合不回退已交付图层。
    """
    q1 = "成都小学点位分布"
    q2 = "成都小学点位分布，加上标题"
    return ScenarioContract(
        id="f12-multiturn-edit-chain",
        family="F12",
        title="多轮：点位图 → 隐藏热力 → 改标题 → 再组装",
        user_goal=q1,
        datasets=[DatasetSpec(name="dense", dataset_kind="school_points_dense")],
        turns=[
            TurnSpec(
                turn_id="t1", message=q1,
                steps=[
                    _step("intent", StepKind.INTENT),
                    _step("store", StepKind.STORE_DATA,
                          arguments={"name": "dense",
                                     "dataset_kind": "school_points_dense"}),
                    _step("heat", StepKind.EXECUTE, tool="heatmap_data",
                          arguments={"geojson": "{{ref:dense}}",
                                     "render_type": "native"}),
                    _step("product", StepKind.PRODUCT,
                          arguments={"primary_ref": "{{ref:dense}}",
                                     "title": "成都市小学分布（模拟数据）"}),
                ],
            ),
            TurnSpec(
                turn_id="t2", message=q2,
                steps=[
                    _step("intent2", StepKind.INTENT),
                    _step("product2", StepKind.PRODUCT,
                          arguments={"primary_ref": "{{ref:dense}}",
                                     "title": "成都市小学点位（模拟）"}),
                    _step("title", StepKind.COMPONENT_UPDATE,
                          arguments={"component_type": "title",
                                     "options": {"text":
                                                 "成都市小学点位（模拟）"}}),
                ],
            ),
        ],
        expectations=[
            _exp("f12-t2-replaced", Face.MAPSPEC, "revision_monotonic",
                 "plan.replaced", value=True,
                 note="同 goal follow-up 必须留在同一 envelope（replaced）"),
            _exp("f12-layers-preserved", Face.MAPSPEC, "visibility_preserved",
                 "mapspec.layer_count", op="ge", value=1,
                 note="agent 再组合不得丢弃已交付图层"),
            _exp("f12-components", Face.MAPSPEC, "mapspec_component",
                 "mapspec.component_types", op="exists"),
            _exp("f12-export", Face.RENDER_EXPORT, "export_status",
                 "export.status", op="in", value=["pass", "warning"]),
            _exp("f12-revision-positive", Face.MAPSPEC, "revision_monotonic",
                 "mapspec.mutation_revision", op="ge", value=1),
            # 多轮 same-key replace 会把未携带行 void（S2 语义）——
            # needs-execution 检查不适用；保留完成面诚实缺席钉扎。
            _exp("f12-no-claim", Face.FINAL_ANSWER, "goal_verdict",
                 "map_product.goal_satisfaction_verdict",
                 expected_verdict="not_evaluated",
                 note="finalize pending（含 replace 后的 void 行），完成块"
                      "不持久化，完成面诚实缺席"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["multiturn", "user-wins"],
    )


# ══════════════════════════════════════════════════════════════════════
# F13 跨会话恢复（进程死亡 → 恢复续绘）
# ══════════════════════════════════════════════════════════════════════

@register
def f13_cross_session_recovery() -> ScenarioContract:
    """t1 中断（leave_unsettled，模拟进程死亡）→ t2 恢复续绘（kernel S5 模式）。"""
    q1 = "看看成都小学都在哪里，标出点位"
    q2 = "继续，把图画完"
    return ScenarioContract(
        id="f13-cross-session-recovery",
        family="F13",
        title="中断（未结算）→ 新 turn 恢复 → 续绘完成产品",
        user_goal=q1,
        datasets=[DatasetSpec(name="schools", dataset_kind="school_points")],
        turns=[
            TurnSpec(
                turn_id="t1", message=q1, leave_unsettled=True,
                steps=[
                    _step("intent", StepKind.INTENT),
                    _step("store", StepKind.STORE_DATA,
                          arguments={"name": "schools",
                                     "dataset_kind": "school_points"}),
                ],
            ),
            TurnSpec(
                turn_id="t2", message=q2,
                steps=[
                    _step("product", StepKind.PRODUCT,
                          arguments={"primary_ref": "{{ref:schools}}",
                                     "title": "成都市小学点位分布（模拟数据）"}),
                ],
            ),
        ],
        expectations=[
            _exp("f13-t1-interrupted", Face.RENDER_EXPORT, "render_seq",
                 "plan.turns[0].status", value="interrupted"),
            _exp("f13-resume-count", Face.MAPSPEC, "revision_monotonic",
                 "plan.recovery.resume_count", op="ge", value=1),
            _exp("f13-mapspec-after-recovery", Face.MAPSPEC, "mapspec_layer",
                 "mapspec.layer_count", op="ge", value=1),
            _exp("f13-export", Face.RENDER_EXPORT, "export_status",
                 "export.status", op="in", value=["pass", "warning"]),
            *_fail_closed_exps("f13"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED + [
            "true_process_restart: 真·跨进程恢复（Redis 信封持久化跨 pod）"
            "由 kernel S5/recovery 专属测试覆盖；本场景在 dispatch 缝内驱动"
            "同一 interrupted→resume 对账语义",
        ],
        tags=["recovery", "multiturn"],
    )


# ══════════════════════════════════════════════════════════════════════
# F14 长任务取消
# ══════════════════════════════════════════════════════════════════════

@register
def f14_user_cancel() -> ScenarioContract:
    q = "统计各区小学数量"
    return ScenarioContract(
        id="f14-user-cancel",
        family="F14",
        title="用户取消 → turn 以 cancelled 结算，不假成功",
        user_goal=q,
        datasets=[DatasetSpec(name="schools", dataset_kind="school_points")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            settle_cause="user_cancel",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "schools",
                                 "dataset_kind": "school_points"}),
            ],
        )],
        expectations=[
            _exp("f14-cancelled-status", Face.MAPSPEC, "render_seq",
                 "plan.turns[0].status", value="cancelled"),
            *_pending_honest_exps("f14"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["cancel"],
    )


# ══════════════════════════════════════════════════════════════════════
# F15 权限/治理拒绝（tier-3 工具不可经 Pi 派发）
# ══════════════════════════════════════════════════════════════════════

@register
def f15_tier3_governance() -> ScenarioContract:
    """负例（治理）：script 注入直调 tier-3 破坏性工具 → dispatch 缝必须拒绝。"""
    q = "清理一下会话数据"
    return ScenarioContract(
        id="f15-tier3-governance",
        family="F15",
        title="tier-3 破坏性工具经 Pi 缝 → TIER3_CONFIRMATION_REQUIRED",
        negative=True,
        user_goal=q,
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT, allow_error=True),
                # tier-3 破坏性工具：Pi 缝必须 typed 拒绝（expect_error 注入）
                _step("danger", StepKind.RAW, tool="refresh_skill_surface",
                      arguments={}, expect_error=True),
            ],
        )],
        expectations=[
            *_pending_honest_exps("f15"),
            _exp("f15-intent-or-empty", Face.INTENT, "intent_resolved",
                 "chapter_summary", op="exists",
                 note="治理拒绝后章节状态诚实（无完成面）"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["governance", "tier3", "negative"],
    )


# ══════════════════════════════════════════════════════════════════════
# F16 导出能力
# ══════════════════════════════════════════════════════════════════════

@register
def f16_export_offline() -> ScenarioContract:
    q = "看看成都小学都在哪里，标出点位，导出成图"
    return ScenarioContract(
        id="f16-export-offline",
        family="F16",
        title="产品导出（离线）→ 导出语义一致性 manifest 判定",
        user_goal=q,
        datasets=[DatasetSpec(name="schools", dataset_kind="school_points")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "schools",
                                 "dataset_kind": "school_points"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:schools}}",
                                 "title": "成都市小学点位分布（模拟数据）"}),
                _step("export", StepKind.RAW, tool="export_thematic_map",
                      arguments={"title": "成都市小学点位分布（模拟数据）"},
                      allow_error=True),
            ],
        )],
        expectations=[
            _exp("f16-mapspec", Face.MAPSPEC, "mapspec_layer",
                 "mapspec.layer_count", op="ge", value=1),
            _exp("f16-export-consistency", Face.RENDER_EXPORT, "export_status",
                 "export.status", op="in",
                 value=["pass", "warning", "unsupported", "not_evaluated"],
                 note="导出能力缺席时必须诚实 unsupported/not_evaluated，"
                      "不得假 pass"),
            _exp("f16-no-fake-pass", Face.RENDER_EXPORT, "export_aspect",
                 "export.failed_aspects", op="absent",
                 note="有图层产品的导出一致性不得落在 fail"),
            *_fail_closed_exps("f16"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["export"],
    )


# ══════════════════════════════════════════════════════════════════════
# F17 渲染代次/信封
# ══════════════════════════════════════════════════════════════════════

@register
def f17_render_work_envelope() -> ScenarioContract:
    q = "成都小学热力图"
    return ScenarioContract(
        id="f17-render-work-envelope",
        family="F17",
        title="产品渲染工作信封（render_work.v1）投影与 revision 对齐",
        user_goal=q,
        datasets=[DatasetSpec(name="dense", dataset_kind="school_points_dense")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "dense",
                                 "dataset_kind": "school_points_dense"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:dense}}",
                                 "title": "成都市小学热力图（模拟数据）"}),
            ],
        )],
        expectations=[
            _exp("f17-envelope-exists", Face.RENDER_EXPORT, "render_seq",
                 "render_work.exists", value=True),
            _exp("f17-schema-version", Face.RENDER_EXPORT, "render_seq",
                 "render_work.schema_version", value="render_work.v1"),
            _exp("f17-layer-works", Face.RENDER_EXPORT, "render_seq",
                 "render_work.layer_work_count", op="ge", value=1),
            _exp("f17-revision-aligned", Face.RENDER_EXPORT, "render_seq",
                 "render_work.revision", op="ge", value=1),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["render-work"],
    )


# ══════════════════════════════════════════════════════════════════════
# F18 错误数据集选择（判错归因注入）
# ══════════════════════════════════════════════════════════════════════

@register
def f18_wrong_dataset_attribution() -> ScenarioContract:
    """负例（wrong_dataset 注入）：任务要学校分布，script 用错误 CRS 点集。

    判错面必须与 F01 正例可区分（verdict 更差/图层缺席）。
    """
    q = "看看成都小学都在哪里，标出点位"
    return ScenarioContract(
        id="f18-wrong-dataset",
        family="F18",
        title="错误数据集注入 → 判错面与正例可区分",
        negative=True,
        user_goal=q,
        datasets=[DatasetSpec(name="bad", dataset_kind="wrong_crs_points")],
        turns=[TurnSpec(
            turn_id="t1", message=q,
            steps=[
                _step("intent", StepKind.INTENT),
                _step("store", StepKind.STORE_DATA,
                      arguments={"name": "bad",
                                 "dataset_kind": "wrong_crs_points"}),
                _step("product", StepKind.PRODUCT,
                      arguments={"primary_ref": "{{ref:bad}}",
                                 "title": "成都市小学点位分布（模拟数据）"},
                      allow_error=True),
            ],
        )],
        expectations=[
            _exp("f18-not-satisfied", Face.FINAL_ANSWER, "goal_verdict",
                 "map_product.goal_satisfaction_verdict", op="not_in",
                 value=["satisfied"],
                 note="错误数据下任务判据不得满足（实测落块为 terminal 态）"),
            _exp("f18-blocking-reason", Face.DATA, "dataset_ref",
                 "map_product.product_verdict", op="in",
                 value=["BLOCKED_BY_DATA", "BLOCKED_BY_METHOD",
                        "NEEDS_REPAIR", "READY_WITH_WARNINGS", "FAILED"],
                 note="终验落块必须携带可归因的裁决词"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED + [
            "crs_domain_violation_not_blocked_at_product: 错误 CRS 坐标在"
            "产品路径只触发分析侧告警（to_utm_gdf polar warning），点位层"
            "仍被组装——CRS 域校验的产品级接线属 data-qualification 方向，"
            "本分支如实披露该覆盖缺口（不造第二判定面）",
        ],
        tags=["negative", "wrong-dataset", "attribution"],
    )


# ══════════════════════════════════════════════════════════════════════
# F19 requirement 文档多轮演进
# ══════════════════════════════════════════════════════════════════════

@register
def f19_requirement_multiturn() -> ScenarioContract:
    """多轮意图重放 → RequirementDocument 持续存在且 envelope 不漂移。"""
    q1 = "看看成都小学都在哪里，标出点位"
    q2 = "加上各区数量的说明"
    return ScenarioContract(
        id="f19-requirement-multiturn",
        family="F19",
        title="多轮意图重放 → requirement 文档存活 + 同 envelope",
        user_goal=q1,
        datasets=[DatasetSpec(name="schools", dataset_kind="school_points")],
        turns=[
            TurnSpec(
                turn_id="t1", message=q1,
                steps=[
                    _step("intent", StepKind.INTENT),
                    _step("store", StepKind.STORE_DATA,
                          arguments={"name": "schools",
                                     "dataset_kind": "school_points"}),
                ],
            ),
            TurnSpec(
                turn_id="t2", message=q2,
                steps=[
                    _step("intent2", StepKind.INTENT),
                    _step("product", StepKind.PRODUCT,
                          arguments={"primary_ref": "{{ref:schools}}",
                                     "title": "成都市小学点位分布（模拟数据）"}),
                ],
            ),
        ],
        expectations=[
            _exp("f19-requirement-persist", Face.REQUIREMENT,
                 "requirement_state", "requirement.exists", value=True),
            _exp("f19-revision-positive", Face.REQUIREMENT,
                 "requirement_state", "requirement.revision", op="ge",
                 value=1, note="多轮 patch/重放下文档 revision 前进"),
            _exp("f19-same-envelope", Face.INTENT, "intent_resolved",
                 "plan.superseded", value=False,
                 note="同主题 follow-up 不应 supersede 成无关新世界"),
            _exp("f19-mapspec", Face.MAPSPEC, "mapspec_layer",
                 "mapspec.layer_count", op="ge", value=1),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["multiturn", "requirement"],
    )


# ══════════════════════════════════════════════════════════════════════
# G3D04 · F20–F26 三维意图/表达/相机/分析/交互/编排/恢复 场景族
# ══════════════════════════════════════════════════════════════════════

_EXTRUSION_LAYER = {
    "id": "bldg", "type": "fill-extrusion", "source": "features",
    "extrusion": {"height_field": "height", "stats": {"valid": 25}},
}


def _interaction_step(call_id: str, events: list) -> AgentStep:
    return _step(call_id, StepKind.INTERACTION_EVENT, arguments={"events": events})


# ── F20 3D 意图理解（cue → Requirement IR typed 投影） ────────────────

@register
def f20_01_terrain_intent() -> ScenarioContract:
    return ScenarioContract(
        id="f20-01-terrain-intent", family="F20",
        title="3D 意图：立体地形 → dimension=3d + terrain 表达",
        user_goal="展示成都市的立体地形",
        turns=[TurnSpec(turn_id="t1", message="展示成都市的立体地形",
                        steps=[_step("intent", StepKind.INTENT)])],
        expectations=[
            _exp("f20-01-dim", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.dimension", value="3d"),
            _exp("f20-01-terrain", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.terrain_requested", value=True),
            _exp("f20-01-lock", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.scene_locks", op="contains",
                 value="scene_mode=3d"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED, tags=["scene3d", "intent"],
    )


@register
def f20_02_extrusion_intent() -> ScenarioContract:
    return ScenarioContract(
        id="f20-02-extrusion-intent", family="F20",
        title="3D 意图：建筑白模 → extrusion_requested + 证据义务",
        user_goal="添加建筑白模",
        turns=[TurnSpec(turn_id="t1", message="添加建筑白模",
                        steps=[_step("intent", StepKind.INTENT)])],
        expectations=[
            _exp("f20-02-extrusion", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.extrusion_requested", value=True),
            _exp("f20-02-no-dim", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.dimension", value="",
                 note="白模 cue 是挤出表达，不隐含整图维度偏好"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED, tags=["scene3d", "intent"],
    )


@register
def f20_03_no_inference() -> ScenarioContract:
    """零推断负例：无 3D 词面的分布请求不得产生任何 3D 需求。"""
    return ScenarioContract(
        id="f20-03-no-inference", family="F20", negative=True,
        title="零推断：分布请求 → scene 全默认",
        user_goal="成都小学分布情况",
        turns=[TurnSpec(turn_id="t1", message="成都小学分布情况",
                        steps=[_step("intent", StepKind.INTENT)])],
        expectations=[
            _exp("f20-03-dim", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.dimension", value=""),
            _exp("f20-03-terrain", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.terrain_requested", value=None),
            _exp("f20-03-goals", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.analysis_goals", value=[]),
        ],
        not_evaluated=COMMON_NOT_EVALUATED, tags=["scene3d", "intent", "negative"],
    )


@register
def f20_04_composite_intent() -> ScenarioContract:
    return ScenarioContract(
        id="f20-04-composite-intent", family="F20",
        title="复合 3D 意图：维度+地形+方位+视域+PNG 媒介",
        user_goal="成都小学分布加立体地形，从西南方向看，再算可视域，导出 PNG",
        turns=[TurnSpec(
            turn_id="t1",
            message="成都小学分布加立体地形，从西南方向看，再算可视域，导出 PNG",
            steps=[_step("intent", StepKind.INTENT)])],
        expectations=[
            _exp("f20-04-dim", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.dimension", value="3d"),
            _exp("f20-04-bearing", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.camera_bearing", value=45.0),
            _exp("f20-04-goal", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.analysis_goals", op="contains",
                 value="viewshed"),
            _exp("f20-04-medium", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.analysis_goals", op="contains",
                 value="viewshed"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED, tags=["scene3d", "intent", "composite"],
    )


# ── F21 表达决策与诚实降级 ────────────────────────────────────────────

@register
def f21_01_extrusion_evidence_applies_3d() -> ScenarioContract:
    """正例链：挤出证据层（真实 dispatch）→ 决策 3d → SetScene 事务。"""
    return ScenarioContract(
        id="f21-01-extrusion-3d-applied", family="F21",
        title="表达链：白模证据 → apply_scene_representation → scene=3d",
        user_goal="展示成都建筑白模（含高度数据）",
        turns=[TurnSpec(
            turn_id="t1", message="展示成都建筑白模（含高度数据）",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("bldg", StepKind.RAW, tool="webgis_layer_upsert",
                      arguments={"layer": dict(_EXTRUSION_LAYER)}),
                _step("apply", StepKind.RAW, tool="apply_scene_representation",
                      arguments={"user_dimension": "3d"}),
            ])],
        expectations=[
            _exp("f21-01-mode", Face.MAPSPEC, "mapspec_scene",
                 "mapspec.scene.mode", value="3d"),
            _exp("f21-01-revision", Face.MAPSPEC, "revision_monotonic",
                 "mapspec.mutation_revision", op="ge", value=1),
            _exp("f21-01-verdict", Face.RENDER_EXPORT, "scene3d_verdict",
                 "scene3d.verdict", op="in", value=["completed", "degraded"],
                 note="挤出 3d 无 terrain 时 scene_quality 或有 warning，"
                      "degraded 是诚实结论"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED, tags=["scene3d", "representation"],
    )


@register
def f21_02_no_evidence_honest_degrade() -> ScenarioContract:
    """负例（诚实降级）：用户显式 3D 无证据 → 不上 3d + 偏好保留。"""
    return ScenarioContract(
        id="f21-02-no-evidence-degrade", family="F21", negative=True,
        title="诚实降级：显式 3D 无证据 → 2d + degradations + 偏好保留",
        user_goal="把地图切换成 3D 视图",
        turns=[TurnSpec(
            turn_id="t1", message="把地图切换成 3D 视图",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("apply", StepKind.RAW, tool="apply_scene_representation",
                      arguments={"user_dimension": "3d"}),
            ])],
        expectations=[
            _exp("f21-02-not-3d", Face.MAPSPEC, "mapspec_scene",
                 "mapspec.scene.mode", op="ne", value="3d",
                 note="无证据绝不 3d（fail-closed）"),
            _exp("f21-02-pref-kept", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.dimension", value="3d",
                 note="降级不撤回用户偏好（IR 保留）"),
            _exp("f21-02-degrade-recorded", Face.REQUIREMENT,
                 "requirement_state", "requirement.scene.degrade_reason",
                 op="regex", value="SCENE_USER_3D_NO_HEIGHT_EVIDENCE",
                 note="review P1-4：降级注记落 IR 跨轮可见（D4 披露闭环）"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "representation", "negative", "honesty"],
    )


@register
def f21_03_static_medium_degrades() -> ScenarioContract:
    return ScenarioContract(
        id="f21-03-static-medium", family="F21", negative=True,
        title="静态媒介：打印用 3D → 降级非 3d",
        user_goal="导出一张打印用的立体地图",
        turns=[TurnSpec(
            turn_id="t1", message="导出一张打印用的立体地图",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("apply", StepKind.RAW, tool="apply_scene_representation",
                      arguments={"user_dimension": "3d", "medium": "print"}),
            ])],
        expectations=[
            _exp("f21-03-not-3d", Face.MAPSPEC, "mapspec_scene",
                 "mapspec.scene.mode", op="ne", value="3d"),
            _exp("f21-03-medium", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.dimension", value="3d"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "representation", "negative"],
    )


@register
def f21_04_terrain_unavailable_typed() -> ScenarioContract:
    """能力发现：无 DEM → terrain unavailable + alternatives（typed）。"""
    return ScenarioContract(
        id="f21-04-terrain-unavailable", family="F21", negative=True,
        title="能力门：无 raster-dem 源 → terrain unavailable + alternatives",
        user_goal="叠加地形起伏效果",
        turns=[TurnSpec(
            turn_id="t1", message="叠加地形起伏效果",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("init", StepKind.RAW, tool="webgis_project_init",
                      arguments={}),
                _step("caps", StepKind.RAW, tool="get_3d_capabilities",
                      arguments={}),
            ])],
        expectations=[
            _exp("f21-04-pref", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.terrain_requested", value=True),
            _exp("f21-04-verdict-na", Face.RENDER_EXPORT, "scene3d_verdict",
                 "scene3d.verdict", value="completed",
                 note="纯能力探测无 scene 写入，无 3D 面可判——completed "
                      "是『探测完成』而非『3D 完成』（display 门 not_applicable）"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "capability", "negative"],
    )


@register
def f21_05_dangling_terrain_rejected() -> ScenarioContract:
    """边界（既有工具守卫）：set_map_scene 悬空 terrain.source 必须被拒。"""
    return ScenarioContract(
        id="f21-05-dangling-terrain", family="F21", negative=True,
        title="边界：悬空 terrain.source → set_map_scene 拒绝",
        user_goal="叠加不存在的地形源",
        turns=[TurnSpec(
            turn_id="t1", message="叠加不存在的地形源",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("init", StepKind.RAW, tool="webgis_project_init",
                      arguments={}),
                _step("bad", StepKind.RAW, tool="set_map_scene",
                      arguments={"scene": {"mode": "2.5d",
                                           "terrain": {"source": "ghost"}}},
                      expect_error=True),
            ])],
        expectations=[
            _exp("f21-05-no-scene", Face.MAPSPEC, "mapspec_scene",
                 "mapspec.scene", value=None,
                 note="被拒事务不得留下半成品 scene"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "negative", "guard"],
    )


# ── F22 相机动作与 user-wins ──────────────────────────────────────────

@register
def f22_01_fly_camera_applies() -> ScenarioContract:
    return ScenarioContract(
        id="f22-01-camera-applies", family="F22",
        title="相机链：fly_camera → SetView 事务（pitch/bearing 落 spec）",
        user_goal="从西南方向看这座城市",
        turns=[TurnSpec(
            turn_id="t1", message="从西南方向看这座城市",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("fly", StepKind.RAW, tool="fly_camera",
                      arguments={"pitch": 45.0, "bearing": 45.0}),
            ])],
        expectations=[
            _exp("f22-01-pitch", Face.MAPSPEC, "mapspec_view",
                 "mapspec.view.pitch", value=45.0),
            _exp("f22-01-bearing", Face.MAPSPEC, "mapspec_view",
                 "mapspec.view.bearing", value=45.0),
            _exp("f22-01-ir", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.camera_bearing", value=45.0,
                 note="用户方位话语进 IR（cue）"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED, tags=["scene3d", "camera"],
    )


@register
def f22_02_user_camera_suppresses_agent() -> ScenarioContract:
    """user-wins 反例：用户 viewport 活跃 → agent 相机动作 typed 抑制。"""
    return ScenarioContract(
        id="f22-02-user-wins-camera", family="F22", negative=True,
        title="user-wins：用户手动调镜头后 fly_camera 被抑制",
        user_goal="看地图（用户随后手动调整视角）",
        turns=[TurnSpec(
            turn_id="t1", message="看地图（用户随后手动调整视角）",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("init", StepKind.RAW, tool="webgis_project_init",
                      arguments={}),
                _interaction_step("vp", [{
                    "kind": "viewport_settled", "client_seq": 1,
                    "payload": {"center": [104.0, 30.6], "zoom": 11.0,
                                "bearing": 88.0, "pitch": 65.0}}]),
                _step("fly", StepKind.RAW, tool="fly_camera",
                      arguments={"pitch": 30.0},
                      allow_error=False),
            ])],
        expectations=[
            _exp("f22-02-viewport", Face.DATA, "interaction_state",
                 "interaction.viewport.pitch", value=65.0,
                 note="Interaction IR 摄入可观测"),
            _exp("f22-02-not-yanked", Face.MAPSPEC, "mapspec_view",
                 "mapspec.view.pitch", op="ne", value=30.0,
                 note="agent 相机不得偷改用户镜头"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "camera", "user-wins", "negative", "interaction"],
    )


@register
def f22_03_camera_scene_composition() -> ScenarioContract:
    return ScenarioContract(
        id="f22-03-camera-scene", family="F22",
        title="组合：场景切换后相机跟随（3d 档位相机）",
        user_goal="切成 3D 视图并压低视角俯仰角 30 度",
        turns=[TurnSpec(
            turn_id="t1", message="切成 3D 视图并压低视角俯仰角 30 度",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("bldg", StepKind.RAW, tool="webgis_layer_upsert",
                      arguments={"layer": dict(_EXTRUSION_LAYER)}),
                _step("apply", StepKind.RAW, tool="apply_scene_representation",
                      arguments={"user_dimension": "3d"}),
                _step("fly", StepKind.RAW, tool="fly_camera",
                      arguments={"pitch": 30.0, "bearing": -15.0}),
            ])],
        expectations=[
            _exp("f22-03-mode", Face.MAPSPEC, "mapspec_scene",
                 "mapspec.scene.mode", value="3d"),
            _exp("f22-03-pitch", Face.MAPSPEC, "mapspec_view",
                 "mapspec.view.pitch", value=30.0),
            _exp("f22-03-ir-pitch", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.camera_pitch", value=30.0),
        ],
        not_evaluated=COMMON_NOT_EVALUATED, tags=["scene3d", "camera", "composition"],
    )


@register
def f22_04_repeat_camera_stable() -> ScenarioContract:
    return ScenarioContract(
        id="f22-04-repeat-camera", family="F22",
        title="重复请求：相同相机载荷重复下发 → 视图值稳定",
        user_goal="从东北方向看，再说一次",
        turns=[
            TurnSpec(turn_id="t1", message="从东北方向看",
                     steps=[_step("intent", StepKind.INTENT),
                            _step("fly", StepKind.RAW, tool="fly_camera",
                                  arguments={"bearing": -135.0})]),
            TurnSpec(turn_id="t2", message="从东北方向看（重复）",
                     steps=[_step("intent2", StepKind.INTENT),
                            _step("fly2", StepKind.RAW, tool="fly_camera",
                                  arguments={"bearing": -135.0})]),
        ],
        expectations=[
            _exp("f22-04-bearing", Face.MAPSPEC, "mapspec_view",
                 "mapspec.view.bearing", value=-135.0),
            _exp("f22-04-ir-once", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.camera_bearing", value=-135.0),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "camera", "idempotent", "multiturn"],
    )


# ── F23 3D 分析（viewshed 编排 + typed decline） ─────────────────────

@register
def f23_01_viewshed_decline_no_dem() -> ScenarioContract:
    return ScenarioContract(
        id="f23-01-viewshed-no-dem", family="F23", negative=True,
        title="分析门：无 DEM 能力 → run_3d_analysis typed decline",
        user_goal="计算塔台的通视范围",
        turns=[TurnSpec(
            turn_id="t1", message="计算塔台的通视范围",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("init", StepKind.RAW, tool="webgis_project_init",
                      arguments={}),
                _step("run", StepKind.RAW, tool="run_3d_analysis",
                      arguments={"analysis": "viewshed",
                                 "raster_path": "dem.tif",
                                 "observer_x": 104.0, "observer_y": 30.6},
                      expect_error=True),
            ])],
        expectations=[
            _exp("f23-01-goal", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.analysis_goals", op="contains",
                 value="viewshed"),
            _exp("f23-01-no-scene", Face.MAPSPEC, "mapspec_scene",
                 "mapspec.scene", value=None,
                 note="decline 不产生任何场景/分析副作用"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "analysis", "negative", "decline"],
    )


@register
def f23_02_missing_raster_path_decline() -> ScenarioContract:
    return ScenarioContract(
        id="f23-02-missing-path", family="F23", negative=True,
        title="分析门：缺 raster_path → typed decline（不猜路径）",
        user_goal="算视域（没给数据）",
        turns=[TurnSpec(
            turn_id="t1", message="算视域（没给数据）",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("run", StepKind.RAW, tool="run_3d_analysis",
                      arguments={"analysis": "viewshed",
                                 "observer_x": 1.0, "observer_y": 1.0},
                      expect_error=True),
            ])],
        expectations=[
            _exp("f23-02-goal", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.analysis_goals", op="contains",
                 value="viewshed"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "analysis", "negative", "decline"],
    )


@register
def f23_03_pending_kind_decline() -> ScenarioContract:
    return ScenarioContract(
        id="f23-03-skyline-pending", family="F23", negative=True,
        title="分析门：skyline 待 G3D03 → typed decline + alternatives",
        user_goal="评估天际线影响",
        turns=[TurnSpec(
            turn_id="t1", message="评估天际线影响",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("init", StepKind.RAW, tool="webgis_project_init",
                      arguments={}),
                _step("run", StepKind.RAW, tool="run_3d_analysis",
                      arguments={"analysis": "skyline"}, expect_error=True),
            ])],
        expectations=[
            _exp("f23-03-goal", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.analysis_goals", op="contains",
                 value="skyline"),
            _exp("f23-03-no-side-effect", Face.MAPSPEC, "mapspec_scene",
                 "mapspec.scene", value=None),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "analysis", "negative", "decline"],
    )


@register
def f23_04_missing_observer_decline() -> ScenarioContract:
    return ScenarioContract(
        id="f23-04-missing-observer", family="F23", negative=True,
        title="分析门：缺观察点 → typed decline",
        user_goal="算个视域（没说观察点）",
        turns=[TurnSpec(
            turn_id="t1", message="算个视域（没说观察点）",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("run", StepKind.RAW, tool="run_3d_analysis",
                      arguments={"analysis": "viewshed",
                                 "raster_path": "dem.tif"},
                      expect_error=True),
            ])],
        expectations=[
            _exp("f23-04-goal", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.analysis_goals", op="contains",
                 value="viewshed"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "analysis", "negative", "decline"],
    )


# ── F24 交互失效（Interaction IR 感知） ──────────────────────────────

@register
def f24_01_layer_hidden_override() -> ScenarioContract:
    return ScenarioContract(
        id="f24-01-layer-hidden", family="F24",
        title="交互：隐藏结果层 → user_overrides 投影可观测",
        user_goal="出图后隐藏白模层",
        turns=[TurnSpec(
            turn_id="t1", message="出图后隐藏白模层",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("bldg", StepKind.RAW, tool="webgis_layer_upsert",
                      arguments={"layer": dict(_EXTRUSION_LAYER)}),
                _interaction_step("hide", [{
                    "kind": "layer_visibility_changed", "client_seq": 1,
                    "client_mutation_id": "m-hide-1",
                    "payload": {"layer_id": "bldg", "visible": False}}]),
            ])],
        expectations=[
            _exp("f24-01-layer", Face.MAPSPEC, "mapspec_layer",
                 "mapspec.layer_count", op="ge", value=1),
        ],
        not_evaluated=[
            *COMMON_NOT_EVALUATED,
            "interaction_mutation_link: layer_visibility_changed 的 mutation "
            "链接需要前端真实 client_mutation_id 与 mapspec mutation 对账，"
            "离线注入无对应 mutation → attempted_not_applied 语义（不伪造 committed）",
        ],
        tags=["scene3d", "interaction"],
    )


@register
def f24_02_observer_selection() -> ScenarioContract:
    return ScenarioContract(
        id="f24-02-observer-selected", family="F24",
        title="交互：点选新观察点 → selection 投影（失效裁决输入面）",
        user_goal="看地图并点选一个山头",
        turns=[TurnSpec(
            turn_id="t1", message="看地图并点选一个山头",
            steps=[
                _step("intent", StepKind.INTENT),
                _interaction_step("sel", [{
                    "kind": "feature_selected", "client_seq": 1,
                    "payload": {"layer_id": "peaks", "feature_id": "peak-b"}}]),
            ])],
        expectations=[
            _exp("f24-02-selection", Face.DATA, "interaction_state",
                 "interaction.selection.feature_id", value="peak-b"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED, tags=["scene3d", "interaction"],
    )


@register
def f24_03_aoi_drawn() -> ScenarioContract:
    return ScenarioContract(
        id="f24-03-aoi-drawn", family="F24",
        title="交互：绘制 AOI → active_aoi 投影",
        user_goal="圈一个分析范围",
        turns=[TurnSpec(
            turn_id="t1", message="圈一个分析范围",
            steps=[
                _step("intent", StepKind.INTENT),
                _interaction_step("aoi", [{
                    "kind": "aoi_drawn", "client_seq": 1,
                    "payload": {"geometry_type": "Polygon",
                                "vertex_count": 5}}]),
            ])],
        expectations=[
            _exp("f24-03-aoi", Face.DATA, "interaction_state",
                 "interaction.active_aoi.geometry_type", value="Polygon"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED, tags=["scene3d", "interaction"],
    )


# ── F25 多轮 3D 需求演进 ─────────────────────────────────────────────

@register
def f25_01_dimension_change_multiturn() -> ScenarioContract:
    return ScenarioContract(
        id="f25-01-dimension-change", family="F25",
        title="多轮：2D → 3D 维度演进（IR patch + 锁换新）",
        user_goal="成都小学分布（随后切 3D）",
        turns=[
            TurnSpec(turn_id="t1", message="成都小学分布",
                     steps=[_step("intent", StepKind.INTENT)]),
            TurnSpec(turn_id="t2", message="切成 3D 视图",
                     steps=[_step("intent2", StepKind.INTENT)]),
        ],
        expectations=[
            _exp("f25-01-dim", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.dimension", value="3d"),
            _exp("f25-01-lock", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.scene_locks", op="contains",
                 value="scene_mode=3d"),
            _exp("f25-01-revision", Face.REQUIREMENT, "requirement_state",
                 "requirement.revision", op="ge", value=1),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "multiturn", "requirement"],
    )


@register
def f25_02_dimension_back_to_2d() -> ScenarioContract:
    return ScenarioContract(
        id="f25-02-back-to-2d", family="F25",
        title="多轮：3D → 2D 回切（用户改主意，锁换新不挡 owner）",
        user_goal="先 3D 后改回平面",
        turns=[
            TurnSpec(turn_id="t1", message="切成 3D 视图",
                     steps=[_step("intent", StepKind.INTENT)]),
            TurnSpec(turn_id="t2", message="改成 2D 平面地图",
                     steps=[_step("intent2", StepKind.INTENT)]),
        ],
        expectations=[
            _exp("f25-02-dim", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.dimension", value="2d"),
            _exp("f25-02-lock", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.scene_locks", op="contains",
                 value="scene_mode=2d"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "multiturn", "user-wins"],
    )


@register
def f25_03_scene_preference_survives_new_task() -> ScenarioContract:
    return ScenarioContract(
        id="f25-03-pref-survives", family="F25",
        title="跨任务：3D 偏好在新任务重建后存活（carried locks）",
        user_goal="先立体地形，再查询咖啡店",
        turns=[
            TurnSpec(turn_id="t1", message="展示成都市的立体地形",
                     steps=[_step("intent", StepKind.INTENT)]),
            TurnSpec(turn_id="t2", message="查询附近的咖啡店",
                     steps=[_step("intent2", StepKind.INTENT)]),
        ],
        expectations=[
            _exp("f25-03-dim-kept", Face.REQUIREMENT, "requirement_state",
                 "requirement.scene.dimension", value="3d",
                 note="user scene_mode 锁跨任务携带（D-05 user-wins 存活）"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "multiturn", "carry"],
    )


# ── F26 重入/恢复/中断 ───────────────────────────────────────────────

@register
def f26_01_interrupted_scene_reapply() -> ScenarioContract:
    return ScenarioContract(
        id="f26-01-interrupted-reapply", family="F26",
        title="重入：scene 应用后中断（未结算）→ 再入同 scene 稳定",
        user_goal="建筑白模 3D（中途进程被打断）",
        turns=[
            TurnSpec(
                turn_id="t1", message="展示成都建筑白模（含高度数据）",
                steps=[
                    _step("intent", StepKind.INTENT),
                    _step("bldg", StepKind.RAW, tool="webgis_layer_upsert",
                          arguments={"layer": dict(_EXTRUSION_LAYER)}),
                    _step("apply", StepKind.RAW,
                          tool="apply_scene_representation",
                          arguments={"user_dimension": "3d"}),
                ],
                leave_unsettled=True),
            TurnSpec(
                turn_id="t2", message="继续，把 3D 场景配好",
                steps=[
                    _step("intent2", StepKind.INTENT),
                    _step("reapply", StepKind.RAW,
                          tool="apply_scene_representation",
                          arguments={"user_dimension": "3d"}),
                ]),
        ],
        expectations=[
            _exp("f26-01-mode", Face.MAPSPEC, "mapspec_scene",
                 "mapspec.scene.mode", value="3d"),
            _exp("f26-01-verdict", Face.RENDER_EXPORT, "scene3d_verdict",
                 "scene3d.verdict", op="in",
                 value=["completed", "degraded"]),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "reentry", "recovery"],
    )


@register
def f26_02_camera_then_interrupt_scene_kept() -> ScenarioContract:
    return ScenarioContract(
        id="f26-02-camera-then-scene", family="F26",
        title="重入：相机已落 → 中断 → 场景再入（相机不被重置）",
        user_goal="调视角后切 3D（中途打断）",
        turns=[
            TurnSpec(
                turn_id="t1", message="从西南方向看",
                steps=[
                    _step("intent", StepKind.INTENT),
                    _step("fly", StepKind.RAW, tool="fly_camera",
                          arguments={"pitch": 40.0, "bearing": 45.0}),
                ],
                leave_unsettled=True),
            TurnSpec(
                turn_id="t2", message="切成 3D 视图",
                steps=[
                    _step("intent2", StepKind.INTENT),
                    _step("bldg", StepKind.RAW, tool="webgis_layer_upsert",
                          arguments={"layer": dict(_EXTRUSION_LAYER)}),
                    _step("apply", StepKind.RAW,
                          tool="apply_scene_representation",
                          arguments={"user_dimension": "3d"}),
                ]),
        ],
        expectations=[
            _exp("f26-02-mode", Face.MAPSPEC, "mapspec_scene",
                 "mapspec.scene.mode", value="3d"),
            _exp("f26-02-pitch-kept", Face.MAPSPEC, "mapspec_view",
                 "mapspec.view.pitch", value=40.0,
                 note="scene 应用不重置既有视图"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "reentry", "camera"],
    )


@register
def f26_03_verdict_blocked_without_evidence() -> ScenarioContract:
    """完成判据负例：scene 写入但无 mutation 证据 → verdict 不 completed。"""
    return ScenarioContract(
        id="f26-03-verdict-honesty", family="F26", negative=True,
        title="判据诚实：2.5d 悬空 terrain → verdict blocked（显示门）",
        user_goal="用地形但数据面缺席",
        turns=[TurnSpec(
            turn_id="t1", message="用地形但数据面缺席",
            steps=[
                _step("intent", StepKind.INTENT),
                _step("apply", StepKind.RAW, tool="apply_scene_representation",
                      arguments={"user_dimension": "2.5d",
                                 "terrain_requested": True}),
            ])],
        expectations=[
            _exp("f26-03-not-25d-dangling", Face.MAPSPEC, "mapspec_scene",
                 "mapspec.scene.terrain_source", value="",
                 note="无核实源绝不绑 terrain（半成品防线）"),
            _exp("f26-03-mode", Face.MAPSPEC, "mapspec_scene",
                 "mapspec.scene.mode", op="in", value=["2d", ""],
                 note="2.5d 唯一垂直通道是 terrain；无源即诚实退 2d"),
        ],
        not_evaluated=COMMON_NOT_EVALUATED,
        tags=["scene3d", "negative", "verdict", "honesty"],
    )
