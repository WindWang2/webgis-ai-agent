# ADR-0143: 项目工作区信息架构 — 项目管理员旅程与资产面板组织（Workspace UI Completion）

- 状态：Accepted（纯前端线；随 workspace-ui-v9 交付）
- 日期：2026-09-12
- 关联：ADR-0069（项目制图记忆治理）、ADR-0092（Map Product 版本工作区）、
  ADR-0097（显式分析图投影）、#1149（Data/Artifact/Workspace 后端 foundation）、
  #1236（V9 lifecycle foundation，在途）、#1235（Lakehouse UI，在途）

## 背景

`app/api/routes/project.py`（48 端点）暴露了完整的项目资产面：数据集
attach/detach、产物 registry + pin/clone/lineage、workspace snapshots
save/restore/clone、quality-audit/repair、data-usage + data-gc plan/execute。
前端（project-tab）此前只消费了其中一半——projects / workflows / runs /
compare / replay / resume / promote + carto-memory + map-product versions。
数据集只有只读挂载列表；产物、快照、质量、回收全部是「API-only 能力」，
项目管理员只能 curl。

## 决策

### D1 资产区 tab 容器（append-only）

`components/sidebar/project/` 新目录承载五个资产面板，由
`ProjectAssetsSection` 的受控 tab 条组织（数据集 | 产物 | 快照 | 质量 |
回收）。tab 状态提升到 project-tab，工作流/运行视图提供回跳按钮（P7
交叉导航的一半）；另一半由质量回执 / gc 候选 → 产物定位（
`focusArtifactId`）与「查看 Map Product 版本台账」滚动导航（
`onViewVersionLedger`）构成。既有 project-tab 视图机（project /
workflow / run / compare）与 CartoMemoryPanel、MapProductVersionsPanel
内部逻辑零改动；`components/sidebar/workflow/**`（E 线）只读引用。

### D2 数据访问层：`project-assets.ts` + 五族 hooks

`lib/api/project-assets.ts` 是 datasets/artifacts/snapshots/quality/gc 族
的 typed client，契约以 `frontend/docs/workspace-ui-recon.md` 的 48 端点
实测表为准（含裸 dict 端点的手写类型）。`lib/hooks/use-project-assets.ts`
沿用 `use-workflow-workspace` 的既定纪律：per-switch AbortController +
generation 计数（stale 响应不回写）、破坏性动作 hook 级 busy 锁、卸载统一
abort、无轮询（project.py 无 job 句柄，全部同步调用）。

### D3 后端契约缺口的诚实降级（详见 recon §2.10 协调点表）

- datasets 无 rename/preview/schema/详情端点 → schema_profile 仅在 attach
  响应捕获（会话内）；预览经 data-fabric catalog preview **前端聚合**
  （表格 + SVG 足迹图）；重命名不做。
- artifacts 无下载/revisions 端点 → 下载降级为引用 + sha256 复制；版本
  维度对比由 Map Product 版本台账承接；pin 态为会话易失镜像。
- snapshots 无 diff 端点 →  restore 前核查（verify 报告）+ 两快照报告的
  **前端聚合 diff**；clone 使用既有端点（目标会话输入）。
- quality repair 无 dry-run 参数 → 审计报告即预检依据 + 执行前列操作 +
  两段确认。
- gc 无 staging/回滚 → `grace_hours` 宽限期与 upcoming_candidates 如实
  展示；危险操作危险样式 + 两段确认（confirm:true 由客户端硬编码，后端
  400 兜底）。

### D4 血缘图：自建渲染器 + 数据适配层

`analysis-graph-panel` 是会话域执行 DAG 投影（ADR-0097），与 artifact
lineage 的 parents/consumers 边表不同源，不复用。`lineage-adapter.ts` 把
边表适配为 ≤5 深度分层布局（纯函数、可单测），`lineage-graph.tsx` 以轻量
SVG 渲染（≥50 节点渲染预算有断言），为 C 线未来的列级下钻留出适配层
扩展点。

### D5 测试与 mock 契约

沿用仓库「在 API 模块边界 mock」的既有模式（`vi.hoisted` + `vi.mock`，
无 msw 依赖），`test/project/fixtures.ts` 提供三态 fixture 工厂 + 大列表
分页 + ≥50 节点血缘图；`stress-100k.test.tsx` 锁定「DOM 有界/预算内完成」
不变式（100k 行经 TabularDataGrid 分页切片、足迹提取、DAG 布局）。

## 后果

- 项目管理员在侧栏内闭环完成：建数据集 → 预览 → 出产物 → pin → 快照
  save → restore（verify→register）→ 质量审计 → 修复 → gc dry-run/execute。
- 协调点（见 PR）：dataset rename/preview/schema、artifact download/
  revisions、snapshot diff、restore/gc job 化、gc staging、repair dry-run、
  rail 级上传深链，均以后端/对应线合并为前提增强，UI 适配层已留位。
- 回滚 = 隐藏资产区 tab 区块（`ProjectAssetsSection` 单点挂载），无后端
  依赖变更。
