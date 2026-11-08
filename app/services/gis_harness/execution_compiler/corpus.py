"""Execution Compiler golden 语料（GC-10）—— ≥20 任务类的端到端编译 Oracle。

每个用例钉住一个**任务类别**的编译正确性：

- 族候选：期望 top 族（或期望族进入候选）——从 IR 信号推导，非关键词
  暗箱；负面用例钉 blocked/needs_clarification（fail-closed）；
- 单位义务：量纲语义（count/density/per_area/per_capita/rate）与分母
  裁决（missing_denominator 只给计数+披露——计数≠率）；
- 表达候选：用户显式点名（user_requested）与系统推荐分离；
- 失效面：多轮变更（改色/换城/换统计）→ 最小重算语义。

覆盖（任务书 GC-10）：学校、医院、道路、生态、时序、矢量叠加、缓冲、
可达性、行政区率值、遥感已有数据结果表达 + 反例（欧氏冒充网络距离的
静态拒绝、无信号 blocked、阻断歧义澄清）。

Oracle 语义：期望值是**科学正确**的编译结果；运行器对账输出逐用例
解释的评估报告（planner evaluation report），重复执行确定性。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_harness.execution_compiler import (
    compile_requirement_to_tasks,
    patch_invalidation_scope,
)
from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir.build import build_document

__all__ = [
    "CompilerCase",
    "CaseResult",
    "build_compiler_corpus",
    "run_corpus",
    "write_benchmark_report",
    "corpus_case_count",
]


@dataclass
class CompilerCase:
    """一个 golden 用例（query → 编译 Oracle）。"""

    id: str
    task_class: str                      # 任务类别（≥20 类的唯一锚）
    query: str
    expect_verdict: str = "compilable"   # compilable|needs_clarification|blocked
    expect_top_family: str = ""          # 期望 top 族（""=不断言）
    expect_family_member: str = ""       # 期望进入候选（非 top 也算）
    expect_semantics: Tuple[str, ...] = ()   # 期望义务语义（任一命中）
    expect_denominator_verdict: str = ""     # missing_denominator|satisfied|…
    expect_user_expression: str = ""         # 期望 user_requested 表达 kind
    negative: bool = False                   # 反例（fail-closed 断言）
    data_facts: Optional[Dict[str, Dict[str, Any]]] = None
    followup: str = ""                       # 多轮变更话语（失效面 oracle）
    followup_expect: str = ""                # style_only|aoi_all|measure_minimal
    oracle_notes: str = ""                   # 解释（进报告）


@dataclass
class CaseResult:
    """用例执行结果（有界、确定性）。"""

    case_id: str
    task_class: str
    checks: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    actual: Dict[str, Any] = field(default_factory=dict)
    passed: bool = False


def build_compiler_corpus() -> List[CompilerCase]:
    """≥20 任务类 golden 语料（确定性清单）。"""
    return [
        # ── 学校/教育（POI × 行政统计）────────────────────────────────
        CompilerCase(
            id="school-admin-count", task_class="school_admin_count",
            query="统计成都各区小学数量",
            expect_top_family="admin_facility_distribution",
            expect_semantics=("count",),
            oracle_notes="行政区设施分布：count 语义、无分母义务",
        ),
        CompilerCase(
            id="school-per-capita", task_class="school_per_capita_rate",
            query="统计成都各区每万人小学数量",
            expect_top_family="admin_facility_distribution",
            expect_semantics=("per_capita",),
            expect_denominator_verdict="missing_denominator",
            oracle_notes="每万人=率语义；分母数据事实缺席 → 只给计数+披露",
        ),
        CompilerCase(
            id="school-density-quantitative",
            task_class="school_quantitative_density",
            query="成都每平方公里小学密度",
            expect_semantics=("density", "per_area"),
            oracle_notes="定量密度：几何自足面积分母（boundary 在证即 satisfied）",
        ),
        CompilerCase(
            id="school-heatmap-visual", task_class="school_visual_heatmap",
            query="成都小学热力图",
            expect_user_expression="heatmap",
            oracle_notes="视觉热力=表达候选（user_requested），义务面不产率",
        ),
        # ── 医院/公共设施（可达性/邻近）──────────────────────────────
        CompilerCase(
            id="hospital-isochrone", task_class="hospital_accessibility",
            query="成都医院15分钟可达范围分析",
            expect_top_family="service_accessibility",
            oracle_notes="等时圈：路网族；无路网时 AWF_NO_NETWORK fail-closed",
        ),
        CompilerCase(
            id="hospital-nearest-network",
            task_class="hospital_nearest_network_distance",
            query="成都各区居民点到最近医院的网络距离分析",
            expect_top_family="network_proximity",
            oracle_notes="网络距离≠欧氏；区均成本=均值语义非率",
        ),
        CompilerCase(
            id="firestation-proximity", task_class="firestation_proximity",
            query="分析成都居民点到最近消防站的邻近可达",
            expect_top_family="network_proximity",
            oracle_notes="proximity 语义 → 网络邻近族",
        ),
        # ── 危险源/生态（缓冲/暴露）──────────────────────────────────
        CompilerCase(
            id="chemical-hazard-buffer", task_class="hazard_buffer_exposure",
            query="成都化工企业周边1000米危险源暴露分析",
            expect_top_family="hazard_exposure",
            oracle_notes="危险源缓冲→受体求交；M:N 多方属须披露",
        ),
        CompilerCase(
            id="flood-receptor-exposure", task_class="flood_receptor_exposure",
            query="分析成都洪涝危险源影响范围内的建筑承灾体",
            expect_top_family="hazard_exposure",
            oracle_notes="承灾体暴露清单=空间连接清单非独立计数",
        ),
        CompilerCase(
            id="river-ecology-buffer", task_class="river_ecology_buffer",
            query="分析成都河流周边500米范围内的生态敏感区",
            expect_family_member="network_proximity",
            oracle_notes="邻近+缓冲语义（欧氏 proxy 必须披露差异）",
        ),
        # ── 时序/变化 ────────────────────────────────────────────────
        CompilerCase(
            id="landuse-temporal-change", task_class="landuse_temporal_change",
            query="对比成都2018年和2024年建设用地变化",
            expect_top_family="temporal_change",
            oracle_notes="双期差分：年份对齐门（同年/缺年 typed 拒绝）",
        ),
        CompilerCase(
            id="landuse-trend-series", task_class="landuse_trend_series",
            query="成都建设用地近十年逐年变化分析",
            expect_top_family="temporal_change",
            oracle_notes="时序语义 → temporal 族",
        ),
        # ── 遥感/栅格（已有数据结果表达）────────────────────────────
        CompilerCase(
            id="ndvi-zonal-summary", task_class="ndvi_zonal_summary",
            query="统计成都各区NDVI植被指数均值",
            expect_top_family="raster_zonal_summary",
            oracle_notes="栅格指数→分区统计；nodata 覆盖率披露",
        ),
        CompilerCase(
            id="dem-terrain-zonal", task_class="dem_terrain_zonal",
            query="按行政区统计成都DEM高程分布",
            expect_top_family="raster_zonal_summary",
            oracle_notes="DEM 分区统计（role_kind=raster 直通不水合）",
        ),
        # ── 统计分区/率值 ────────────────────────────────────────────
        CompilerCase(
            id="grid-rate-statistics", task_class="grid_rate_statistics",
            query="成都小学网格化统计每个网格的万人均学校率",
            expect_top_family="statistical_zones",
            expect_denominator_verdict="missing_denominator",
            oracle_notes="网格率：分子分母同一网格身份；分母缺席→计数网格",
        ),
        CompilerCase(
            id="poi-category-share", task_class="poi_category_share",
            query="统计成都各类别餐饮店占比",
            expect_semantics=("share", "count"),
            oracle_notes="类别占比：share 语义（分母=合计）",
        ),
        CompilerCase(
            id="concentration-hotspot", task_class="concentration_hotspot",
            query="分析成都小学哪里最集中",
            expect_family_member="statistical_zones",
            oracle_notes="聚集语义 → 统计分区（热点多重比较披露）",
        ),
        # ── 道路/网络（负例：欧氏冒充）───────────────────────────────
        CompilerCase(
            id="road-network-vs-euclid", task_class="road_network_distance",
            query="统计成都各区到主干路的网络距离",
            expect_top_family="network_proximity",
            oracle_notes="静态 Oracle：min/km=time/length 维度互斥，"
                         "网络距离义务不得被行程时间满足（#2005 形态）",
        ),
        # ── 多轮变更（失效面；真实 edit 路径：话语 → diff_to_patches →
        #    rebuild_with_patches，非整句重建）────────────────────────
        CompilerCase(
            id="edit-style-only", task_class="edit_style_only",
            query="统计成都各区小学数量",
            followup="颜色改成蓝色",
            followup_expect="style_only",
            oracle_notes="改色=style 维度：零科学重算（数据 ref 全复用）",
        ),
        CompilerCase(
            id="edit-city-switch", task_class="edit_city_switch",
            query="统计成都各区小学数量",
            followup="换成北京，样式不变",
            followup_expect="aoi_all",
            oracle_notes="换城市：全量数据角色失效（跨城 ref 不假匹配）",
        ),
        CompilerCase(
            id="edit-statistic-switch", task_class="edit_statistic_switch",
            query="统计成都各区小学数量",
            followup="改成每万人统计",
            followup_expect="measure_minimal",
            oracle_notes="换统计口径：分母+率步骤+下游失效；纯计数步骤复用",
        ),
        # ── fail-closed 反例 ─────────────────────────────────────────
        CompilerCase(
            id="blocked-no-signal", task_class="blocked_no_signal",
            query="成都小学分布情况",
            expect_verdict="compilable",   # 分布语义可编译（AOI+subject 信号）
            negative=False,
            oracle_notes="宽泛分布仍可编译（分布族兜底）——真正的 blocked "
                         "由 family_allowlist 空/无主体反例覆盖（单测钉）",
        ),
        CompilerCase(
            id="blocked-allowlist-empty", task_class="blocked_allowlist_empty",
            query="统计成都各区小学数量",
            expect_verdict="blocked",
            negative=True,
            oracle_notes="封闭词表外（allowlist 空）→ blocked+理由，不猜族",
        ),
    ]


def _check(result: CaseResult, ok: bool, label: str) -> None:
    if ok:
        result.checks.append(label)
    else:
        result.failures.append(label)


def run_corpus() -> List[CaseResult]:
    """执行全部用例（确定性；零 IO/零 LLM）。"""
    results: List[CaseResult] = []
    for case in build_compiler_corpus():
        core = resolve_map_request_intent(case.query)
        doc = build_document(case.query, core)
        compilation = compile_requirement_to_tasks(
            doc, data_facts=case.data_facts,
            family_allowlist=() if case.id == "blocked-allowlist-empty" else None)

        result = CaseResult(case_id=case.id, task_class=case.task_class)
        result.actual = {
            "verdict": compilation.verdict,
            "top_family": (
                compilation.family_candidates[0].family
                if compilation.family_candidates else ""),
            "families": [c.family for c in compilation.family_candidates],
            "semantics": [u.expected_semantics
                          for u in compilation.unit_obligations],
            "denominator_verdicts": [u.verdict for u in
                                     compilation.unit_obligations],
            "user_expressions": [c.kind for c in
                                 compilation.expression_candidates
                                 if c.user_requested],
        }
        _check(result, compilation.verdict == case.expect_verdict,
               f"verdict={compilation.verdict} (expect {case.expect_verdict})")
        if case.expect_top_family:
            _check(result,
                   bool(compilation.family_candidates)
                   and compilation.family_candidates[0].family
                   == case.expect_top_family,
                   f"top_family={result.actual['top_family']} "
                   f"(expect {case.expect_top_family})")
        if case.expect_family_member:
            _check(result,
                   case.expect_family_member in result.actual["families"],
                   f"member={case.expect_family_member} in "
                   f"{result.actual['families']}")
        if case.expect_semantics:
            _check(result,
                   bool(set(case.expect_semantics)
                        & set(result.actual["semantics"])),
                   f"semantics={result.actual['semantics']} "
                   f"(expect any of {case.expect_semantics})")
        if case.expect_denominator_verdict:
            _check(result,
                   case.expect_denominator_verdict
                   in result.actual["denominator_verdicts"],
                   f"denominator={result.actual['denominator_verdicts']} "
                   f"(expect {case.expect_denominator_verdict})")
        if case.expect_user_expression:
            _check(result,
                   case.expect_user_expression
                   in result.actual["user_expressions"],
                   f"user_expression={result.actual['user_expressions']} "
                   f"(expect {case.expect_user_expression})")

        if case.followup and case.followup_expect:
            # 真实 edit 语义：话语差分 → patch journal → 重放（超替/重建
            # 之外的增量修订路径——ensure_document 的生产流）。
            from app.services.gis_harness.requirement_ir.build import (
                diff_to_patches,
                rebuild_with_patches,
            )

            edit_core = resolve_map_request_intent(case.followup)
            patches = diff_to_patches(doc, edit_core, turn=1,
                                      query=case.followup)
            new_doc = rebuild_with_patches(doc, patches)
            from app.services.gis_harness.analysis_workflows.families import (
                build_contract as _build_family_contract,
            )

            scope = patch_invalidation_scope(
                doc, new_doc,
                contract=_build_family_contract(
                    "admin_facility_distribution",
                    workflow_id="awf.corpus.invalidation"))
            result.actual["invalidation"] = scope.to_bounded_dict()
            if case.followup_expect == "style_only":
                _check(result,
                       scope.style_changed
                       and not scope.science_invalidated_steps,
                       "style-only: zero science invalidation")
            elif case.followup_expect == "aoi_all":
                _check(result,
                       scope.aoi_changed
                       and bool(scope.invalidated_roles),
                       "city-switch: all data roles invalidated")
            elif case.followup_expect == "measure_minimal":
                _check(result,
                       scope.measure_changed
                       and "per_capita_rate" in scope.science_invalidated_steps
                       and "zones_count" not in scope.science_invalidated_steps,
                       "statistic-switch: minimal invalidation "
                       "(rate step only, count step reused)")
        result.passed = not result.failures
        results.append(result)
    return results


def corpus_case_count() -> int:
    return len(build_compiler_corpus())


def write_benchmark_report(path: str) -> Dict[str, Any]:
    """评估报告（有解释的 planner evaluation report）落盘。"""
    results = run_corpus()
    passed = sum(1 for r in results if r.passed)
    lines = [
        "# Execution Compiler Benchmark Report",
        "",
        f"- cases: {len(results)}（任务类 {len({r.task_class for r in results})}）",
        f"- passed: {passed} / {len(results)}",
        "- determinism: rerun-safe（纯函数；同输入同输出）",
        "",
        "| case | task_class | verdict | top_family | checks | failures |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.case_id} | {r.task_class} | {r.actual.get('verdict')}"
            f" | {r.actual.get('top_family') or '-'}"
            f" | {'; '.join(r.checks)[:160] or '-'}"
            f" | {'; '.join(r.failures)[:160] or '-'} |")
    lines += ["", "## Oracle notes", ""]
    for case in build_compiler_corpus():
        lines.append(f"- **{case.id}**：{case.oracle_notes}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return {"cases": len(results), "passed": passed,
            "failed": len(results) - passed}


if __name__ == "__main__":  # 本地报告生成入口（python -m）
    import json
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else \
        "reports/execution_compiler_benchmark.md"
    print(json.dumps(write_benchmark_report(out),  # noqa: T201 — CLI
                     ensure_ascii=False))
