# feat(interaction): 情境化人机协同交互闭环 —— 选择成为受控 Agent 输入 / 多轮指代 / turn 链 / 状态面（ADR-0283）

## 范围

把既有资产（Interaction IR / GIS Situation / map_operation 回执 / turn journal /
selection-store）**贯通**为多轮人机协同闭环，不建任何第二真相：

1. **E0 事实审计**：12 组多轮操作事件流 characterization 测试钉死现状（15 tests）。
2. **S1 AnalysisScope**（`gis_situation/analysis_scope.py`）：ClaimedScope（纯函数三源
   合并）× ValidatedScope（五链服务端验证：ref 所有权→宿主层→revision→要素存活→
   有界披露；reason 封闭词表 fail-closed）；situation.analysis.selection_scope additive
   fact + `[GIS 情境]` 投影行「选择 scope(未验证)」；只读工具
   `webgis_selection_scope`（tier-2；`GIS_SELECTION_SCOPE=0` typed disabled）。
3. **S3 Selection History**（`selection_history.py`）：ledger 纯函数重建选择代（≤6）；
   `previous`/`epoch_seq` 句柄解析「刚才那组学校」，不可解析返回 None 绝不随机挑。
4. **S2 IR 词表三连收**：`chart_selection_changed` / `table_selection_changed` /
   `temporal_window_settled`（mutation-linked，含 `pinned` 用户锁定面，replace_pending）
   ——全部有真实前端钩子（ADR-0227 纪律）；manifest 双闸再生；chart-panel /
   table-panel / temporal commitUserTemporal 结局穿透接线；IR 投影 additive
   `chart_selection`/`temporal_window` 面。
5. **S4 TurnChain**（`turn_chain.py` + `situation_views.py` 路由）：journal×回执×IR×
   provenance×观察代 只读 join；三类可解释 diff（user_explicit /
   agent_proposed_pending / executed_observed），迟到旧观察不升格（pending_execution）；
   `GET /chat/sessions/{sid}/situation` 与 `/turn-chain`（require_owned_session）。
6. **S5/S6 恢复与状态面**：`use-situation-projection`（服务端事实源恢复，boot 选择
   重放仅在本地空白时一次，用户现场优先）+ `SituationStatusBar` 六态
   （范围/选中含来源/已验证结果/执行中/等待验证/可重试；role=status+aria-live；V4
   token；zh/en）+ 回执 additive `invertible` 披露 + agent 可逆回执自动录 undo 栈
   （反向重放既有 CAS 通道，遵守 ADR-0278「回退=再提交」）。
7. **S7 旅程**：后端 8 组可回放多轮旅程（真实 dispatch 链，选择真实约束 feature_edit
   动作目标）+ 浏览器 j10/j11（@smoke）/j12。

## 与已有 PR 区别

- 与 open **#1972**（awf-v2：analysis_workflows/tools.py）**文件零重叠**；scoped
  analysis 只经显式工具传参，不碰其编排。
- 与 open **#1965**（g3dv2 review：map-panel/use-cartographic-observation/viewCommands）
  **文件零重叠**；状态条旁路挂载，map-panel 未触碰。
- 与已合并 #1623/#1873/#1874/#1875/#1877/#1879 全部为复用+additive：确认门/回执环/
  linked views/temporal 契约/recovery 词表原样；本 PR 只补「贯通层」。

## 验证（本地，命令与真实计数见 planning/.../EVIDENCE.md）

- 后端：E5 旅程 8 passed；situation/IR/scope/history/工具/路由/回执 全族
  480+ passed；capability parity 118；pi_abi 11；ruff 干净；import boundaries OK；
  openapi/scope-matrix/api-docs 生成物再生。
- 前端：vitest 邻域 550+ passed；tsc 0 错；playwright j10-j12 3 passed、@smoke 4 passed
  （**j9 为预存环境失败**：干净基线 stash 复现——mock 档未打桩端点泄漏真实后端
  CORS 噪声，与本 PR 无关）。
- NOT_RUN：E2E real 档（本地无真实后端栈/夜间账号）；Redis 集成（走 Memory 降级路径）。

## 回退开关 / 兼容性

- 全部 additive：situation fact 默认 unknown（旧构造方零改动）；manifest/schema
  dump 再生；`GIS_SELECTION_SCOPE=0` 关停工具（typed disabled）；状态条数据面
  失败诚实 error 不阻断壳；map_operations 回执新键旧读者忽略。
- ADR-0283（预判 #1972 撞号跳过 0282，watermark→283）+ UL 词条 + planning 全套。

## 技术债 / 诚实披露

- 图例分级过滤仍无 settle 钩子（ADR-0227 纪律未收词，如实记录）；
- 「上一张图」以 fingerprint 短码披露（不建 per-generation 实体）；
- AOI 面经 canvas affordance 环消费（框选要素集优先，几何兜底 bbox）；
- j9 预存环境失败未在本 PR 修复（属 e2e 基建维护线）。
