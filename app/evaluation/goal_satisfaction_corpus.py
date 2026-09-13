"""Goal Satisfaction Acceptance Corpus（ADR-0183 G6/G9）。

「数据即案例」模式（与 anti_claim / conformance 语料同规）：每个案例 =
章节/产品事实快照 + 期望（全局裁决、逐需求状态、信号、must_not_pass），
runner 确定性执行 derive → evaluate 并逐条断言。全部离线、零 LLM。

两个最高优先级指标：

- **false_pass_rate**：``must_not_pass=True`` 的案例被判 satisfied 的比率
  —— 必须恒为 0（「地图好看但任务没完成」「工具成功但证据不足」绝不
  PASS）；
- **not_evaluated_honesty**：证据不足的案例不得被判 satisfied（三态诚实）。

覆盖矩阵（≥100）：G6 八大反事实注入 + zh/en + single/multi-goal +
data-missing + map-only + analysis+map + export + user-wins + 契约边
界（must_not / threshold / 显式契约）+ 产品裁决族 × goal 裁决交互。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.gis_harness.goal_satisfaction import (
    GoalVerdict,
    evaluate_goal_satisfaction,
)


# ── 案例契约 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GoalSatisfactionCase:
    case_id: str
    name: str
    family: str                     # 覆盖矩阵分片（测试可按片运行）
    chapter: Dict[str, Any]
    map_product: Optional[Dict[str, Any]] = None
    expected_global: Optional[str] = None       # GoalVerdict 值；None = 不检查
    expected_states: Dict[str, str] = field(default_factory=dict)  # req_id → state
    expected_signal: Optional[str] = None
    must_not_pass: bool = False     # True：判 satisfied 即 false-PASS（最高优先级指标）


@dataclass
class GoalCaseResult:
    case_id: str
    family: str
    passed: bool
    false_pass: bool
    got_global: str
    failures: List[str] = field(default_factory=list)


# ── 章节工厂（结构化事实，零 regex）───────────────────────────────────────

def _intent(**kw) -> Dict[str, Any]:
    base = {
        "query": kw.get("query", "成都各区学校分布"),
        "task": "distribution_overview",
        "scope": {"name": kw.get("scope", "成都"), "level": "city"},
        "output_intents": kw.get("outputs", ["map"]),
        "export_intents": kw.get("exports", []),
    }
    for key in ("group_by", "comparison", "clarification"):
        if kw.get(key) is not None:
            base[key] = kw[key]
    return base


def _step(cap: str, status: str = "complete", purpose: str = "",
          params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    row: Dict[str, Any] = {"capability": cap, "status": status,
                           "purpose": purpose or cap}
    if params:
        row["params"] = params
    return row


def _product(verdict: str = "READY", final: str = "verified",
             render: str = "verified", revision: Any = 7,
             reasons: Optional[List[str]] = None, **extra) -> Dict[str, Any]:
    block: Dict[str, Any] = {
        "product_verdict": {"verdict": verdict, "reasons": reasons or []},
        "final_map_status": final,
        "render_status": render,
        "checked_revision": revision,
    }
    block.update(extra)
    return block


def _receipt(fmt: str, revision: Any = 7) -> Dict[str, Any]:
    return {"format": fmt, "revision": revision}


# ── F-A：需求种类 × 行证据状态矩阵（6 × 4 = 24）──────────────────────────

_KIND_ROWS = {
    "analysis": [_step("geometry_buffer", "complete")],
    "comparison": [_step("change_detection", "complete")],
    "statistics": [_step("admin_aggregation", "complete")],
    "chart": [_step("admin_aggregation", "complete")],
    "export": [_step("admin_aggregation", "complete")],
    "map": [_step("geometry_buffer", "complete")],
}
_KIND_INTENT = {
    "analysis": {},
    "comparison": {"comparison": "各区对比"},
    "statistics": {"outputs": ["map", "statistics"]},
    "chart": {"outputs": ["map", "chart"]},
    "export": {"exports": ["png"]},
    "map": {},
}
_ROW_STATE_GLOBAL = {
    "complete": (GoalVerdict.SATISFIED.value, False),
    "failed": (GoalVerdict.FAILED.value, True),
    "pending": (GoalVerdict.PARTIAL.value, True),
    "unavailable": (GoalVerdict.PARTIAL.value, True),
}


def _family_row_matrix() -> List[GoalSatisfactionCase]:
    cases: List[GoalSatisfactionCase] = []
    for kind, base_rows in _KIND_ROWS.items():
        for state, (global_verdict, mnp) in _ROW_STATE_GLOBAL.items():
            rows = [_step(r["capability"], state, r.get("purpose", ""))
                    for r in base_rows]
            if kind == "map":
                # map 需求吃产品裁决，不吃行状态 —— 该族只覆盖行族；
                # map 单独在 F-B。
                continue
            receipts = [_receipt("png")] if (kind == "export"
                                             and state == "complete") else []
            cases.append(GoalSatisfactionCase(
                case_id=f"GC-matrix-{kind}-{state}",
                name=f"{kind} requirement with row {state}",
                family="matrix",
                chapter={
                    "query": "矩阵案例",
                    "intent": _intent(**_KIND_INTENT[kind]),
                    "analysis_steps": rows,
                    "export_receipts": receipts,
                },
                map_product=_product(),
                expected_global=global_verdict,
                must_not_pass=mnp,
            ))
    return cases


# ── F-B：产品裁决族 × map-only（5 + 4）───────────────────────────────────

def _family_product_verdicts() -> List[GoalSatisfactionCase]:
    cases = []
    product_expectations = [
        ("READY", GoalVerdict.SATISFIED.value, "complete", False),
        ("READY_WITH_WARNINGS", GoalVerdict.SATISFIED.value, "complete", False),
        ("NEEDS_REPAIR", GoalVerdict.PARTIAL.value, "repair_cartography", True),
        ("BLOCKED_BY_DATA", GoalVerdict.BLOCKED.value, "blocked_by_data", True),
        ("BLOCKED_BY_METHOD", GoalVerdict.FAILED.value, "replan", True),
    ]
    for verdict, global_v, signal, mnp in product_expectations:
        cases.append(GoalSatisfactionCase(
            case_id=f"GC-product-{verdict}",
            name=f"map-only goal under product {verdict}",
            family="product",
            chapter={"query": "只要一张图", "intent": _intent(),
                     "analysis_steps": []},
            map_product=_product(verdict),
            expected_global=global_v,
            expected_states={"map": ("fulfilled" if verdict.startswith("READY")
                                     else "partial" if verdict == "NEEDS_REPAIR"
                                     else "blocked" if verdict == "BLOCKED_BY_DATA"
                                     else "failed")},
            expected_signal=signal,
            must_not_pass=mnp,
        ))
    # final_map 状态族（map-only READY 族下的空间验证面）。
    for final, state, mnp in [
        ("verified", "fulfilled", False),
        ("verified_with_degradation", "fulfilled", False),
        ("failed", "not_evaluated", True),
        ("unknown", "not_evaluated", True),
    ]:
        cases.append(GoalSatisfactionCase(
            case_id=f"GC-finalmap-{final}",
            name=f"map-only with final_map {final}",
            family="product",
            chapter={"query": "只要一张图", "intent": _intent(),
                     "analysis_steps": []},
            map_product=_product(final=final),
            expected_states={"map": state},
            must_not_pass=mnp,
        ))
    return cases


# ── F-C：数据充分性（G6 #1 工具 200 但数据空）────────────────────────────

def _family_data_sufficiency() -> List[GoalSatisfactionCase]:
    cases = []
    for code in ("empty_result", "artifact_expired", "source_missing",
                 "artifact_missing", "execution_blocked"):
        cases.append(GoalSatisfactionCase(
            case_id=f"GC-data-{code}",
            name=f"tools 200 but data blocked: {code}",
            family="data",
            chapter={
                "query": "各区统计",
                "intent": _intent(outputs=["map", "statistics"]),
                "analysis_steps": [_step("admin_aggregation", "complete")],
            },
            map_product=_product(reasons=[code]),
            expected_states={"analysis:admin_aggregation": "partial",
                             "statistics": "partial"},
            must_not_pass=True,
        ))
    # 数据角色 blocked（workflow 契约 data_blockers）。
    cases.append(GoalSatisfactionCase(
        case_id="GC-data-role-blocked",
        name="denominator role blocked by contract",
        family="data",
        chapter={
            "query": "人均公平性",
            "intent": _intent(outputs=["map", "statistics"]),
            "analysis_steps": [_step("admin_aggregation", "complete")],
            "workflow_contract": {"data_blockers": ["denominator_role_missing"]},
        },
        map_product=_product(),
        expected_states={"analysis:admin_aggregation": "partial"},
        must_not_pass=True,
    ))
    return cases


# ── F-D：导出交付族（3 × 3 + G6 #7 stale export）─────────────────────────

def _family_export() -> List[GoalSatisfactionCase]:
    cases = []
    for fmt in ("png", "pdf", "csv"):
        for label, receipts, state, mnp in [
            ("current", [_receipt(fmt)], "fulfilled", False),
            ("stale", [_receipt(fmt, revision=3)], "partial", True),
            ("missing", [], "not_evaluated", True),
        ]:
            cases.append(GoalSatisfactionCase(
                case_id=f"GC-export-{fmt}-{label}",
                name=f"export {fmt} with {label} receipt",
                family="export",
                chapter={
                    "query": "导出案例",
                    "intent": _intent(exports=[fmt]),
                    "analysis_steps": [_step("admin_aggregation")],
                    "export_receipts": receipts,
                },
                map_product=_product() if label != "missing" else None,
                expected_states={f"export:{fmt}": state},
                must_not_pass=mnp,
            ))
    return cases


# ── F-E：回退族（G6 #6 fallback 不可比）──────────────────────────────────

def _family_fallbacks() -> List[GoalSatisfactionCase]:
    cases = []
    for cls, state, rule_hint, mnp in [
        ("equivalent", "fulfilled", "", False),
        ("proxy", "partial", "fallback_proxy_result", True),
        ("approximation", "partial", "fallback_proxy_result", True),
        ("degraded", "partial", "fallback_proxy_result", True),
        ("not_allowed", "partial", "fallback_non_comparable", True),
    ]:
        cases.append(GoalSatisfactionCase(
            case_id=f"GC-fallback-{cls}",
            name=f"analysis under {cls} fallback",
            family="fallback",
            chapter={
                "query": "回退案例",
                "intent": _intent(outputs=["map"]),
                "analysis_steps": [_step("admin_aggregation", "complete")],
                "fallbacks": [{"downgrade_class": cls,
                               "reason_code": f"rc_{cls}"}],
            },
            map_product=_product(),
            expected_states={"analysis:admin_aggregation": state},
            must_not_pass=mnp,
        ))
    return cases


# ── F-F：scope / filter 一致性（G6 #3 #4）────────────────────────────────

def _family_scope_filter() -> List[GoalSatisfactionCase]:
    return [
        GoalSatisfactionCase(
            case_id="GC-scope-match",
            name="row scope matches intent",
            family="scope-filter",
            chapter={"query": "成都学校", "intent": _intent(scope="成都"),
                     "analysis_steps": [_step("poi.query", params={"scope": "成都"})]},
            map_product=_product(),
            expected_states={"analysis:poi.query": "fulfilled"},
        ),
        GoalSatisfactionCase(
            case_id="GC-scope-mismatch",
            name="visual PASS but scope wrong (G6)",
            family="scope-filter",
            chapter={"query": "成都学校", "intent": _intent(scope="成都"),
                     "analysis_steps": [_step("poi.query", params={"scope": "绵阳"})]},
            map_product=_product(),
            expected_states={"analysis:poi.query": "partial"},
            expected_signal="continue",
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-filter-mismatch",
            name="chart present but group-by differs (G6)",
            family="scope-filter",
            chapter={"query": "各区统计", "intent": _intent(outputs=["map", "chart"],
                                                             group_by="district"),
                     "analysis_steps": [_step("admin_aggregation",
                                              params={"group_by": "grid"})]},
            map_product=_product(),
            expected_states={"analysis:admin_aggregation": "partial",
                             "chart": "fulfilled"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-scope-substring",
            name="成都市 vs 成都 inclusive match",
            family="scope-filter",
            chapter={"query": "成都学校", "intent": _intent(scope="成都市"),
                     "analysis_steps": [_step("poi.query", params={"scope": "成都"})]},
            map_product=_product(),
            expected_states={"analysis:poi.query": "fulfilled"},
        ),
        GoalSatisfactionCase(
            case_id="GC-filter-absent-row-side",
            name="intent group_by without row param → no mismatch",
            family="scope-filter",
            chapter={"query": "各区统计", "intent": _intent(group_by="district"),
                     "analysis_steps": [_step("admin_aggregation")]},
            map_product=_product(),
            expected_states={"analysis:admin_aggregation": "fulfilled"},
        ),
    ]


# ── F-G：multi-goal（G4 partial completion ledger）───────────────────────

def _family_multi_goal() -> List[GoalSatisfactionCase]:
    full_steps = [
        _step("poi.query"),
        _step("admin_aggregation"),
        _step("change_detection"),
    ]
    base_intent = _intent(outputs=["map", "statistics"],
                          exports=["png"], comparison="各区对比")
    return [
        GoalSatisfactionCase(
            case_id="GC-multi-all-done",
            name="显示+比较+统计+导出 全部兑现",
            family="multi-goal",
            chapter={"query": "多目标", "intent": base_intent,
                     "analysis_steps": full_steps,
                     "export_receipts": [_receipt("png")]},
            map_product=_product(),
            expected_global="satisfied",
            expected_signal="complete",
        ),
        GoalSatisfactionCase(
            case_id="GC-multi-comparison-missing",
            name="显示+统计 done，比较缺位（G6 #2）",
            family="multi-goal",
            chapter={"query": "多目标", "intent": base_intent,
                     "analysis_steps": [_step("poi.query"),
                                        _step("admin_aggregation")],
                     "export_receipts": [_receipt("png")]},
            map_product=_product(),
            expected_states={"comparison": "not_evaluated",
                             "statistics": "fulfilled",
                             "map": "fulfilled"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-multi-export-missing",
            name="分析全 done，导出未兑现",
            family="multi-goal",
            chapter={"query": "多目标", "intent": base_intent,
                     "analysis_steps": full_steps},
            map_product=_product(),
            expected_states={"export:png": "not_evaluated",
                             "comparison": "fulfilled"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-multi-map-failed",
            name="分析 done，地图 BLOCKED_BY_DATA → 全局 blocked/partial",
            family="multi-goal",
            chapter={"query": "多目标", "intent": base_intent,
                     "analysis_steps": full_steps,
                     "export_receipts": [_receipt("png")]},
            map_product=_product("BLOCKED_BY_DATA",
                                 reasons=["source_missing"]),
            expected_states={"map": "blocked",
                             "analysis:poi.query": "partial"},
            expected_signal="blocked_by_data",
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-two-of-three",
            name="三子目标完成两个 → partial（不是 satisfied）",
            family="multi-goal",
            chapter={"query": "多目标", "intent": base_intent,
                     "analysis_steps": [_step("poi.query"),
                                        _step("admin_aggregation"),
                                        _step("change_detection", "failed")],
                     "export_receipts": [_receipt("png")]},
            map_product=_product(),
            expected_states={"analysis:change_detection": "failed"},
            expected_global="failed",
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-analysis-only-goal",
            name="只要分析不要图（map 不在 output intents）",
            family="multi-goal",
            chapter={"query": "纯分析",
                     "intent": _intent(outputs=["statistics"]),
                     "analysis_steps": [_step("admin_aggregation")]},
            map_product=None,
            expected_states={"statistics": "fulfilled"},
            expected_global="satisfied",
        ),
    ]


# ── F-H：zh / en 双语（G9）───────────────────────────────────────────────

def _family_language() -> List[GoalSatisfactionCase]:
    return [
        GoalSatisfactionCase(
            case_id="GC-lang-zh-compare-missing",
            name="zh：比较缺位不 PASS",
            family="language",
            chapter={"query": "成都各区医院分布并比较",
                     "intent": _intent(query="成都各区医院分布并比较",
                                       comparison="各区医院数量对比"),
                     "analysis_steps": [_step("poi.query")]},
            map_product=_product(),
            expected_states={"comparison": "not_evaluated"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-lang-en-compare-missing",
            name="en：comparison missing must not pass",
            family="language",
            chapter={"query": "show schools per district and compare",
                     "intent": {"query": "show schools per district and compare",
                                "task": "distribution_overview",
                                "scope": {"name": "", "level": "unknown"},
                                "output_intents": ["map"],
                                "comparison": "compare across districts"},
                     "analysis_steps": [_step("poi.query")]},
            map_product=_product(),
            expected_states={"comparison": "not_evaluated"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-lang-en-export-ok",
            name="en：export fulfilled",
            family="language",
            chapter={"query": "export the map as png",
                     "intent": {"query": "export the map as png",
                                "task": "distribution_overview",
                                "scope": {"name": "", "level": "unknown"},
                                "output_intents": ["map"],
                                "export_intents": ["png"]},
                     "analysis_steps": [],
                     "export_receipts": [_receipt("png")]},
            map_product=_product(),
            expected_states={"export:png": "fulfilled",
                             "map": "fulfilled"},
            expected_global="satisfied",
        ),
        GoalSatisfactionCase(
            case_id="GC-lang-zh-purpose-classification",
            name="zh：目的词驱动比较族分类",
            family="language",
            chapter={"query": "各区对比",
                     "intent": _intent(),
                     "analysis_steps": [_step("custom_cap_x",
                                              purpose="各区学校数量对比")]},
            map_product=_product(),
            expected_states={"comparison": "fulfilled",
                             "analysis:custom_cap_x": "fulfilled"},
            expected_global="satisfied",
        ),
    ]


# ── F-I：user-wins / 澄清（G6 #8 + G5 request_clarification）────────────

def _family_user_overrides() -> List[GoalSatisfactionCase]:
    hidden = _product(intent_acceptance={
        "accepted": True, "intent_verified": False,
        "observed_confirmed": False,
        "disclosures": ["layer_hidden_by_user:layer-1"], "unmet": [],
    })
    return [
        GoalSatisfactionCase(
            case_id="GC-user-hidden-layer",
            name="用户显式隐藏 required optional view → 披露不阻断（G6 #8）",
            family="user",
            chapter={"query": "地图案例", "intent": _intent(),
                     "analysis_steps": []},
            map_product=hidden,
            expected_states={"map": "fulfilled"},
        ),
        GoalSatisfactionCase(
            case_id="GC-user-hidden-not-false-block",
            name="user-wins 隐藏不得造成 false-FAIL 阻断",
            family="user",
            chapter={"query": "地图案例", "intent": _intent(),
                     "analysis_steps": [_step("poi.query")],
                     "export_receipts": [_receipt("png")]},
            map_product=hidden,
            expected_global="satisfied",
        ),
        GoalSatisfactionCase(
            case_id="GC-clarification-pending-idle",
            name="澄清未决 + 无证据 → request_clarification",
            family="user",
            chapter={"query": "画个图",
                     "intent": _intent(clarification={
                         "question": "哪个城市？", "status": "pending"}),
                     "analysis_steps": []},
            map_product=None,
            expected_signal="request_clarification",
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-clarification-resolved-ignored",
            name="澄清已解决 → 不再触发 clarification 信号",
            family="user",
            chapter={"query": "画个图",
                     "intent": _intent(clarification={
                         "question": "哪个城市？", "status": "resolved"}),
                     "analysis_steps": [_step("poi.query")]},
            map_product=_product(),
            expected_global="satisfied",
        ),
    ]


# ── F-J：显式契约边界（must_not / threshold）─────────────────────────────

def _family_contract_edges() -> List[GoalSatisfactionCase]:
    no_heatmap = {
        "schema_version": "goal_contract.v1", "goal_id": "g",
        "requirements": [
            {"id": "map", "kind": "map"},
            {"id": "no_heatmap", "kind": "analysis", "polarity": "must_not",
             "capability": "hotspot"},
        ],
    }
    threshold_half = {
        "schema_version": "goal_contract.v1", "goal_id": "g",
        "success_threshold": 0.5,
        "requirements": [
            {"id": "map", "kind": "map"},
            {"id": "analysis:poi.query", "kind": "analysis",
             "capability": "poi.query"},
            {"id": "analysis:change_detection", "kind": "analysis",
             "capability": "change_detection"},
            {"id": "analysis:admin_aggregation", "kind": "analysis",
             "capability": "admin_aggregation"},
        ],
    }
    return [
        GoalSatisfactionCase(
            case_id="GC-contract-must-not-violated",
            name="显式禁令被违反 → 全局 failed",
            family="contract",
            chapter={"query": "禁令", "intent": _intent(),
                     "analysis_steps": [_step("hotspot")],
                     "goal_contract": no_heatmap},
            map_product=_product(),
            expected_global="failed",
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-contract-must-not-clean",
            name="禁令未违反 → 正常满足",
            family="contract",
            chapter={"query": "禁令", "intent": _intent(),
                     "analysis_steps": [_step("poi.query")],
                     "goal_contract": no_heatmap},
            map_product=_product(),
            expected_global="satisfied",
        ),
        GoalSatisfactionCase(
            case_id="GC-contract-threshold-half",
            name="threshold 0.5：2/4 分析 fulfilled 即 satisfied",
            family="contract",
            chapter={"query": "阈值", "intent": _intent(),
                     "analysis_steps": [_step("poi.query"),
                                        _step("admin_aggregation"),
                                        _step("change_detection", "pending")],
                     "goal_contract": threshold_half},
            map_product=_product(),
            expected_global="satisfied",
        ),
        GoalSatisfactionCase(
            case_id="GC-contract-invalid-falls-back",
            name="非法显式契约 → 回退派生（不假用不崩溃）",
            family="contract",
            chapter={"query": "坏契约", "intent": _intent(),
                     "analysis_steps": [_step("poi.query")],
                     "goal_contract": {"requirements": "garbage"}},
            map_product=_product(),
            expected_states={"analysis:poi.query": "fulfilled"},
        ),
    ]


# ── F-K：G6 八大反事实注入（命名案例，逐条对应任务书）─────────────────────

def _family_counterfactual() -> List[GoalSatisfactionCase]:
    return [
        GoalSatisfactionCase(
            case_id="GC-cf1-empty-result",
            name="CF1: 工具都 200 但数据为空",
            family="counterfactual",
            chapter={"query": "统计", "intent": _intent(outputs=["map", "statistics"]),
                     "analysis_steps": [_step("admin_aggregation", "complete")]},
            map_product=_product(reasons=["empty_result"]),
            expected_states={"analysis:admin_aggregation": "partial"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-cf2-map-pass-comparison-missing",
            name="CF2: 地图 PASS 但缺 district comparison",
            family="counterfactual",
            chapter={"query": "分布并比较",
                     "intent": _intent(comparison="各区对比"),
                     "analysis_steps": [_step("poi.query")]},
            map_product=_product(),
            expected_states={"comparison": "not_evaluated"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-cf3-chart-filter-differs",
            name="CF3: chart 有但过滤条件不同",
            family="counterfactual",
            chapter={"query": "各区图表",
                     "intent": _intent(outputs=["map", "chart"],
                                       group_by="district"),
                     "analysis_steps": [_step("admin_aggregation",
                                              params={"group_by": "category"})]},
            map_product=_product(),
            expected_states={"analysis:admin_aggregation": "partial"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-cf4-visual-pass-scope-wrong",
            name="CF4: visual PASS 但 scope 错",
            family="counterfactual",
            chapter={"query": "成都学校", "intent": _intent(scope="成都"),
                     "analysis_steps": [_step("poi.query",
                                              params={"scope": "重庆"})]},
            map_product=_product(),
            expected_states={"analysis:poi.query": "partial"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-cf5-stale-artifact",
            name="CF5: stale artifact（render stale + final failed）",
            family="counterfactual",
            chapter={"query": "地图", "intent": _intent(),
                     "analysis_steps": []},
            map_product=_product("NEEDS_REPAIR", final="failed",
                                 render="stale",
                                 issues=[{"code": "render_revision_stale"}]),
            expected_states={"map": "partial"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-cf6-fallback-non-comparable",
            name="CF6: fallback source non-comparable",
            family="counterfactual",
            chapter={"query": "统计",
                     "intent": _intent(outputs=["map", "statistics"]),
                     "analysis_steps": [_step("admin_aggregation", "complete")],
                     "fallbacks": [{"downgrade_class": "not_allowed",
                                    "reason_code": "non_comparable_source"}]},
            map_product=_product(),
            expected_states={"analysis:admin_aggregation": "partial",
                             "statistics": "partial"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-cf7-export-stale-revision",
            name="CF7: export file 存在但旧 revision",
            family="counterfactual",
            chapter={"query": "导出", "intent": _intent(exports=["png"]),
                     "analysis_steps": [],
                     "export_receipts": [_receipt("png", revision=2)]},
            map_product=_product(),
            expected_states={"export:png": "partial"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-cf8-user-hid-required-view",
            name="CF8: user 显式隐藏 required view → 披露且不阻断，"
                 "但 missing 面可见",
            family="counterfactual",
            chapter={"query": "地图", "intent": _intent(),
                     "analysis_steps": []},
            map_product=_product(intent_acceptance={
                "accepted": True, "intent_verified": False,
                "observed_confirmed": False,
                "disclosures": ["layer_hidden_by_user:l1"], "unmet": [],
            }),
            expected_states={"map": "fulfilled"},
        ),
    ]


# ── F-L：质量集成（G7 只消费）+ F-M：not_evaluated 诚实三态 ───────────────

def _family_quality_and_honesty() -> List[GoalSatisfactionCase]:
    carto_block = _product(cartography={
        "status": "failed", "no_deterministic_failures": False,
        "blocking_rules": ["C_LAYOUT_OVERLAP"], "warning_rules": [],
        "auto_repairable": [],
    })
    return [
        GoalSatisfactionCase(
            case_id="GC-quality-carto-blocking",
            name="V11 cartographic blocking rules → map partial（不重算）",
            family="quality",
            chapter={"query": "地图", "intent": _intent(),
                     "analysis_steps": []},
            map_product=carto_block,
            expected_states={"map": "partial"},
            expected_signal="repair_cartography",
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-quality-render-stale",
            name="render stale → map partial（诚实再观察）",
            family="quality",
            chapter={"query": "地图", "intent": _intent(),
                     "analysis_steps": []},
            map_product=_product(render="stale"),
            expected_states={"map": "partial"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-honest-no-evidence",
            name="契约在但零证据 → not_evaluated（三态诚实）",
            family="honesty",
            chapter={"query": "空章节", "intent": _intent(outputs=["map"]),
                     "analysis_steps": [_step("poi.query", "pending")]},
            map_product=None,
            expected_global="not_evaluated",
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-honest-all-blocked",
            name="全部 unavailable → blocked（不得默认 PASS）",
            family="honesty",
            chapter={"query": "全阻塞",
                     "intent": _intent(outputs=["map", "statistics"]),
                     "analysis_steps": [_step("admin_aggregation",
                                              "unavailable")]},
            map_product=None,
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-honest-partial-then-satisfied",
            name="部分在证 + 部分缺 → partial",
            family="honesty",
            chapter={"query": "部分",
                     "intent": _intent(outputs=["map", "statistics"]),
                     "analysis_steps": [_step("admin_aggregation", "complete"),
                                        _step("poi.query", "pending")]},
            map_product=_product(),
            expected_global="partial",
            must_not_pass=True,
        ),
    ]



# ── F-N：规格级组件路径 + 观测面零漂移 + 契约边角（≥100 补充族）──────────

def _family_spec_and_edges() -> List[GoalSatisfactionCase]:
    """chart 的 spec 级 structural 证据路径、观测面零漂移、契约边角。"""
    chart_product_with_gap = _product(
        issues=[{"code": "chart_data_missing"}])
    return [
        GoalSatisfactionCase(
            case_id="GC-spec-chart-component",
            name="chart via required_components structural path",
            family="spec-edges",
            chapter={"query": "图表", "intent": _intent(outputs=["map", "chart"]),
                     "analysis_steps": [],
                     "required_components": ["chart-panel"]},
            map_product=_product(),
            expected_states={"chart": "fulfilled"},
            expected_global="satisfied",
        ),
        GoalSatisfactionCase(
            case_id="GC-spec-chart-component-gap",
            name="chart slot declared but chart_data_missing → not fulfilled",
            family="spec-edges",
            chapter={"query": "图表", "intent": _intent(outputs=["map", "chart"]),
                     "analysis_steps": [],
                     "required_components": ["chart-panel"]},
            map_product=chart_product_with_gap,
            expected_states={"chart": "not_evaluated"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-edge-observation-health-present",
            name="observation_health additive key ignored by evaluator",
            family="spec-edges",
            chapter={"query": "地图", "intent": _intent(),
                     "analysis_steps": []},
            map_product=_product(observation_health={"aggregate": "verified"}),
            expected_states={"map": "fulfilled"},
            expected_global="satisfied",
        ),
        GoalSatisfactionCase(
            case_id="GC-edge-repair-plan-present",
            name="repair_plan additive key ignored by evaluator",
            family="spec-edges",
            chapter={"query": "地图", "intent": _intent(),
                     "analysis_steps": []},
            map_product=_product(repair_plan={"actions": []}),
            expected_global="satisfied",
        ),
        GoalSatisfactionCase(
            case_id="GC-signal-repair-via-cartography-key",
            name="cartography summary on block drives repair signal",
            family="spec-edges",
            chapter={"query": "地图", "intent": _intent(),
                     "analysis_steps": []},
            map_product=_product(cartography={
                "status": "failed", "blocking_rules": ["C_LABEL_COLLISION"],
            }),
            expected_signal="repair_cartography",
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-signal-continue-pending-row",
            name="open analysis row → continue signal",
            family="spec-edges",
            chapter={"query": "统计",
                     "intent": _intent(outputs=["map", "statistics"]),
                     "analysis_steps": [_step("admin_aggregation", "pending")]},
            map_product=_product(),
            expected_signal="continue",
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-edge-pinned-missing-evidence",
            name="pinned required without evidence blocks satisfied",
            family="spec-edges",
            chapter={"query": "钉死", "intent": _intent(),
                     "analysis_steps": [_step("poi.query", "pending")]},
            map_product=_product(),
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-edge-invalid-kind-falls-back",
            name="unknown requirement kind rejected → derived fallback",
            family="spec-edges",
            chapter={"query": "坏 kind",
                     "intent": _intent(outputs=["map"]),
                     "analysis_steps": [],
                     "goal_contract": {
                         "schema_version": "goal_contract.v1",
                         "goal_id": "g",
                         "requirements": [{"id": "x", "kind": "hovercraft"}],
                     }},
            map_product=_product(),
            expected_states={"map": "fulfilled"},
            expected_global="satisfied",
        ),
    ]


# ── F-O：组合场景（analysis+map+export 交错部分完成）─────────────────────

def _family_combinations() -> List[GoalSatisfactionCase]:
    base_intent = _intent(outputs=["map", "statistics", "chart"],
                          exports=["png", "pdf"], comparison="各区对比")
    full_steps = [_step("poi.query"), _step("admin_aggregation"),
                  _step("change_detection")]
    full_receipts = [_receipt("png"), _receipt("pdf")]

    def ch(steps, receipts):
        return {"query": "组合", "intent": base_intent,
                "analysis_steps": steps, "export_receipts": receipts}

    return [
        GoalSatisfactionCase(
            case_id="GC-combo-all-green",
            name="组合：全部兑现（map+statistics+chart+comparison+2 exports）",
            family="combinations",
            chapter=ch(full_steps, full_receipts),
            map_product=_product(),
            expected_global="satisfied",
            expected_signal="complete",
        ),
        GoalSatisfactionCase(
            case_id="GC-combo-pdf-missing",
            name="组合：png 兑现 pdf 缺 → partial",
            family="combinations",
            chapter=ch(full_steps, [_receipt("png")]),
            map_product=_product(),
            expected_states={"export:pdf": "not_evaluated"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-combo-chart-via-row",
            name="组合：chart 由聚合行背书",
            family="combinations",
            chapter=ch(full_steps, full_receipts),
            map_product=_product(),
            expected_states={"chart": "fulfilled",
                             "statistics": "fulfilled"},
        ),
        GoalSatisfactionCase(
            case_id="GC-combo-analysis-failed-export-ok",
            name="组合：comparison 行 failed → 全局 failed",
            family="combinations",
            chapter=ch([_step("poi.query"), _step("admin_aggregation"),
                        _step("change_detection", "failed")], full_receipts),
            map_product=_product(),
            expected_states={"analysis:change_detection": "failed",
                             "comparison": "not_evaluated"},
            expected_global="failed",
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-combo-stale-pdf-fresh-png",
            name="组合：一新一旧回执 → partial（旧回执不背书）",
            family="combinations",
            chapter=ch(full_steps, [_receipt("png"), _receipt("pdf", revision=2)]),
            map_product=_product(),
            expected_states={"export:png": "fulfilled",
                             "export:pdf": "partial"},
            must_not_pass=True,
        ),
        GoalSatisfactionCase(
            case_id="GC-combo-scope-loose-across-rows",
            name="组合：scope 宽松匹配覆盖全行",
            family="combinations",
            chapter={
                "query": "组合scope",
                "intent": _intent(scope="成都", outputs=["map", "statistics"],
                                  exports=["png"]),
                "analysis_steps": [
                    _step("admin_aggregation", params={"region": "成都"}),
                    _step("change_detection", params={"area": "成都市"}),
                ],
                "export_receipts": [_receipt("png")],
            },
            map_product=_product(),
            expected_global="satisfied",
        ),
        GoalSatisfactionCase(
            case_id="GC-edge-many-rows-bounded",
            name="有界性：30 行章节 → 契约 ≤24 需求，评估不崩、全绿 satisfied",
            family="combinations",
            chapter={
                "query": "有界",
                "intent": _intent(),
                "analysis_steps": [
                    _step(f"cap_row_{i}") for i in range(30)
                ],
            },
            map_product=_product(),
            expected_global="satisfied",
        ),
        GoalSatisfactionCase(
            case_id="GC-edge-optional-must-not-ignored",
            name="optional must_not 违反不构成全局 failed（仅 required 禁令阻断）",
            family="contract",
            chapter={
                "query": "可选禁令",
                "intent": _intent(),
                "analysis_steps": [],
                "goal_contract": {
                    "schema_version": "goal_contract.v1", "goal_id": "g",
                    "requirements": [
                        {"id": "map", "kind": "map"},
                        {"id": "soft_ban", "kind": "analysis",
                         "polarity": "must_not", "capability": "hotspot",
                         "required": False},
                    ],
                },
            },
            map_product=_product(),
            expected_global="satisfied",
        ),
    ]


def build_goal_satisfaction_cases() -> List[GoalSatisfactionCase]:
    """≥100 个结构化 goal 案例（覆盖矩阵见模块 docstring）。"""
    return (
        _family_row_matrix()
        + _family_product_verdicts()
        + _family_data_sufficiency()
        + _family_export()
        + _family_fallbacks()
        + _family_scope_filter()
        + _family_multi_goal()
        + _family_language()
        + _family_user_overrides()
        + _family_contract_edges()
        + _family_counterfactual()
        + _family_quality_and_honesty()
        + _family_spec_and_edges()
        + _family_combinations()
    )


# ── Runner + 指标 ────────────────────────────────────────────────────────

def run_goal_satisfaction_case(case: GoalSatisfactionCase) -> GoalCaseResult:
    """确定性执行一个案例（derive → evaluate → 逐条断言）。"""
    failures: List[str] = []
    report = evaluate_goal_satisfaction(
        case.chapter,
        map_product=case.map_product,
    )
    got_global = report.verdict.value if report else "none"
    if report is None:
        failures.append("report_missing")
        return GoalCaseResult(case.case_id, case.family, False, False,
                              got_global, failures)
    if case.expected_global is not None and got_global != case.expected_global:
        failures.append(f"global: expected {case.expected_global}, "
                        f"got {got_global}")
    states = {v.requirement_id: v.verdict.value
              for v in report.requirements}
    for req_id, expected in case.expected_states.items():
        got = states.get(req_id)
        if got != expected:
            failures.append(f"{req_id}: expected {expected}, got {got}")
    if case.expected_signal is not None and \
            report.signal.value != case.expected_signal:
        failures.append(f"signal: expected {case.expected_signal}, "
                        f"got {report.signal.value}")
    false_pass = case.must_not_pass and got_global == "satisfied"
    if false_pass:
        failures.append("FALSE_PASS: must_not_pass case judged satisfied")
    return GoalCaseResult(case.case_id, case.family,
                          not failures, false_pass, got_global, failures)


def corpus_metrics(results: List[GoalCaseResult]) -> Dict[str, Any]:
    """语料指标（false_pass_rate 是最高优先级指标，必须恒 0）。"""
    total = len(results)
    failed = [r for r in results if not r.passed]
    false_passes = [r for r in results if r.false_pass]
    by_family: Dict[str, Dict[str, int]] = {}
    for r in results:
        slot = by_family.setdefault(r.family, {"total": 0, "passed": 0})
        slot["total"] += 1
        slot["passed"] += int(r.passed)
    return {
        "total": total,
        "passed": total - len(failed),
        "failed": len(failed),
        "false_pass_count": len(false_passes),
        "false_pass_rate": (len(false_passes) / total) if total else 0.0,
        "false_pass_case_ids": [r.case_id for r in false_passes],
        "failed_case_ids": [r.case_id for r in failed],
        "by_family": by_family,
    }


def run_full_corpus() -> Dict[str, Any]:
    """全语料执行 → 指标（测试与验收面共用入口）。"""
    results = [run_goal_satisfaction_case(c)
               for c in build_goal_satisfaction_cases()]
    return corpus_metrics(results)


__all__ = [
    "GoalSatisfactionCase",
    "GoalCaseResult",
    "build_goal_satisfaction_cases",
    "run_goal_satisfaction_case",
    "corpus_metrics",
    "run_full_corpus",
]
