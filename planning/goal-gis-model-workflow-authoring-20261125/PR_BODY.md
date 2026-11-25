# PR Body（草稿；R1/R2 复审后定稿）

## feat(gis-model): visual model authoring and reusable parameterized workflows

03｜可视化 GIS 模型构建器、参数化工作流与可复用专业技能

## 基线
- origin/master `8fe3d76634bfb7052f438325c7db3e0ef3dad040`（merge #2266；任务期内 re-fetch 无漂移）
- Pi vendor pin：本任务未改（方向 01 职责）
- 分支：`zcode/goal-gis-model-workflow-authoring-20261125`；独立 worktree 开发

## 用户旅程（新增能力）
1. **从既有分析生成草案**：成功 SessionPlan / 项目工作流 → 确定性转换成可编辑模型草稿（复用 promotion，skipped 项诚实披露）
2. **可视化编辑**：前端模型构建器 tab（画布增删节点/连线即时合法性提示/参数面板/typed findings/运行 overlay/撤销恢复）
3. **静态验证**：20 码 typed findings（运行时绑定门忠实镜像）（结构/端口/角色/能力可用性/参数约束），error 级阻断发布
4. **参数实例化**：`$param:` 引用 + 声明校验（required/default/min/max/enum）→ 逐节点 wire 注入既有 driver
5. **版本化发布**：零 error 才可发布；`umodel_<slug>` 包进既有 PackageRegistry（semver、不可变指纹、owner/org 域隔离、deprecated 撤回）
6. **跨城市复用**：同模型 revision + 不同 session 数据绑定 → 独立实例（缓存按指纹+权限+输入版本，既有 reuse 栈）
7. **运行与解释**：既有 runtime receipts/事件/局部重算/inspector 全链直通，模型层零重复端点

## WP 验收表
（见 planning/goal-gis-model-workflow-authoring-20261125/acceptance.md；PR 定稿时折叠摘要）

## 架构边界（无第二事实源）
- 不重写 workflow_runtime（scheduler/driver/绑定门/复用零改动）；实例化/绑定是组合 adapter
- 发布复用既有 `WorkflowPackage`+`PackageRegistry`（`umodel_` 前缀区分，与 recipe 包同表同纪律）
- 不触碰 #2272 method_experiment / #2273 data_fusion / #2274 paper_scale / #2275 scenario_harness 功能域
- 共享接线最小化：`app/main.py` +2 行、`app/tools/__init__.py` +1 行、`app/models/__init__.py` +4 行、前端 tab 挂接 append-only、i18n 登记 4 行
- 迁移 `m0111_model_drafts`：单张 additive 表（从 0109 分支；#2273 占 0110，合并前按最新 head 重挂）；upgrade/downgrade 本地验证

## 科学/安全证据
- 真实端到端（heavy）：真实包注册→发布→实例化→session FeatureCollection 绑定→真实 geocompute buffer（点→多边形）→真实 data_fabric 聚合（3/3/3）→真实 matplotlib 渲染 blob→journal 事件；owner 域隔离/未发布拒绝/CAS 冲突/越权绑定不泄露存在性
- 无任意代码/SQL/网络面：closed 词表 + patch 白名单 op + capability 引用注册表校验
- 执行面诚实：未接线能力 `NODE_NOT_EXECUTABLE` 阻断（模板只装可执行等价结构，替代理由在 docstring）

## 本地验证（exact commands）
- 后端全域：`OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pytest tests/unit/model_authoring -q --no-cov`（101+ 新增；含 heavy e2e）
- 回归网：workflow_runtime 376 绿；extensions+gis_tool_conformance 2816 绿（NDVI 1 失败=master 预存，基线 worktree 复现）
- API 契约：response_model 全覆盖；字段快照 sanctioned 刷新（API_CONTRACT_FIELDS_UPDATE=1，+79 行 additive）
- 迁移：`alembic heads` 单头；upgrade→downgrade→upgrade 循环验证
- 前端：pnpm typecheck（零新增错误）/ eslint 定向 / vitest 定向 + i18n 完整性 / 生产 build
实测（2026-11-26，本 worktree）：
- 后端：model_authoring 143 + Pi journey 1 绿（含 heavy 真实 driver e2e）；workflow_runtime 376 绿；api_contract 绿；extensions+gis_tool_conformance 2816 绿（NDVI 1 失败=master 预存）；gis_harness 3623 绿（4 失败=master 预存 catalog 指纹漂移，基线 worktree 逐字节复现）
- 前端：typecheck 零新增（基线 5 错 diff 为空）；eslint 3 警告与 master 逐字节一致；vitest 全量 5796 绿 / 3 失败=master 预存（geoai×2 + i18n 占位符×1）；生产 build 通过
- 迁移：alembic heads 单头 m0111；upgrade→downgrade→upgrade 循环验证；model_drafts 零漂移（master 唯一漂移 lifecycle_policies 为预存）

## 独立评审
- R1 冷审（未参与实现的独立 agent，2026-11-26）：0 P0 / 1 P1 / 4 P2 / 8 P3，全部关闭（42fb6161a）：
  - P1-1 run 端点 project_id 无鉴权（跨项目草稿参数泄漏 IDOR）→ 写权限 + 实例归属项目派生 + PARAMS_UNRESOLVABLE；
  - P2-1 作者端口规则与运行时绑定门分歧 → 静态规则改为绑定门忠实镜像（elevation→terrain_surface；删除把正确接线标可疑的 warning）；前后端同构；
  - P2-2 publish 指针竞态孤儿态 → 幂等收尾 + pointer_stale 回执；P2-3 slug 文案插值；P2-4 primary_output 悬空 → wire id + validate_typed_dag 出口自检；
  - P3：IntegrityError 归一/ref 超长 typed 拒绝/remove_param NOT_FOUND/无草稿可实例化已发布包/文档计数修正等。
- R2 复审：（复审完成后填写）

## 未完成/跳过
（定稿时如实填）

## 开放 PR 重叠与合并顺序建议
- 共享文件交叉：`app/tools/__init__.py`（#2272-2275 均碰：本分支 +1 行注册表项，语义独立）；`app/main.py`（#2272/2273/2275：本分支 +2 行）；`app/models/__init__.py`（#2273：本分支 +4 行 import）；`frontend/lib/i18n/messages.ts`（#2272/2273：本分支 +4 行 namespace 登记）；`tests/quality/snapshots/api_contract_fields.json`（+79 行 additive，合并顺序无关，冲突按行合并）
- 建议合并顺序：本分支不依赖任何开放 PR；任何顺序合并均只需处理上述共享文件的行级冲突
- migration：若 #2273（0110）先合并，本分支交付前将 `m0111` down_revision 重挂到 0110（避免双头）
