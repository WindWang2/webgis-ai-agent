# F13 独立 Review 报告与清偿（ADR-0214）

- Reviewer：Subagent C（独立深审，非实现者自评）；范围 `origin/master...HEAD`（基线 9e1ad229）。
- 结论：**PASS-WITH-FIXES** → P0/P1 全部修复，P2 全部清偿，P3 修复廉价项、其余记录在案。

## Review 发现与修复对照

| 级别 | 发现 | 修复 |
|---|---|---|
| P0-1 | `render-apply-ack.test.ts` 组件 pending 断言 `'applied'` 与实现（`partial`）及同文件层 pending 语义矛盾（两个测试锁相反语义；前端测试从未运行） | 统一语义：任何非终态条目（pending/skipped/discarded）→ 事务 `partial`；测试期望修正并注明 |
| P0-2 | `visibility-pin.test.ts` setPriority 测试针对 in-flight 条目（concurrency=4 单请求同步进 inflight，setPriority 只扫队列 → 恒 false） | 改用既有 gate 模式（concurrency=1 + 第一发门住，目标条目留队列），与 scheduler.test.ts 既有姿势一致 |
| P1-1 | pin 接线挂在**条件渲染**的 LayersTab（仅 layers tab 且非样式编辑时挂载）：D4 保证只在盯着图层 tab 时成立；切走/卸载后 pin 残留，跨会话 pinned 单调增长可钉死 256MB 预算 | 重构为**模块级 HUD store 订阅**（map-panel 静态 import 激活，与 tab 显隐无关）；新增 `unpinSession` 会话 sweep（会话切换全量 unpin 旧会话残留）；签名去重不变 |
| P2-1 | 投影对 `source=""`（background 设计内哨兵）走 `source_map.get("") → None → +2000 先验且误报 features_estimated` | sid 为空直接跳过计数；新增回归测试锁定 |
| P2-2 | ADR/design 承诺 reconcileError 非空 → 事务级降级，实现只截断文本（`RC_APPLY_ERROR` 从生产 builder 不可达；测试反向锁定错误语义） | 实现**事务级降级**（reconcileError 非空 → status 绝不 `applied`）；测试改锁正确语义；文档措辞精确化（advisory 文本披露） |
| P2-3 | `isStaleApplyAck` 无生产消费方，ADR 称"前端+后端双向丢弃"不实 | ADR/design 措辞收敛：**服务端 stamped revision 门是 stale 权威**；`isStaleApplyAck` 为前端镜像工具（本地消费方预判用） |
| P2-4 | observation 校验早退路径绕过 ACK findings —— 确认与 docstring 一致且有意（stale 双防线）；`not_applicable` 会话的 ACK 披露不可见属既有框架行为 | 无需修改（记录在案） |
| P3-1 | render-observation 手写镜像接口（漂移温床；"类型循环"理由不成立） | 改 type-only import（`RenderApplyAck`/`RenderPerfBlock`），形状单源 |
| P3-2 | 去重键剔除整个 apply_ack 过宽：pending 出现/消失改变 ACK 而键不变 → 新 ACK 可能永不上报 | ACK 折叠为廉价签名（status+failed 数+pending 数）参与去重键 |
| P3-5 | builder 截断记账次序：cap 检查先于弃权检查，弃权层被计入 discarded | 弃权检查前移（披露口径 = 因预算截断的待报告层数） |
| P3-7/10 | setRefPinned 返回值文档不准；aliasesOf 死 try/catch | 措辞修正 / 死代码移除 |
| P3-3/4/6/8/9 | mounted 基准恒真（warning 有界噪声）、notes 上限互蚀（防御边界）、validate errors 无界（64KB DTO 门间接约束）、同 ref 混合键瞬态双往返、_reset 不退订（无正确性影响） | 记录在案，不修（均有界/无害/防御性边界） |

## Review 正面确认（摘）

- 单一真相 ✓：ACK/投影/探针全为纯派生，零平行写入；探针订阅 session-cursor 单一代次源；layerAliases 导出无循环 import。
- stale 语义 ✓：stamped revision 锁内读取、fingerprint 门之前，无竞态窗；stale ACK 只披露不判定。
- user-wins ✓：builder 排除 pending 层（含别名族）+ 后端 RC_USER_PENDING 跳过双保险；pin 只动缓存不改显隐。
- governor fail-open ✓：双保险 try/except；classify 两处 subsystem 判定一致（cost 仅在无 pattern 命中时起作用）。
- 有界性 ✓：全部新载荷达标且截断有披露。
- 兼容性 ✓：旧客户端零影响；无 revision 键向后兼容（测试锁定）；design_system 加键安全。
- 变异验证 ✓：删 stale 分支 / user_pending 跳过 / revision 键分支均有测试爆红。

## 本地验证证据

- 后端：`tests/cartography/test_render_work_projection.py`（20）+ `test_render_apply_ack.py`（21）+ `tests/governor/test_render_projection.py`（9）= 50 新契约测试全绿；
- 邻域回归全绿：governor 全套 178、design_system 族 60、render observation/finalization 族 123+37、chat_api observation 子集 4、cartography 闭环 + mutation revision + component lifecycle 36；
- ruff 全部改动文件 clean；
- 前端：本机无 node_modules（无 pnpm/npm/corepack），vitest/tsc 无法本地执行——全部前端文件经 Node 26 type-stripping 解析检查（0 语法错误），3 个新测试文件经人工逐断言推演 + 独立 reviewer 复核（P0 两处即为该流程捕获），待 CI vitest 验证。
