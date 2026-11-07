# G04-CARTOGRAPHY-COMPOSER LOOP LEDGER

| 轮 | 阶段 | 改动 | 验证 | 结果 | 下一步 |
|---|---|---|---|---|---|
| 1 | M0 | 审计 master 585812ee4 + PR #2011(G02 execution_compiler,不冲突) + issues #1999-#2007 开放未认领 + worktree 创建 | git/gh 查询 | 通过 | 三事实面 baseline |
| 2 | M1 | W1 四断链 characterization(后端 9 红+前端 2 红文件) | pytest/vitest | 通过(红=缺陷复现) | W1 实现 |
| 3 | M3 | W1 修复(#1999 求解接线/#2000 锚点门/#2002 族锁/#2003 bearing/A3 profile) | cartography 全量 2715→2726 绿;前端 923 绿 | 通过 | W2 |
| 4 | M4 | W2 selection_context 单一投影器+双侧接线+168 cardinality 归一+闭包门 | 11+5 新测试;cartography 2726/gis_harness 3328 绿 | 通过 | W3 |
| 5 | M5 | W3 图例单位贯通(真实缺陷)+parity 矩阵+回执链 | parity 28/golden 646/前端 739 绿 | 通过 | W4 |
| 6 | M6 | W4 增量语义(改色不重跑)+7 黄金案例 | 3+7 新测试绿 | 通过 | 全量回归 |
| 7 | M7 | 全量×2 + 质量门 + 依赖隔离修复(dompurify 错配→自装 node_modules) | cartography 3333×2;前端 534 文件/5003 测试;ruff/eslint/mypy(基线) 绿;a3 断言更新后 harness 3828 绿 | 通过 | 独立 review |
| 8 | M7d | 子代理 B 冷读 review 派发 | (进行中) | — | review 结论→修复→PR |
| 9 | M7d | 子代理 B 冷读 review:APPROVE-WITH-FIXES(16 声明属实,0×P0,2×P1,5×P2,5×P3) | REVIEW.md | 通过(评审) | 清偿 |
| 10 | M7d | 清偿 commit f4996822e(P1×2+P2×4+P3×5 修,2 项记账) | cartography 3335 绿;前端 17+typecheck+eslint 绿;ruff 零告警 | 通过 | 复审→PR |
