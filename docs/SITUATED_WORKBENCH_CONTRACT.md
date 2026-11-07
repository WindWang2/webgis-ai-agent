# SITUATED_WORKBENCH_CONTRACT — 地图—对话情境一致性契约（G05-SITUATED-WORKBENCH）

> 版本：1.0（2026-11-08）；权威实现见 ADR-0227/0278/0283/0285/0287。
> 本文档是**对外可审计的契约面**：每个用户地图动作如何成为 Agent 可信输入、
> 范围如何披露、终验如何分层、竞态如何隔离。每条都标注权威代码位。

## 1. 单一 GIS 情境视图（SW-01）

**权威**：`GISSituation` 11 分区契约（`app/services/gis_situation/contract.py`，
extra=forbid）；单一只读投影端点 `GET /chat/sessions/{sid}/situation`
（`app/api/routes/situation_views.py`）聚合 situation + 一致性检查 +
interaction 快照 + 选择史 + claimed scope。

- 每个事实带 `source`（来源标签）；`SituationRevision` 四分量单调
  （mutation/observation/frontend/interaction），任何分量倒退即拒收。
- 投影有界、确定性（无 wall-clock/随机/LLM）、显式 unknown。
- UI 消费面：`SituationStatusBar`（六态，a11y role=status）+
  `AgentRunPanel`/`AnalysisGraphPanel`（结构化事实，非私有思维链）。
- 本 PR 增量：面板事件驱动刷新（SW-05，§5）。

## 2. 选择 → Agent 输入（SW-02，#2004 修复后契约）

**上行链**：MapLibre 点选/框选 → `emitFeatureSelected`
（`frontend/lib/interaction/bus.ts`）→ outbox 批量 POST →
`ingest_interaction_batch`（ledger 环 ≤128 条/96KB）→ 投影
`project_interaction_snapshot` → turn 情境块 + `webgis_selection_scope` 工具。

### 2.1 范围与披露（三方对齐上限 50）

| 面 | 常量 | 位置 |
|---|---|---|
| 前端选择存储 | `MAX_SELECTED_IDS = 50` | `frontend/lib/selection/selection-store.ts` |
| IR 摄入（selected_ids 专属） | `MAX_SELECTED_IDS_PAYLOAD = 50` | `interaction_ir/contracts.py`（manifest 双闸） |
| 声称面/验证面 | `MAX_SCOPE_FEATURE_IDS = 50` | `gis_situation/analysis_scope.py` |

- `matched_count`：真实命中数（UI 反馈同源），有界整数 ≤10⁷。
- `ids_truncated`：**服务端权威派生**（原始 id 数 > 50 即 True；客户端申报
  覆写；仅当 selected_ids 保留于载荷时派生）。
- Oracle：**选 20 → Agent 见 20**；框选 >50 → 50 样本 + matched_count
  全量披露（消费方必须能说"前 50/共 N"，或改走 bbox/AOI 谓词）。
- `bbox`（4 元数值）随框选上行——"刚才框选的这片"作为空间范围可指代
  （ClaimedScope.bbox；数值校验在消费侧 `_bbox4`）。
- 无稳定 id 图层框选：count-only 事件（layer_id+matched_count[+bbox]）
  ——**零上行即失明**，空集语义优于沉默。
- 图表类别上行 ≤20（对齐 selection-store `selected_categories`）；
  通用 list ≤8 纪律不变。
- 字节预算：选择 kind（feature_selected/table_selection_changed/
  chart_selection_changed）4096B；通用 512B。超载荷预算整事件拒收
  （rejected_payload）；单条 ledger 记录预算与载荷预算同层（复审 P0-1
  钉扎：`_MAX_RECORD_BYTES ≥ MAX_PAYLOAD_BYTES_SELECTION`——消
  accepted-but-dropped 静默丢失）。

### 2.2 两段式 scope 验证（ADR-0285 D1，本 PR 补披露透传）

- `derive_claimed_scope`（纯函数）：IR 投影 selection > pre-turn 快照 >
  canvas affordance 环 AOI；输出 ClaimedScope（含 matched_count/
  ids_truncated/bbox）。
- `validate_analysis_scope`（IO 壳，fail-closed）：ref 所有权（跨会话 ref
  拒绝）→ 宿主层在架 → revision 一致（漂移→stale）→ 要素存活
  （≤20k 逐点核对）→ 有界披露。`checks.features` 含
  `truncated_sample`（样本截断披露）；框选范围独立键 `selection_bbox`
  透传（`bbox` 键保留数据集描述符语义——两者分义，空间工具 extent
  兜底必须用 selection_bbox）。
- 空间工具隐式消费：经验证 scope 失败 = 报错，不静默整层分析
  （`app/tools/_spatial_selection.py`）。

### 2.3 多轮指代

- `selection_ref`：`current` / `previous` / `<epoch_seq>`
  （`selection_history.py`，史代 ≤6、每代 ids ≤50、含 matched_count/
  ids_truncated——历史指代披露与当前代同权）；不可解析返回 None →
  工具层最小澄清（`selection_ref_unresolvable`），绝不随机挑。
- 部分取消（带 id 的 deselect）从当前代移除 id——这是**用户显式决策**，
  与摄入截断（静默）以摄入层披露标记区分，不用计数推断。
- 视口史代（"上一张图"）**不在本契约**（viewport_settled 只留最新；
  留待后续，见 ADR-0287 Consequences）。

## 3. 直接操作 ↔ Agent 意图同义（SW-03）

- 用户与 Agent 同一 MapSpec mutation 引擎/CAS/user-wins 守卫：
  Agent 面 `webgis_map_operation` → `preview/commit_map_operation`；
  用户面 `user-mutation.ts` → 同族 mutation 端点。
- IR mutation-linked kind 服务端 `_mutation_dedup` 交叉校验——
  rejected/superseded 绝不投影为成功。
- 用户锁定布局：`user_wins` 守卫拒绝 Agent 覆盖（显隐/透明度族）。

## 4. 预览、确认、撤销、版本（SW-04）

- preview 零突变（before/after/风险分类/歧义候选 ≤6）；commit 带
  CAS（`expected_revision`）；中链 superseded 停止并披露
  `chain_aborted`；状态聚合不掩盖部分成功。
- 破坏性（remove_layer）：`confirmation_required` pending 回执 +
  owner 显式批准路由（零突变直至批准）。
- **可逆性（本 PR 扩展）**：显隐/透明度 + **paint/符号尺寸（before 全
  数值在场）** → `invertible=true`；before 缺值诚实回落不可逆（无法恢复
  "原本缺失"）。前端 undo/redo：presentation 族经
  `commitLayerPresentation`，样式族经 `commitLayerStyleAndCommit`
  （`styleCommand`）——同通道反向重放，非状态回写。
- 数据层编辑撤销：feature_edit journal undo/redo（ADR-0277，独立通道）。

## 5. 可解释执行面板（SW-05）

- 三层：AgentRunPanel（阶段/工具/执行节点/产物/地图效应）+
  AnalysisGraphPanel（goal/execution/product 结构化摘要）+ turn-chain
  三分类（user_explicit/agent_proposed_pending/executed_observed，
  `observed_revision >= mutation_revision` 才算 executed_observed）。
- **只展示结构化事实/公开理由，不展示模型私有思维链**。
- 刷新：手动 + 事件驱动（`projection-invalidations` 广播 500ms 防抖，
  会话身份复核——本 PR SW-05）。
- 点击操作定位产物/图层/失败回执（产物 registry 与 ReceiptCard）。

## 6. 澄清与修复（SW-06）

- 城市/单位歧义：Requirement IR 澄清（blocking 才问，≤2 问/回合）→
  `ClarificationCard`（结构化应答，非 NLP）。
- 缺图层/目标多义：map-op `clarification_needed` + candidates ≤6
  （整笔不写状态）。
- 视觉缺陷：`VisualRepairCard`（plan 预览 → 批准 CAS / 拒绝记忆）。
- 数据编辑：`EditProposalCard`（owner 决策路由 + diff ≤12 行）。
- **禁止**绿色完成徽章覆盖 partial/blocked（ReceiptCard 状态词表如实）。

## 7. 竞态与隔离（SW-07）

- outbox：per-tab 实例 id（sessionStorage+随机后缀）、client_seq 严格
  单调、ack 水印按**会话**作用域、enqueue 快照 sessionId/token、
  迟到响应只裁同会话队列。
- 服务端：session 锁 fail_on_degraded 503、tombstone 410、
  per-instance 水印表 ≤8（FIFO）、gap 披露。
- 切会话：`resetLiveState` 级联清 selection/viewport/filter/chart/
  temporal；selection epoch bump 迟到发布失效；回执总线跨会话丢弃；
  situation 投影三重 isCurrent 守卫。
- 取消：turn epoch bump——旧 turn 图层回填/观察修复丢弃；abort 经
  拥有 turn 的 worker bridge 定向（#2009）。
- 会话恢复后命令资格动态重算（#2010）。

## 8. 图表/地图联动与可达性（SW-08）

- 联动为**真实数据过滤**：chart→map 编译 MapLibre filter 表达式；
  chart→table 行级过滤；map 框选/表格点选→map id 过滤高亮；视口→图表
  opt-in（inline bbox / MVT 服务端聚合带证据披露）。
- 图表类别选择：点击 + **键盘（原生 select，Tab/方向键/Enter）同链**
  （publishSelection + IR 上行；空值项=清除）——本 PR SW-08。
- 面板卸载清自己发布的选择；无主 stale filter 不残留。

## 9. 地图终验用户闭环（SW-09）

四层终态**各归其位、互不冒充**：

| 层 | 权威 | UI 承载 |
|---|---|---|
| 工具 success | map-op receipt status/applied_count/failed_count | ReceiptCard |
| MapSpec 接受 | final_revision/final_fingerprint（CAS） | ReceiptCard（rev 行） |
| 渲染 ACK | `_cartographic_observation` 代次门 | FinalAcceptanceLine（代次） |
| 用户确认 | display-confirmation（mode+ack 新鲜度门） | FinalAcceptanceLine（auto 披露/required 交互卡） |

- auto 模式**明确披露**"自动确认（auto 模式，未请求用户确认）"——
  渲染已发生绝不冒充用户已认可（本 PR SW-09）。
- 取消/重试：turn epoch 守卫，旧 turn 回执/完成卡不重复生成；
  operation_id 幂等重放返回存证回执。
- 用户继续调整不丢情境：MapSpec 单一真相 + 情境投影随交互失效广播刷新。

## 10. 不变量（Oracle 摘要）

1. 选 N（≤50）要素 → Agent 见 N 个 id；>50 → 50 + matched_count 披露。
2. UI 反馈数 = IR matched_count = 声称面 matched_count（同源）。
3. 截断标记由服务端派生；客户端申报不可信。
4. 旧事件/旧会话/取消后的迟到回写一律被 epoch/水印/会话守卫丢弃。
5. 终验四层可区分且 UI 如实分层；auto 不冒充用户确认。
6. 破坏性操作零突变直至 owner 显式批准。
7. 所有 payload 有界；超界整事件拒收并计数披露，绝不静默缩小。
