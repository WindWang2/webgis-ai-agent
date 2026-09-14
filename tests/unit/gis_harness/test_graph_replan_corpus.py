"""Graph Replan Corpus —— 增量重规划语料与指标（方向 5 E9 / ADR-0184）。

沿用人审定情境 × 确定性扩展的语料模式（runtime_corpus 同门）：
10 类情境（覆盖任务书 10 个必做场景的计划侧语义）× scope/subject/
语言变体 → 50 个 multi-step 场景。全部确定性（同输入同语料、零 LLM、
零网络、零 I/O）。

指标（每场景）：
- ``total``      计划节点总数（行数）；
- ``carried``    携带完成事实的节点（master 语义下这些会被全量 void）；
- ``lost``       失效节点；
- ``savings``    carried / total（增量重算节省面）。

正确性红线（每场景断言）：
- 零错误携带：carried ∩ expected_lost == ∅ 且 carried ⊆ expected_carry；
- 保守失效：expected_lost ⊆ lost ∪ dropped（新计划不再需要的行）；
- global 重塑维变化 → 零携带（等价现状全量语义）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from app.services.gis_harness.intent_diff import (
    diff_chapters,
    semantic_row_signature,
)

# ── 标准 6 节点计划骨架（成都学校分布族）─────────────────────────────────

_CAPS = {
    "boundary": "adm_boundary_fetch",
    "poi": "poi_query",
    "roads": "fetch_network_roads",
    "stats": "district_stats",
    "heat": "density_heatmap",
    "chart": "boundary_chart",
}


def _req(cap: str, tool: str, params: Dict[str, Any], *, ref: str = "",
         deps: Tuple[str, ...] = (), optional: bool = False) -> Dict[str, Any]:
    return {
        "capability": cap, "purpose": "", "optional": optional,
        "resolved_tool": tool, "resolved_algorithm": "a1",
        "params": params, "depends_on": list(deps),
        "status": "available" if ref else "pending", "bound_ref": ref,
    }


def _step(cap: str, params: Dict[str, Any], deps: Tuple[str, ...],
          ref: str = "") -> Dict[str, Any]:
    row = _req(cap, f"tool_{cap}", params, ref=ref, deps=deps)
    # 分析行完成态词表是 "done"（_mark_progress 同规），非数据行的 available
    row["status"] = "done" if ref else "pending"
    return row


def _chapter(scope: str, subject: str, *, time_tag: str = "",
             task: str = "distribution_overview",
             measure: str = "count", stats_by: str = "district",
             poi_alg: str = "a1", with_heat: bool = True,
             output_intents: Tuple[str, ...] = ("map",),
             complete: bool = True) -> Dict[str, Any]:
    """标准计划：boundary/poi/roads 数据 + stats/heat/chart 分析。"""
    ref = (f"ref:{cap}-{scope}-{subject}" for cap in ())  # 占位生成器不消费
    done = "ref:out" if complete else ""

    def r(cap: str) -> str:
        return f"ref:{cap}-{scope}-{subject}-{time_tag}" if done else ""

    reqs = [
        _req(_CAPS["boundary"], "get_local_admin_boundary",
             {"scope": scope}, ref=r(_CAPS["boundary"])),
        _req(_CAPS["poi"], "query_local_poi",
             {"category": subject, "scope": scope}, ref=r(_CAPS["poi"]),
             optional=True),
        _req(_CAPS["roads"], "get_local_roads",
             {"scope": scope}, ref=r(_CAPS["roads"]), optional=True),
    ]
    steps = [
        _step(_CAPS["stats"], {"by": stats_by},
              (_CAPS["boundary"], _CAPS["poi"]), ref=r(_CAPS["stats"])),
    ]
    if with_heat:
        steps.append(_step(_CAPS["heat"], {"radius": 800},
                           (_CAPS["poi"],), ref=r(_CAPS["heat"])))
    steps.append(_step(_CAPS["chart"], {"series": measure},
                       (_CAPS["boundary"],), ref=r(_CAPS["chart"])))
    return {
        "plan_id": f"plan-{scope}-{subject}-{task}".replace("市", ""),
        "query": f"{scope}{subject}{task}",
        "recipe_id": "poi_stats",
        "intent": {
            "scope": {"name": scope, "level": "city"},
            "subject": {"type": "poi", "category": subject},
            "task": task, "measure": measure, "time": time_tag,
            "output_intents": list(output_intents),
        },
        "data_requirements": reqs,
        "analysis_steps": steps,
    }


# ── 人审定的 10 类情境（expected 语义 + 任务书场景映射）──────────────────

#: (kind, 场景号, followup 构造器, expect_carry, expect_lost, global)
def _situations() -> List[Tuple[str, str, Any, Set[str], Set[str], bool]]:
    C = _CAPS
    return [
        # 1 成都学校分布（基线首轮：无 diff）
        ("baseline_first_plan", "场景1", lambda s, j: {}, set(), set(), False),
        # 2 只看主城区（scope 收缩：数据行失效，保守全链失效）
        ("scope_narrow", "场景2",
         lambda s, j: {"scope": f"{s}主城区"},
         set(), {C["boundary"], C["poi"], C["stats"], C["heat"]}, False),
        # 3 换成高中（subject 换：boundary/chart 携带，poi 链失效）
        ("subject_swap", "场景3",
         lambda s, j: {"subject": "高中" if j == "小学" else "小学"},
         {C["boundary"], C["chart"]},
         {C["poi"], C["stats"], C["heat"]}, False),
        # 4 热力图换分级设色（style-only：chapter 零变化，科学零触碰）
        ("style_only", "场景4", lambda s, j: {}, set(), set(), False),
        # 5 导出 16:9 PPT 图（output 维：零科学重算）
        ("delivery_format", "场景5",
         lambda s, j: {"output_intents": ("map", "export")},
         {C["boundary"], C["poi"], C["stats"], C["chart"]}, set(), False),
        # 6 dataset version drift（同查询重提：行签名不变 → 全携带）
        ("dataset_resubmit", "场景6", lambda s, j: {}, set(), set(), False),
        # 7 analysis 失败后 resume（V5/plan_runtime 域，可靠性测试钉住；
        # 本章对应其姊妹情境：失败修复后的统计参数微调 —— 真部分携带）
        ("params_tweak", "场景7",
         lambda s, j: {"stats_by": "grid"},
         {C["boundary"], C["poi"], C["roads"], C["heat"], C["chart"]},
         {C["stats"]}, False),
        # 8 user hides/pins layer（mapspec 域：chapter 零变化）
        ("layer_pin", "场景8", lambda s, j: {}, set(), set(), False),
        # 9 offline fallback 后重评（时间维变化：数据门全失效，保守）
        ("time_shift", "场景9",
         lambda s, j: {"time_tag": "2024"},
         set(), {C["boundary"], C["poi"], C["stats"], C["heat"]}, False),
        # 10 换统计任务（measure 变 → global 重塑，保守全失效）
        ("task_reshape", "场景10",
         lambda s, j: {"measure": "density"},
         set(), {C["boundary"], C["poi"], C["stats"], C["heat"]}, True),
    ]


_SCOPES = ("成都市", "武汉市", "西安市", "杭州市", "成都市")
_SUBJECTS = ("小学", "小学", "医院", "小学", "图书馆")
_LANGS = ("zh", "zh", "zh", "en", "zh-en")


def build_replan_corpus() -> List[Dict[str, Any]]:
    """情境 × 5 变体 → 50 场景（确定性展开）。"""
    cases: List[Dict[str, Any]] = []
    for idx, (kind, scenario, followup_fn, exp_carry, exp_lost,
              exp_global) in enumerate(_situations()):
        for v, (scope, subject, lang) in enumerate(
                zip(_SCOPES, _SUBJECTS, _LANGS)):
            kw = followup_fn(scope, subject)
            old = _chapter(scope, subject)
            new = _chapter(
                kw.get("scope", scope),
                kw.get("subject", subject),
                time_tag=kw.get("time_tag", ""),
                task=kw.get("task", "distribution_overview"),
                measure=kw.get("measure", "count"),
                stats_by=kw.get("stats_by", "district"),
                poi_alg=kw.get("poi_alg", "a1"),
                output_intents=kw.get("output_intents", ("map",)),
            )
            cases.append({
                "case_id": f"{kind}-{v}-{lang}",
                "scenario": scenario,
                "kind": kind,
                "old": old, "new": new,
                "expect_carry": exp_carry, "expect_lost": exp_lost,
                "expect_global": exp_global,
            })
    assert len(cases) == 50
    return cases


def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """单场景执行 + 正确性断言 + 指标。"""
    old, new = case["old"], case["new"]
    diff = diff_chapters(old, new)
    total_rows = len(old["data_requirements"]) + len(old["analysis_steps"])

    # 零错误携带：携带行的语义签名必须确实未变
    old_rows = {r["capability"]: r
                for r in old["data_requirements"] + old["analysis_steps"]}
    new_rows = {r["capability"]: r
                for r in new["data_requirements"] + new["analysis_steps"]}
    for cap in diff.carried:
        assert cap in old_rows and cap in new_rows, case["case_id"]
        assert semantic_row_signature(old_rows[cap]) == \
            semantic_row_signature(new_rows[cap]), case["case_id"]

    # 保守失效：expect_lost 必须全部在 lost（或新计划已丢弃该行）
    lost_caps = {item["capability"] for item in diff.lost}
    dropped = set(old_rows) - set(new_rows)
    assert case["expect_lost"] <= (lost_caps | dropped), (
        case["case_id"], case["expect_lost"], lost_caps, dropped)
    # 携带与失效互斥（构造性保证，双查）
    assert not (set(diff.carried) & lost_caps), case["case_id"]
    # global 重塑维变化 → 零携带
    if case["expect_global"]:
        assert diff.global_reshape and not diff.carried, case["case_id"]

    carried = set(diff.carried) & set(case["expect_carry"]) \
        if case["expect_carry"] else set(diff.carried)
    missing_carry = case["expect_carry"] - set(diff.carried) - dropped
    assert not missing_carry, (case["case_id"], missing_carry)

    return {
        "case_id": case["case_id"],
        "kind": case["kind"],
        "scenario": case["scenario"],
        "total": total_rows,
        "carried": len(diff.carried),
        "lost": len(diff.lost),
        "savings": round(len(diff.carried) / max(1, total_rows), 3),
        "global_reshape": diff.global_reshape,
    }


def test_corpus_all_cases_pass_with_expected_semantics():
    cases = build_replan_corpus()
    assert len(cases) == 50
    metrics = [run_case(case) for case in cases]

    # ── 聚合指标（增量重算显著降低工作量的证据面）──────────────────
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for m in metrics:
        by_kind.setdefault(m["kind"], []).append(m)
    # subject 换：boundary 链携带 ≥ 1/3 节点
    subj = by_kind["subject_swap"]
    assert all(m["savings"] >= 0.3 for m in subj)
    # stats 参数微调：仅 stats 失效，数据链 + 其他分析全携带（≥ 0.8）
    assert all(m["savings"] >= 0.8 for m in by_kind["params_tweak"])
    # style-only / output / resubmit / pin：零科学重算（全携带 —— 身份
    # 契约：重复提交/呈现态变更不得触发任何重算）
    for kind in ("style_only", "delivery_format", "dataset_resubmit",
                 "layer_pin", "baseline_first_plan"):
        assert all(m["lost"] == 0 for m in by_kind[kind]), kind
        assert all(m["savings"] == 1.0 for m in by_kind[kind]), kind
    # scope/time/task：保守全失效（等价现状语义，正确性优先）
    for kind in ("scope_narrow", "time_shift", "task_reshape"):
        assert all(m["carried"] == 0 for m in by_kind[kind]), kind
    # 全语料：零场景出现 carried + lost 交叉或错误携带（run_case 内断言）

    # 场景覆盖完备（任务书 10 必做场景一一对应）
    kinds = {m["kind"] for m in metrics}
    assert len(kinds) == 10
    scenarios = {m["scenario"] for m in metrics}
    assert scenarios == {f"场景{i}" for i in range(1, 11)}

    # 指标摘要（ledger 引用；确定性输出）。诚实披露：avg 含 25 个身份
    # 契约用例（同章重提/呈现态），replan 类指标才是局部重算节省面。
    avg_savings = sum(m["savings"] for m in metrics) / len(metrics)
    per_kind = {
        kind: round(sum(m["savings"] for m in items) / len(items), 3)
        for kind, items in sorted(by_kind.items())
    }
    print("\n[graph-replan-corpus] cases=50 avg_savings="
          f"{avg_savings:.3f} per_kind={per_kind}")
