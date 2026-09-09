# 04 — Test Matrix（本地验证，不依赖线上 CI/CD）

## 前端（pnpm；vitest 4 + jsdom；workers 默认）

| 层级 | 套件 | 结果 |
|---|---|---|
| 纯函数模型 | doc.test（14：迁移/归一化/环截断/深度/reparent 守卫/字节）+ undo.test（7）+ doc 数量上限 | 全绿 |
| Store 行为 | workbenchSlice.test（23：嵌套创建/move 守卫/子组提升/hydrate） | 全绿 |
| 投影 | workspace-projection.test（11：DFS/折叠传播/孤儿/Set 化后回归） | 全绿 |
| 持久化 | persistence.test（6：armed 门/superseded 回灌/会话切换/空基线/冲刷） | 全绿 |
| 协同 | collab.test（5：收敛/防回声/旧 rev 忽略/hello/权限与降级） | 全绿 |
| 锁门 | layer-lock.test（6：全锁 typed 冲突/部分披露/解锁 override/remove 门） | 全绿 |
| 恢复 | session-anchor.test（5：指针/恢复判定/清锚/冲刷） | 全绿 |
| 虚拟化 | workbench-virtual-10k（3：窗口恒定/滚动/折叠传播） | 全绿 |
| 编辑/徽标/a11y | workbench-editing-a11y（6：嵌套子组/键盘 undo/徽标/Escape） | 全绿 |
| 后端引擎 | test_mapspec_lifecycle_engine workbench×4（提交持久化/CAS 拒绝/7 类非法 doc/校验器） | 全绿 |
| **全量回归** | **277 文件 / 2677 例（含 coverage ratchet 75/70/75/60，实测 80.0/69.9/79.7/82.9）** | **全绿** |
| 静态 | eslint --max-warnings 0（全仓）；tsc 双 tsconfig（app+test） | 通过 |
| 构建 | next build（production） | 通过 |

## 后端（pytest）

| 层级 | 套件 | 结果 |
|---|---|---|
| 引擎意图 | lifecycle engine 全套 34 例（含 workbench 4 例：提交+落盘/CAS superseded/非法 doc 拒绝/校验器） | 全绿 |
| 路由契约 | mapspec user presentation/remaining chrome 11 例 | 全绿 |
| OpenAPI 快照 | 字节级兼容闸（API_SNAPSHOT_UPDATE=1 属主刷新后复验一致） | 全绿 |
| 契约漂移 | drift gate 5 例（报告再生成后字节一致） | 全绿 |
| 白名单 | test_map_action_whitelist | 全绿 |

## 性能口径

- 全部行为测试用 work-count/结构断言（窗口 DOM 量 <60 行恒定、投影计数），不依赖整机 wall-clock；
- 10k 投影 + 扁平化 + 虚拟窗口纯函数预算测试（`workbench-virtual-10k`）；
- thinning 确定性测试（同输入同视口同输出，2 万特征规模）。
