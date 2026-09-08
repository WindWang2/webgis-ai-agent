"""Determinism Certification（ADR-0104 Wave 12）——可复现性派生认证。

两条腿：
- **行为认证**（tests）：规划双跑逐位一致；代表性算法双跑结果一致；
  artifact 契约 round-trip 稳定 —— 「same input → same output」。
- **声明认证**（派生表）：AlgorithmRegistry 的 ``deterministic`` /
  ``random_seed_policy`` 声明自洽性。规则：
    * deterministic=True 且 policy ∈ {unseeded} → INCONSISTENT（自相矛盾：
      声明确定却用无种子随机）；
    * policy ∈ {deterministic, none} → deterministic 计算（无种子需求）；
    * fixed_seed / caller_seeded → 种子化随机（可复现，种子来源必须声明）。

不做「全系统 replay 语义等价」的伪断言——nondeterministic 的地方如实
列出并要求声明，不伪装 deterministic。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from app.lib.gis.algorithm_registry import get_algorithm_registry

#: policy → 认证分类
_POLICY_CLASS = {
    "deterministic": "deterministic",
    "none": "deterministic",
    "fixed_seed": "seeded-stochastic",
    "caller_seeded": "seeded-stochastic",
    "unseeded": "stochastic-unseeded",
}


def algorithm_determinism_rows() -> List[Dict[str, Any]]:
    reg = get_algorithm_registry()
    rows: List[Dict[str, Any]] = []
    for aid in sorted(reg.all_ids):
        algo = reg.get(aid)
        if algo is None:  # pragma: no cover
            continue
        policy = str(algo.random_seed_policy)
        dflag = bool(algo.deterministic)
        pclass = _POLICY_CLASS.get(policy, "unknown-policy")
        inconsistent = dflag and policy == "unseeded"
        rows.append({
            "id": aid,
            "deterministic": dflag,
            "random_seed_policy": policy,
            "class": "INCONSISTENT" if inconsistent else pclass,
            "scientific_status": str(algo.scientific_status),
        })
    return rows


def determinism_summary() -> Dict[str, int]:
    rows = algorithm_determinism_rows()
    summary: Dict[str, int] = {}
    for r in rows:
        summary[r["class"]] = summary.get(r["class"], 0) + 1
    summary["total"] = len(rows)
    return summary


def render_determinism_md() -> str:
    rows = algorithm_determinism_rows()
    summary = determinism_summary()
    lines: List[str] = []
    lines.append("# Determinism Certification（自动生成）")
    lines.append("")
    lines.append("> 由 `python scripts/gen_determinism_certification.py` 从")
    lines.append("> AlgorithmRegistry 派生，请勿手改。行为认证（规划/算法/")
    lines.append("> artifact 双跑一致）：tests/quality/")
    lines.append("> test_determinism_certification.py。")
    lines.append("")
    lines.append("## 分类汇总")
    lines.append("")
    lines.append("| class | count | 语义 |")
    lines.append("|---|---|---|")
    semantics = {
        "deterministic": "确定性计算（同输入同输出）",
        "seeded-stochastic": "种子化随机（可复现；种子来源已声明）",
        "stochastic-unseeded": "无种子随机（声明型 nondeterministic）",
        "INCONSISTENT": "自相矛盾声明（deterministic=True 且 unseeded）——禁止",
    }
    for cls in ("deterministic", "seeded-stochastic", "stochastic-unseeded", "INCONSISTENT"):
        lines.append(f"| {cls} | {summary.get(cls, 0)} | {semantics[cls]} |")
    lines.append(f"| total | {summary['total']} | |")
    lines.append("")
    lines.append("## INCONSISTENT / stochastic-unseeded 明细")
    lines.append("")
    flagged = [r for r in rows if r["class"] in ("INCONSISTENT", "stochastic-unseeded")]
    if not flagged:
        lines.append("（无）")
    else:
        for r in flagged:
            lines.append(f"- `{r['id']}`（{r['class']}，status={r['scientific_status']}）")
    lines.append("")
    fingerprint = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    lines.append(f"- 内容指纹：`{fingerprint[:16]}…`")
    lines.append("")
    return "\n".join(lines)
