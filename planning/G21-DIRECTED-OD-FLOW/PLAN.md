# G21-DIRECTED-OD-FLOW — 执行计划

## Goal
在既有 OD 流图上交付可靠身份、有向/无向合计/净流、可辨方向、节点焦点、权威保存、UI/Pi 同源修改和真实 PNG/SVG/PDF 闭环，不接管 G09/G12–G20 或全局 UI 重设计。

## Current Phase
Phase 1 — 有界产品、身份、聚合与 refs；MapSpec 原子 apply 同步接线。

## Next Step
完成 OD 纯领域核心和 ApplyFlowProductIntent 的公开契约测试，随后接通 preview/apply/query service 与现有 `od_flow_edges`。

### Phase 0 — 基线、排重与真实调用链（WP01）
**Status:** complete
- 核对最新 master、全部开放 PR、相关已合并 PR 与 review/comments/issues。
- 阅读项目/前端规范、实际 Next.js 本地文档与相关 ADR。
- 复跑 OD backend、converter、frontend paint/export 基础测试。
- 用真实输入记录现有反例，完成 DEDUP_MATRIX 与 INTERFACES。

### Phase 1 — 有界产品、身份、聚合与 refs（WP02–WP04、WP12）
**Status:** in_progress
- 扩展现有工具/契约，不建平行工具、store 或 ID 系统。
- 实现显式字段/节点绑定、稳定身份、坐标冲突与有限数校验。
- 实现 directed/undirected-total/net、守恒统计、范围证据、确定性过滤/top-N。
- 用现有 SessionStore/artifact refs 持久化有界节点/边/索引与指纹。
- 验证已知答案、行序/反向交换/缩放变形和 50k 有界输入。

### Phase 2 — 可见几何、编码与共享导出（WP05/WP06/WP11）
**Status:** pending
- 保留旧直线，增加确定性分离曲线、局部切线箭头、自环/短线/日期变更线规则。
- 贯通标准 weight、固定 domain、节点 sqrt 面积和口径明确图例。
- 薄接 Python/TS SVG/Canvas/出版链，验证 PNG/SVG/PDF、96/300 DPI、旋转与缺失披露。

### Phase 3 — 权威生命周期、焦点与局部 UI（WP07–WP09）
**Status:** pending
- 复用 MapSpec mutation/CAS/checkpoint/undo，UI/Pi 共用 plan/preview/apply/query。
- 复用 SelectionContext 做 transient 节点/边焦点，显式过滤才持久化。
- 在现有 Studio/图层插槽增加功能专属面板，不改全局主题/布局。
- 验证重复 apply、取消、代次、user-wins、保存/刷新/恢复与跨会话隔离。

### Phase 4 — OD planner/descriptor 与真实 Pi 旅程（WP10）
**Status:** pending
- 仅修 OD 局部 recipe/绑定，真实 OD 表直接绑定 od_flow_mapping，无 od_matrix/provider 前置。
- 沿用注册工具和 Pi dispatcher，补有界情境与 inspect/preview/apply/query。
- 固定基线 Pi pin；使用本地 LLM stub 走生产 dispatch→MapSpec→渲染回执。

### Phase 5 — 全场景验收与性能证据（WP12）
**Status:** pending
- 按 TEST_MATRIX 完成单元、API、浏览器、Pi stub、三格式内容与性能验收。
- 对既有失败在同 SHA 干净基线最小复现，不降低门禁。
- 更新工具目录、兼容说明、无碰撞 ADR 与用户示例。

### Phase 6 — 独立 review→修复→复审→PR
**Status:** pending
- 释放实现槽位，由未写核心实现的 reviewer 审查最新 base 与完整 diff。
- 修复本任务阻塞发现并补回归，交回独立 reviewer 复审。
- fetch 最新 master，处理兼容/冲突；运行受影响回归和 git diff --check。
- 分阶段 commit，push 自有分支并创建 PR；不自动 merge、不等待线上 CI。

## Errors Encountered
| Error | Attempt | Resolution |
|---|---:|---|
| Vitest `--minWorkers` 不受 4.1.11 支持，测试未启动 | 1 | 读取实际 `--help`，改用受支持的单 worker/禁用文件并行参数 |
| 独立 worktree 无 `frontend/node_modules`，配置导入失败 | 2 | 在共享重型锁内执行标准 `pnpm install --frozen-lockfile --offline`，不绕过 Vite 配置 |
| MapSpec 大段 Edit 的精确 old_string 未命中 | 1 | 重读当前文件，改为小范围、可审计的 additive 编辑 |

## Decisions Made
- 基线：`816705218a5aa6202bf15d745cb31cddb3437f44`（启动时最新 origin/master）。
- 主工作区已有 `vendor/pi` 修改与未跟踪文件，保持只读且不触碰。
- 所有实现位于独立 worktree；共享文件只做 additive 局部接线。
