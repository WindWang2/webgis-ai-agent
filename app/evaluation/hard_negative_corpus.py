"""Hard-Negative Benchmark Corpus（V2；GOAL 里程碑 3/4）。

对抗性案例：语义相近但 GIS 语义相反的查询对、错误 AOI、无能力请求、
指代/多轮。每个期望值 = 2026-09-16 对照 faa453a8 的**手工审定真值**
（探针运行 + 意图/规划语义推导双确认），冻结后即回归锁。

覆盖的对抗面：
- point-vs-choropleth：点位请求诱导分级统计图 → 必须诚实降级；
- count-vs-rate：「数量最多」（count/choropleth）vs「密度最高」
  （rate/point_density）必须落到不同任务/recipe（中英双向）；
- wrong-AOI：数据覆盖外的 AOI → scope 不得被虚构（known=False 锚）；
- no-capability：融合（dissolve）类请求不得静默编造 dissolve 算法；
- coreference / 多轮：``turns`` + 绑定表 —— 指代消解由前序轮真实解析
  的前件驱动（runner 求值），非自证元数据。

全部离线、确定、零 LLM。
"""
from __future__ import annotations

from typing import List

from app.evaluation.case import (
    GISBenchmarkCase,
    ConversationTurn,
    ScopeExpectation,
)


def build_hard_negative_corpus() -> List[GISBenchmarkCase]:
    """硬负例语料（审定冻结表；按 id 排序 + 构建期守卫）。"""
    cases: List[GISBenchmarkCase] = [
        # ── point-vs-choropleth（诱导错误地图形态）──────────────────────
        GISBenchmarkCase(
            id="HN-pvc-points-choropleth-zh", name="点请求诱导分级统计图 → 诚实降级",
            group="hard-negative",
            query="用分级统计图显示这些学校点位",
            description="审定：点位展示请求不得产出 administrative_choropleth "
                        "（分级统计图需要面聚合语义）→ 降级 poi_distribution_overview",
            plan_only=True, tags=["hard-negative", "point-vs-choropleth", "zh"],
            expected_recipe="poi_distribution_overview",
            forbidden_algorithms=["admin.choropleth"],
            forbidden_warning_codes=["EQUITY_MISSING_DENOMINATOR"],
        ),
        GISBenchmarkCase(
            id="HN-pvc-count-choropleth-zh", name="count 语义 → 行政聚合 choropleth",
            group="hard-negative",
            query="成都哪个区小学数量最多",
            description="审定：数量最多 = count → administrative_statistic + "
                        "administrative_choropleth（面聚合正确形态；与 HN-cvr "
                        "密度对互为镜像）",
            plan_only=True, tags=["hard-negative", "count-vs-rate", "zh"],
            expected_task="administrative_statistic",
            expected_recipe="administrative_choropleth",
            expected_scope=ScopeExpectation(known=True, name="成都", level="city"),
        ),
        # ── count-vs-rate（统计语义对抗对）────────────────────────────────
        GISBenchmarkCase(
            id="HN-cvr-rate-density-zh", name="rate 语义 → 密度分析（非 count 聚合）",
            group="hard-negative",
            query="成都哪个区小学密度最高",
            description="审定：密度最高 = rate → concentration_analysis + "
                        "point_density（与 count 对 recipe 必须不同）",
            plan_only=True, tags=["hard-negative", "count-vs-rate", "zh"],
            expected_task="concentration_analysis",
            expected_recipe="point_density",
            expected_scope=ScopeExpectation(known=True, name="成都", level="city"),
        ),
        GISBenchmarkCase(
            id="HN-cvr-count-en", name="EN count 语义 → choropleth（双语对）",
            group="hard-negative",
            query="Which district has the most schools in Chengdu",
            description="审定：EN count → administrative_statistic（双语 count "
                        "语义稳定）",
            plan_only=True, tags=["hard-negative", "count-vs-rate", "en"],
            expected_task="administrative_statistic",
            expected_recipe="administrative_choropleth",
            expected_scope=ScopeExpectation(known=True, name="Chengdu", level="city"),
        ),
        GISBenchmarkCase(
            id="HN-cvr-rate-en-gap", name="EN rate 措辞缺口（现状锚定）",
            group="hard-negative",
            query="Which district has the highest school density in Chengdu",
            description="已知缺口锚：EN「highest density」措辞当前落到 "
                        "distribution_overview 而非密度分析（审定 2026-09-16）。"
                        "锚定现状防静默漂移；语义升级时此案例应有意识翻新。",
            scenario_kind="en-rate-phrasing-gap",
            plan_only=True, tags=["hard-negative", "count-vs-rate", "en", "known-gap"],
            expected_task="distribution_overview",
        ),
        GISBenchmarkCase(
            id="HN-cvr-percapita-denominator", name="人均语义必须携带分母披露",
            group="hard-negative",
            query="成都各区小学人均拥有量",
            description="审定：人均查询无分母事实 → RATE_MISSING_DENOMINATOR 等 "
                        "分母披露（count 伪装成 rate 的反声明锚）",
            plan_only=True, tags=["hard-negative", "count-vs-rate", "anti-claim"],
            expected_task="spatial_equity",
            expected_warning_codes=["RATE_MISSING_DENOMINATOR"],
        ),
        # ── wrong-AOI（数据覆盖外，禁止虚构绑定）──────────────────────────
        GISBenchmarkCase(
            id="HN-aoi-outside-coverage", name="覆盖外 AOI 不得虚构绑定",
            group="hard-negative",
            query="巴黎各区小学分布",
            description="审定：巴黎不在本地数据覆盖 → scope 必须留空"
                        "（known=False，level=unknown）；静默绑定默认城市 = 虚构",
            plan_only=True, tags=["hard-negative", "wrong-aoi", "anti-fabrication"],
            expected_scope=ScopeExpectation(known=False),
            expected_recipe="poi_distribution_overview",
        ),
        GISBenchmarkCase(
            id="HN-aoi-benign-twin", name="良性孪生查询正常绑定（对照锚）",
            group="hard-negative",
            query="成都市小学分布",
            description="HN-aoi-outside-coverage 的良性孪生：同句式在覆盖内 "
                        "AOI 必须正常绑定（成都市,city）—— 差分锚",
            plan_only=True, tags=["hard-negative", "wrong-aoi", "twin"],
            expected_scope=ScopeExpectation(known=True, name="成都市", level="city"),
        ),
        # ── no-capability（不得静默编造能力）──────────────────────────────
        GISBenchmarkCase(
            id="HN-cap-no-dissolve-fabrication", name="融合请求不得编造 dissolve 能力",
            group="hard-negative",
            query="把所有图层融合成一个面",
            description="审定：模糊融合请求在无面要素上下文时，plan 不得解析出 "
                        "dissolve 族算法（能力缺席必须诚实，不得静默编造）",
            plan_only=True, tags=["hard-negative", "no-capability", "anti-fabrication"],
            forbidden_algorithms=["dissolve", "vector.dissolve", "merge.dissolve"],
        ),
        # ── 多轮 / 指代消解（runner 求值的绑定，非自证声明）───────────────
        GISBenchmarkCase(
            id="HN-turn-coreference-carry", name="轮2指代继承轮1 scope（carry）",
            group="hard-negative",
            query="成都哪个区小学密度最高",
            description="审定：轮2「{scope}哪个区医院密度最高」必须继承轮1解析出的"
                        "「成都」scope（指代消解被真实求值），并落到密度分析",
            plan_only=True, tags=["hard-negative", "coreference", "multi-turn"],
            turns=[
                ConversationTurn(
                    query="{scope}哪个区医院密度最高",
                    expected_tasks=["concentration_analysis"],
                    expected_scope_binding="carry",
                    note="指代「该区」→ 继承轮1 scope；任务切换到密度语义",
                ),
            ],
        ),
        GISBenchmarkCase(
            id="HN-turn-coreference-subject-scope-switch", name="轮2换绑 scope + 继承 subject",
            group="hard-negative",
            query="成都哪个区小学数量最多",
            description="审定：轮2「重庆哪个区{subject}数量最多」换绑 scope=重庆"
                        "（new）且继承轮1 subject=小学 —— 双绑定求值",
            plan_only=True, tags=["hard-negative", "coreference", "multi-turn"],
            turns=[
                ConversationTurn(
                    query="重庆哪个区{subject}数量最多",
                    expected_task="administrative_statistic",
                    expected_scope_binding="new",
                    note="换绑 scope；subject 继承轮1（小学）",
                ),
            ],
        ),
        # ── 同义工具竞争（synonym competition，语义分流锚）────────────────
        GISBenchmarkCase(
            id="HN-syn-geocode-single", name="单点地理编码语义",
            group="hard-negative",
            query="成都天府广场的经纬度坐标是多少",
            description="审定：单点坐标查询不得触发批量地理编码（同义工具竞争："
                        "single vs batch 的分流锚）",
            plan_only=True, tags=["hard-negative", "synonym-competition"],
            forbidden_algorithms=["geocode.batch"],
        ),
    ]
    cases.sort(key=lambda c: c.id)
    # 构建期守卫：id 唯一 / query 非空 / 对抗压痕
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), f"duplicate hard-negative ids: {ids}"
    for c in cases:
        assert c.query or c.turns, f"{c.id} empty query"
    # count vs rate 镜像对必须 recipe 不相交（对抗对的语义分流保证）
    by_id = {c.id: c for c in cases}
    count_case = by_id["HN-pvc-count-choropleth-zh"]
    rate_case = by_id["HN-cvr-rate-density-zh"]
    assert count_case.expected_recipe != rate_case.expected_recipe, (
        "count/rate mirror pair must select different recipes"
    )
    return cases
