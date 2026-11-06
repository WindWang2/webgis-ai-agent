# feat(goal03): Agent 驱动的持久数据要素编辑 —— 预览/确认/撤销闭环（ADR-0277）

## 概要

在真实生产路径补齐 master 上完全缺失（absent）的**数据集要素级写入**能力：用户框选真实要素 → Agent 构建有界预览（类型/枚举/单位校验 + before/after diff）→ 受信任通道提交（owner 卡确认或 policy 放行的 safe 属性面）→ 数据 content_revision 递增 + MapSpec 新鲜度盖章 → 地图增量刷新 → journal 审计 + undo/redo 反向走同一 CAS 写路径。

**为什么不是已有能力**：sketch 是 transient 草图；MapSpec mutation 是显隐/样式；Interaction IR 是遥测；修复管线是数据集级 new-ref 变换——本 PR 是唯一一条「修改既有数据集中指定要素」的受信写通道（去重矩阵见 planning/agent-feature-editing/PR_OVERLAP_MATRIX.md）。

- master 起始 SHA：`4b3629c90ee977e287f7b1fb4fd827b0fad7428c`
- master 结束 SHA：（PR 创建时 origin/master，无前进即同上；已二次去重，唯一 open PR #1868 为数据准备方向，无实质重叠）

## 用户场景（E2E journey 测试钉住，tests/unit/feature_edit/test_e2e_journey.py）

真实摄入管线（学校+道路）→ IR 框选 3 校（selected_ids additive 扩展）→ Agent 工具预览（改类型+吸附到路）→ Agent 直提被 typed 拒（CONFIRMATION_REQUIRED）→ 用户卡 approve → 未选中要素逐字不变 / 选中要素类型+坐标更新 → MapSpec 源 content_revision 盖章 = 数据新版本 → situation 投影让 Agent 下一 turn 可观察 → undo 恢复类型/坐标 → 历史可见 → 同 plan 重复决策幂等不写两版。

## 信任边界与安全

- **坐标变更（move/snap/repair）与非 safe 计划的唯一合法通道 = owner 鉴权 REST 决策端点**；Agent 工具直提仅当 plan.safe（纯 set_fields、零 findings、≤50 要素）且 owner 已设 `allow_safe_attribute`。
- actor/origin/session 全部服务端裁决；跨会话/不存在统一 404 不泄存在；幂等键 plan 命名空间化（不得重放他人 receipt）。
- 原始上传文件（data/uploads/**）绝不触碰；编辑只动会话 ref 载荷 + 有界 journal（map_state，FIFO 128 条/64KB + dropped 披露）。
- 锁纪律：chat turn 持锁内经 ContextVar 透传（不自锁）；MapSpec 盖章绝不在编辑锁内调用（turn 内转后台锁外执行）；并发双提交一成一 409。

## 中心文件（全部行级最小 patch）

`app/services/mapspec/intents.py`（+RefreshDataRevisionIntent）、`mutation_handlers.py`（+handler）、`mutation_registry.py`（+注册）、`representation_binding/taxonomy.py`（+1 行）、`gis_situation/contract.py`/`compiler.py`（additive feature_edits fact）、`interaction_ir/contracts.py`+manifest（additive selected_ids 键）、`app/tools/__init__.py`（+1 行）、`app/main.py`（+1 行 router）、前端 use-sse-stream/chat-tab/context-panel/adapters/message-reducer/undo.ts/bus.ts/use-feature-selection/map-panel（additive 接线）。

## 测试与验证（本地，不等远端 CI）

- 后端：tests/unit/feature_edit/** 64 绿（core/routes/tools/concurrency/e2e/r1 回归钉）；工具命名 vector_feature_edit_*（preview tier-1 恒可见入口；commit/history tier-2 走 list_available_tools 自救——dataset 域 schema 预算既有临界，命名与 tier 选择保证不挤占既有工具（audit4 reproject 红线测试钉住））+ 邻域（interaction_ir/situation/contract/mutation parity 子集/tool registry）163+ 绿；全量 tests/ 除 1 个**既有环境基线红**（`test_mutation_parity_corpus[set_scene_invalid_mode]`：pydantic 2.13 错误消息 URL 漂移，基线 commit 同样红，与本分支无关——见 EVIDENCE）。
- 前端：tsc --noEmit 绿；eslint 新文件零错误；vitest 全量 494 文件 / 4674 测试绿。
- 生成物：openapi 快照 / endpoint-scope-matrix / api-docs / staleness 账本均用项目生成器再生，check_generated_staleness 绿；situation schema dump 再生；ADR 编号契约绿。
- 未跑项如实披露：Playwright/真实浏览器（本机未配置）——地图渲染消费路径以 mapspec 盖章 + content_revision 管线断言代替；vendor/pi 子模块网络受限（既有环境基线）。

## 独立评审

- R1（独立 subagent，全量 diff）：P0=0 / P1=3 / P2=10 / P3=10 → **全部清偿**（planning/agent-feature-editing/INDEPENDENT_REVIEW.md 有逐项映射与回归钉）。
- R2（清偿复核）：见 INDEPENDENT_REVIEW.md R2 节（PR 时点状态：无新引入 P0/P1/P2）。

## 回滚

单分支 revert 即可（无 DB 迁移；map_state 键 `_feature_edit_*` 为 additive，旧代码忽略；工具注册为独立模块行）。Rollback 后已提交的编辑数据留在会话 ref（journal 键成为死数据，随会话生命周期消亡）。

## 后续方向（不在本 PR）

- Playwright 真 UI journey（浏览器环境就绪后）；undo/redo 的工具通道（当前用户 REST/操作记录通道已闭环）；journal 跨会话持久化（当前随 session store 生命周期，与 mapspec/checkpoint 同族）。
