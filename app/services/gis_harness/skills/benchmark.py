"""Skill Benchmark —— 确定性 resolver 语料评测（ADR-0182；goal S24）。

语料：``library/benchmark_corpus_v1.yaml``（≥150 任务；覆盖中文/英文、
明确/模糊、简单/复杂、数据不足、unsupported、multi-skill 组合）。

评测（全部确定性，零 LLM、零网络）：

- ``top1`` / ``top3``：期望技能命中 ranked 首位 / 前三；
- ``wrong_skill``：期望 select 却选出无关技能；
- ``reject``：期望被拒的技能确实被拒（按 reason_code 前缀对账）；
- ``clarification``：期望澄清且 resolver 给出澄清；
- ``composition``：组合存在、成员完整、且触发 query 的 top1 = primary 成员。

红线：本评测只针对确定性 resolver；语料是审定资产，修改走 code review；
禁止伪造指标 —— 失败用例在 ``failures`` 中如实列出。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.services.gis_harness.skills.loader import SkillLibrary
from app.services.gis_harness.skills.situation import SelectionFacts

#: 语料默认路径。
CORPUS_PATH = Path(__file__).resolve().parent / "library" / "benchmark_corpus_v1.yaml"


def load_corpus(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """语料装载（审定资产；形态非法 fail loud）。"""
    p = path or CORPUS_PATH
    with p.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    entries = doc.get("cases") if isinstance(doc, dict) else doc
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"skill benchmark corpus: 空语料 {p}")
    seen = set()
    for e in entries:
        if not isinstance(e, dict) or "id" not in e or "q" not in e:
            raise ValueError(f"skill benchmark corpus: 非法条目 {e}")
        if e["id"] in seen:
            raise ValueError(f"skill benchmark corpus: duplicate id {e['id']}")
        seen.add(e["id"])
    return entries


def _facts_from_case(case: Dict[str, Any]) -> SelectionFacts:
    facts = dict(case.get("facts") or {})
    facts.setdefault("goal_text", str(case.get("q", "")))
    return SelectionFacts(**facts)


def evaluate_case(library: SkillLibrary, case: Dict[str, Any]) -> Dict[str, Any]:
    """单例评测 → {id, expect_type, pass, kind(命中/失败类别), detail}。"""
    expect = case.get("expect") or {}
    etype = str(expect.get("type", "select"))
    facts = _facts_from_case(case)
    result = library.resolver.resolve(facts)
    rejected_by_id = {r.skill_id: r for r in result.rejected}

    out: Dict[str, Any] = {"id": case["id"], "expect": etype, "passed": False,
                           "kind": "", "detail": ""}

    if etype == "select":
        top_expect = expect.get("top")
        in_top3 = list(expect.get("in_top3") or [])
        top_ids = [c.skill_id for c in result.ranked[:3]]
        if result.selected == top_expect:
            out.update(passed=True, kind="top1")
        elif top_expect and top_expect in top_ids:
            out.update(passed=True, kind="top3")
        elif in_top3 and set(top_ids) & set(in_top3):
            out.update(passed=True, kind="in_top3")
        elif result.ranked:
            out.update(kind="wrong_skill",
                       detail=f"got {top_ids} expect {top_expect}")
        else:
            out.update(kind="no_result", detail=f"expect {top_expect}")
    elif etype == "clarification":
        if result.clarification is not None:
            out.update(passed=True, kind="clarified")
        else:
            out.update(kind="no_clarification",
                       detail=f"selected {result.selected}")
    elif etype == "reject":
        skill = str(expect.get("skill", ""))
        reason_prefix = str(expect.get("reason", ""))
        rej = rejected_by_id.get(skill)
        if rej is not None and (not reason_prefix
                                or any(c.startswith(reason_prefix)
                                       for c in rej.reason_codes)):
            fb_ok = True
            if expect.get("fallback_to"):
                fb_ok = rej.fallback_skill_id == expect["fallback_to"]
            out.update(passed=fb_ok,
                       kind="rejected" if fb_ok else "fallback_mismatch",
                       detail="" if fb_ok else
                       f"fallback {rej.fallback_skill_id} != {expect['fallback_to']}")
        elif rej is None:
            out.update(kind="not_rejected",
                       detail=f"{skill} ranked={skill in [c.skill_id for c in result.ranked]}")
        else:
            out.update(kind="wrong_reason",
                       detail=f"{rej.reason_codes} !~ {reason_prefix}")
    elif etype == "composition":
        comp_id = str(expect.get("composition", ""))
        comp = library.composition(comp_id)
        if comp is None:
            out.update(kind="composition_missing", detail=comp_id)
            return out
        primary = next(
            (m.skill_id for m in comp.members if m.role == "primary"), "")
        missing_members = [m.skill_id for m in comp.members
                           if library.get(m.skill_id) is None]
        if missing_members:
            out.update(kind="composition_broken", detail=str(missing_members))
        elif result.selected == primary:
            out.update(passed=True, kind="composition_resolved")
        else:
            out.update(kind="composition_primary_mismatch",
                       detail=f"top1 {result.selected} != primary {primary}")
    else:
        out.update(kind="unknown_expect", detail=etype)
    return out


def evaluate_corpus(
    library: SkillLibrary,
    corpus: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """全语料评测 → 汇总指标 + 逐类指标 + 失败清单（如实、不伪造）。"""
    cases = corpus if corpus is not None else load_corpus()
    results = [evaluate_case(library, c) for c in cases]

    select_cases = [r for r in results if r["expect"] == "select"]
    top1 = sum(1 for r in select_cases if r["kind"] == "top1")
    top3 = sum(1 for r in select_cases
               if r["kind"] in ("top1", "top3", "in_top3"))
    wrong = sum(1 for r in select_cases if r["kind"] == "wrong_skill")
    clar_cases = [r for r in results if r["expect"] == "clarification"]
    clar_hits = sum(1 for r in clar_cases if r["passed"])
    rej_cases = [r for r in results if r["expect"] == "reject"]
    rej_hits = sum(1 for r in rej_cases if r["passed"])
    comp_cases = [r for r in results if r["expect"] == "composition"]
    comp_hits = sum(1 for r in comp_cases if r["passed"])
    all_pass = sum(1 for r in results if r["passed"])

    by_category: Dict[str, Dict[str, Any]] = {}
    for case, r in zip(cases, results):
        cat = str(case.get("cat", "uncategorized"))
        bucket = by_category.setdefault(cat, {"n": 0, "passed": 0})
        bucket["n"] += 1
        bucket["passed"] += int(r["passed"])

    failures = [
        {"id": r["id"], "expect": r["expect"], "kind": r["kind"],
         "detail": r["detail"]}
        for r in results if not r["passed"]
    ]
    return {
        "total": len(results),
        "passed": all_pass,
        "select_cases": len(select_cases),
        "top1_hits": top1,
        "top3_hits": top3,
        "wrong_skill": wrong,
        "top1_rate": round(top1 / len(select_cases), 4) if select_cases else 0.0,
        "top3_rate": round(top3 / len(select_cases), 4) if select_cases else 0.0,
        "wrong_skill_rate": round(wrong / len(select_cases), 4) if select_cases else 0.0,
        "clarification_cases": len(clar_cases),
        "clarification_hits": clar_hits,
        "reject_cases": len(rej_cases),
        "reject_hits": rej_hits,
        "composition_cases": len(comp_cases),
        "composition_hits": comp_hits,
        "by_category": by_category,
        "failures": failures,
    }


__all__ = [
    "CORPUS_PATH",
    "load_corpus",
    "evaluate_case",
    "evaluate_corpus",
]
