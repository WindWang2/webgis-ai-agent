# PROGRESS — 实现记录（collab/spatial-review-approval-v1）

## 提交

1. `929f9686` feat(review): backend（契约/策略/store/anchors/merge/service/export/API/bus kind/intent codec/69 测试/双门禁刷新）
2. 本提交：前端（protocol review kind/adopt/store/api/drawer + vitest）+ 文档（ADR-0201/CHANGELOG/UBIQUITOUS_LANGUAGE/CONTEXT）+ Phase0 文档

## 验证（本地，Windows/anaconda python，无 Redis）

| 套件 | 命令 | 结果 |
| --- | --- | --- |
| review 后端 | `pytest tests/review/ --no-cov` | 69 passed |
| cartography+collab 回归 | `pytest tests/cartography/test_mapspec_user_presentation_api.py tests/cartography/test_mapspec_remaining_chrome.py tests/test_collab_v6.py --no-cov` | 40 passed（合计与 review 109 passed） |
| 漂移门禁 | `pytest tests/test_api_docs_drift.py tests/quality/test_api_compatibility.py --no-cov` | 15 passed |
| ruff | `ruff check <touched>` | All checks passed |
| 前端 review | `vitest run lib/review components/workbench/review-drawer.test.tsx` | 10 passed |
| 前端回归 | `vitest run lib/collab lib/workbench components/workbench` | 81 passed |
| typecheck | `tsc --noEmit` | 0 error |
| eslint | touched files | clean |

## 已知边界（记录到 ADR-0201 §3）

- 合并序列非单事务：交错有存证 + 保护，中途失败可能留下部分落地（rebase 收口）。
- 高风险不加 claim 感知加严（v1 同 high 路径）。
- artifact 锚活性未接线注册表 → unverified。
- 匿名单人低风险自批 = 可用性让步（有审计存证）。
- 合并 dedup 键含 base：同 base 完全重放命中幂等回执（checkpoint/rollback 不带幂等键，回滚后 CAS 漂移自然逼 rebase）。
