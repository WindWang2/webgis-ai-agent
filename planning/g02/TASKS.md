# G02 TASKS

状态图例：☐ 待办 / ◐ 进行中 / ☑ 完成 / ✗ 放弃(原因)

## W0
- ☑ 环境侦察（git/gh/worktree/PR/issue 收集）
- ☑ 勘察代理（Explore）全链路 file:line 证据
- ☑ 主代理复核关键断言（resolver/planner/compiler/registry/engine 五处亲读）
- ☑ RECON.md / PR_OVERLAP_MATRIX.md / PLAN.md / TASKS.md / DECISIONS.md / TEST_MATRIX.md
- ☑ W0 commit

## W1 契约层
- ☐ scientific_preconditions：deferred 标志 + pass_evidence_state + 14 处标注
- ☐ algorithm_resolver：not_evaluated 通道（4 元组 + Resolution 字段）
- ☐ method_use_contract.py 新建（schema v1 投影）
- ☐ scripts/inspect_method_contract.py 新建
- ☐ test_g02_contract_semantics.py 红→绿
- ☐ ruff + 相关旧测试回归
- ☐ W1 commit

## W2 生产入口
- ☐ planner AlgorithmSelectionRecord 三字段 + 转录（两处 resolve 点核对）
- ☐ workflow_engine resolve_step_tool 科学 fail-closed + evidence 扩展
- ☐ science_final_gate.py 新建
- ☐ product_compiler science_findings 接线
- ☐ tools/registry ScientificError 分支
- ☐ 三段对账测试
- ☐ W2 commit

## W3 失败注入/TOCTOU/并发
- ☐ TOCTOU profile 变化测试
- ☐ 并发/幂等测试
- ☐ 畸形输入测试
- ☐ W3 commit（可与 W2 合并提交）

## W4 oracle 语料
- ☐ ≥8 组守门 oracle（含负例）
- ☐ performance 基线（237 算法 < 2s + 无 IO 断言）
- ☐ W4 commit

## W5 独立 review
- ☐ reviewer C 逐文件审 diff
- ☐ 修复全部 P0/P1（P2/P3 评估）
- ☐ 同 reviewer 复审通过
- ☐ REVIEW.md 记录

## W6 PR
- ☐ 终版 fetch + master 比对
- ☐ PR body（Why/Now、overlap 证据、DoD、测试、风险/回退）
- ☐ gh pr create + URL 记录 PR.md
