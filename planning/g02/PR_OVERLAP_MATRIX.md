# G02 PR_OVERLAP_MATRIX — 与 open PR / master 的文件+语义交叠（基线 b1b2ec23）

## Open PR（2026-10-21 gh 实测）

| PR | head | 状态 | 交叠结论 |
|---|---|---|---|
| #1758 | zcode/swarm-review-fixes-v1 | OPEN（429 文件） | 见下表逐文件 |
| #1698 | dependabot/pip/sqlalchemy | OPEN | 无交叠 |
| #1701 | dependabot/pip/pandas | OPEN | 无交叠 |

## 本方向计划触碰文件 × #1758

| 文件 | #1758 | 本方向改动 | 冲突风险 |
|---|---|---|---|
| app/lib/gis/scientific_preconditions.py | 否 | deferred 标志 + evidence_state | 无 |
| app/lib/gis/algorithm_resolver.py | 否 | not_evaluated 通道 | 无 |
| app/lib/gis/method_use_contract.py (新) | 否 | 新文件 | 无 |
| app/lib/gis/science_final_gate.py (新) | 否 | 新文件 | 无 |
| app/tools/registry.py | 否 | except ScientificError 分支 | 无 |
| app/services/workflow_engine.py | 是（:691 +12 行，无关区域） | resolve_step_tool :140-215 科学 fail-closed + evidence | hunk 不相交；PR 披露 |
| app/services/gis_harness/planner.py | 是（best_effort 替换 :49/:369/:832/:866/:894/:1082） | AlgorithmSelectionRecord :561 + 转录 :739 | hunk 不相交；PR 披露 |
| app/services/gis_harness/workflow_compiler.py | 否 | 无改动（消费者已在 :448 等） | 无 |
| app/services/gis_harness/product_compiler.py | 否 | science_findings 投影 | 无 |
| scripts/inspect_method_contract.py (新) | 否 | 新文件 | 无 |
| tests/unit/gis/test_g02_*.py (新) | 否 | 新文件 | 无 |

## 语义交叠（防重复合工）

- #1758 已认领：#1736（geo_analysis 四缺陷）、#1737（longitude）、#1705、#1735、#1716、
  #1720 部分等 —— **本方向不修这些单点 bug**，不回退其变更。
- #1755（已合并 H02）落地 export conformance/manifest —— 本方向 final gate 不重造
  export manifest，只做方法科学 evidence 对账（语义正交）。
- #1756（已合并 H01）qualification→completion —— 本方向复用其四态词表习惯，不改其文件。

## 同步纪律

- 每个 wave 提交前 `git fetch` 重试（网络恢复时）并 `git log origin/master -3` 比对；
  提 PR 前必须再次 fetch + 比较（当前网络下用 `gh api .../commits/master` 交叉核对）。
- 若 #1758 先合并且触及相邻区域：rebase 后逐 hunk 复核，不覆盖其语义。

## 执行期实况更新（2026-10-21，PR 创建前）

- fetch 恢复后实测：**PR #1758 已合并**，master 前移 b1b2ec23 → ee8c14f8。
- 本分支 `git rebase origin/master` **零冲突**（交叠三文件 planner.py /
  workflow_engine.py / pyproject.toml 的 hunk 不相交，与预判一致）。
- rebase 后复验（合并 #1758 的新基线）：核心套件 192P ×2 双跑一致；
  gis_harness 全量 2571P+3S；冻结语料 2272P；benchmark harness 4P；
  ruff 全绿；CLI --check exit 0。#1758 的 geo_analysis/best_effort 语义
  与本分支无回归交互。
