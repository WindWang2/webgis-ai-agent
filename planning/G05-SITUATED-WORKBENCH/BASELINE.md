# BASELINE — G05-SITUATED-WORKBENCH（Situated GIS Agent Workbench）

> 审计时间：2026-11-08（实时重查，非快照假设）
> 基准：`origin/master = 585812ee4e2d75612f2b124e6fe6587af3deca5c`（2026-11-07 17:54 +0800，Merge PR #2010）
> worktree：`../webgis-wt-situated-gis-workbench-20261107`，branch `zcode/goal-situated-gis-workbench-20261107`

## 1. 实时仓库事实（gh/git 实测）

- master HEAD 与 Prompt 快照一致（585812ee4），但为实测值；执行期间每次合入前重新 fetch 复核。
- 在途 open PR（与本方向所有权比对）：
  - **#2012** `zcode/goal-pi-native-gis-runtime-20261107`：占 `app/agent_pi_bridge.py`、`app/api/routes/chat.py`、`app/services/chat/pi_*`、`scripts/pi_doctor.py`。**零文件交叠**（我方避让 Pi RPC 内核；不改 `app/services/chat/`、`app/api/routes/chat.py`）。
  - **#2011** `zcode/goal-gis-execution-compiler-20261107`：占 `app/services/gis_harness/{completion/,execution_compiler/,requirement_ir/,registry_validation.py}`、`app/tools/analysis_workflow_tools.py`。**零文件交叠**（我方只动 `gis_harness/{interaction_ir/,map_operations/,display_confirmation.py}`）。
  - dependabot ×7（pip/npm/actions 依赖）：无交叠。
- 未开 PR 的远端 feature 分支全部 ahead-by=0（`gis-harness-map-operations-vnext`、`gis-agent-feature-editing-vnext`、`g3dv2-c-analysis-harness-map`、`swarm-review-fixes-v1`、`pi-gis-harness-usability-v2`）——均已并入 master，无隐性冲突面。
- 近期合并链（本方向相关）：#1974（情境交互闭环 ADR-0283）、#1989（Pi/GIS 可用性）、#1998（载荷预算）、#2008（真实 Story 浏览器旅程）、#2009（session 锁重入）、#2010（恢复后命令资格）。

## 2. 已落地能力（不重做）

| 能力 | 位置 | 状态 |
|---|---|---|
| GISSituation 11 分区契约/编译/diff/观察环 | `app/services/gis_situation/`（contract/compiler/diff/projection/observation/consistency） | 完整（ADR-0252/0180/0285） |
| AnalysisScope 声称面+五链验证面 | `app/services/gis_situation/analysis_scope.py`（derive_claimed_scope / validate_analysis_scope） | 完整（ADR-0285 D1） |
| selection 历史/多轮指代证据 | `selection_history.py` / `turn_chain.py` / `turn_context.py` | 完整（#1974） |
| Interaction IR 19 kind 词表+批量摄入+ledger | `app/services/gis_harness/interaction_ir/`（contracts/ingest/projection/replay） | 完整（ADR-0227+#1974） |
| map_operations 事务（preview/commit/敏感批准/CAS/回执环/指代解析） | `app/services/gis_harness/map_operations/`（contracts/resolver/service，ADR-0278） | 完整 |
| 前端 interaction bus/outbox/projection/invalidations | `frontend/lib/interaction/` | 完整（#1974） |
| selection-store v4（map/chart/table/legend 四源+防回环） | `frontend/lib/selection/` | 完整 |
| map-operation receipt-bus + temporal mutation | `frontend/lib/map-operations/`、`lib/temporal-map/` | 完整 |
| situation status bar（六态/降级） | `frontend/components/workbench/situation-status-bar.tsx` | 完整 |
| Feature Edit preview/confirm/undo + journal | `app/services/feature_edit/` | 完整（ADR-0277） |
| e2e journeys j10–j12（situation restore/六态/降级） | `frontend/e2e/journeys/` | 完整（mock 档） |

## 3. 真实缺口（本方向增量，实测 file:line）

### G-1 #2004 未修：IR 摄入静默截断 selected_ids 到 8【P0，SW-02 核心】
- `app/services/gis_harness/interaction_ir/contracts.py:248`：`out[key] = [_clip(v) for v in value[:8] ...]` —— 任意 list 值（含 `selected_ids`）截断到 8，**无 omitted 计数披露**。
- `contracts.py:112`：`feature_selected` 的 payload_keys 白名单不含 `matched_count`/`omitted_count` —— 前端 box-select 的溢出披露字段即使上报也被白名单丢弃。
- 前端侧：`frontend/lib/selection/selection-store.ts` `MAX_SELECTED_IDS=50` + `brush-select.ts` 溢出时带 `matched_count`+bbox 谓词描述符 —— **前端已按 50+披露上报，后端截到 8 且不披露**：框选 20 学校 → Agent 只见 8，违反"选 20 分析 20"。
- `analysis_scope.py:89` `MAX_SCOPE_FEATURE_IDS=50`（声称面上限与前端对齐），但入口数据已被截到 8 —— 链路断裂在 IR 摄入层。
- 复现路径：框选 >8 要素 → interaction 快照 selection.selected_ids ≤8 → derive_claimed_scope kind=feature_ids 但只有 ≤8 ids → 工具"分析这些学校"只分析 8 个，无任何披露。

### G-2..G-N：审计子代理全链路报告（file:line 已核，2026-11-08）

**演示假象风险清单（按危险度）**：
1. 【P0·已修】框选 >8：UI"已框选 23 个"（map-panel.tsx:1483-1488 + zh-CN/map.json:126），模型收 8 id 无总数（bus.ts:209-212 + contracts.py:248 + 白名单无 matched_count）。→ 本 PR 已修（见 §8 增量）。
2. 【P0·已修】无稳定 id 图层框选零上行 IR（map-panel.tsx:1470 门）——模型对框选完全失明。→ 已修（count-only 事件）。
3. 【P1】selection-store 50 ids"绝不进 LLM context"（selection-store.ts:11-13）——联动丰富度与模型感知不一致。→ 随 #1 修复对齐（IR 现可承载 50 + matched_count）。
4. 【P1】display_confirmation 默认 auto（display_confirmation.py:35-38）——"用户已认可"层形同虚设；`GIS_FINAL_DISPLAY_CONFIRM` 未设即恒 confirmed。
5. 【P1】框选 bbox 谓词只在 selection-store（客户端），IR feature_selected 白名单不含 bbox——"刚才框选的那片区域"不可指代为空间范围（仅 AOI 工具几何可走 affordance 环）。

**逐 SW 缺口矩阵**（已落地面见 §2，此处只列缺口）：

| SW | 缺口 | 评级 |
|---|---|---|
| SW-01 | 三处分散（状态条/AgentRunPanel/AnalysisGraph），非"单一情境视图"；但只读投影端点已聚合 | P2 |
| SW-02 | #2004 截断族（ids≤8×双侧、matched_count 白名单外、无 id 零上行、bbox 谓词不进 IR、selection_history MAX_HISTORY_IDS=8+死标志、chart categories 截 8 上行） | **P0（本 PR 核心）** |
| SW-03 | 基本闭环（同引擎同 CAS + user-wins + _mutation_dedup 交叉校验） | P2 |
| SW-04 | map-op undo 仅 presentation 族（invertible 判定 service.py:869-879）；**无 redo**；symbol/viewport/component 不可逆仅文字披露 | P1 |
| SW-05 | 三层面板手动刷新非实时（projection-invalidations 通道已存在未订阅）；turn 归属依赖 receipt.provenance.turn_id | P2 |
| SW-06 | 覆盖良好（Clarification/VisualRepair/EditProposal/map-op clarification） | P2 |
| SW-07 | 机制密度高（epoch/水印/会话锁/tombstone/#2009/#2010 已修）；ledger 环 128 条 FIFO 淘汰属设计内披露 | P2 |
| SW-08 | recharts 点击无键盘路径；chart categories 上行截 8 | P1 |
| SW-09 | 四层区分结构齐备（工具 success/MapSpec 接受/渲染 ACK/用户确认）但 display-confirmation 默认 auto → 用户确认层不生效；完成卡未聚合导出态 | **P1** |
| SW-10 | j10-j12 仅 mock 档；real 档未含 situation 断言；后端 E5 八组已真实 dispatch | P2 |

**多轮指代现状**：selection_ref current/previous/epoch_seq 可解析（selection_history.py:134-175）；空间工具隐式消费经验证 scope（_spatial_selection.py:53-110，验证失败=报错不静默整层分析）；**无视口史代**（"上一张图"无句柄，viewport_settled replace_pending 只留最新）。

## 4. 冲突矩阵

| 在途 PR/分支 | 我方所有权文件 | 交叠 | 处置 |
|---|---|---|---|
| #2012 Pi native runtime | 无（避让 chat/pi_*） | 0 文件 | 避让；不依赖其新接口 |
| #2011 execution compiler | 无（避让 completion/requirement_ir） | 0 文件 | 避让；不改 analysis_workflow_tools |
| dependabot ×7 | — | 0 | 无 |
| 存量 vnext 分支（ahead=0） | — | 0 | 已并入，直接基于 master 增量 |

## 5. 基线测试（本 worktree 实测）

- 后端：`pytest tests/test_gis_situation_analysis_scope.py tests/unit/gis_harness/interaction_ir/ tests/unit/gis_harness/map_operations/` → **191 passed**（90.8s）。
- 前端：`vitest run lib/interaction lib/selection lib/map-operations lib/temporal-map components/workbench components/map/map-harness-cards.test.tsx` → **194 passed**（20 files）。

## 6. 风险登记

- #2012 若先合并并改动 `app/api/routes/chat.py` 的 SSE 事件面 → 我方前端 receipt 联动需 rebase 复核（低概率，接口面未动）。
- #2004 修复若被他人抢先：以"范围正确性失效返回测试"为主交付，检测重复即下沉到下一缺口。
- e2e 真浏览器档依赖本地环境（playwright），无凭证时用 mock 档 + 披露。

## 7. 避让决策

- 不重做 #1974 选择历史/turn 链/六态状态条；只在断裂处（IR 截断、验证返回、UI 披露）补增量。
- 不动 Pi vendor pin、不新建 chat 引擎、不重制图 layout solver、不做第二套多代理控制台。
- Issues #1999–#2003、#2005–#2007（导出/指北针/族锁/单位/summary）非本方向集成链，不抢修；仅 #2004 属 SW-02 直接核心（选择范围正确性），纳入最小修复。

## 8. 本 PR 增量清偿（SW-02 选择范围正确性族）

1. **IR 契约 additive**（contracts.py）：`feature_selected`/`table_selection_changed` 白名单 + `matched_count`/`ids_truncated`；`selected_ids` 专属上限 50（三方对齐：selection-store MAX_SELECTED_IDS=50 / MAX_SCOPE_FEATURE_IDS=50 / MAX_SELECTED_IDS_PAYLOAD=50）；selection kind 载荷预算 4096B（50×32 字符 id 集 ≈1.9KB，512B 会把截断恶化成整事件拒绝）；`ids_truncated` 服务端权威派生（客户端申报被覆写）。
2. **投影/声称面/验证面贯通**：projection `_selection_view` 透传披露键；`derive_claimed_scope`/`validate_analysis_scope`/ClaimedScope/ValidatedScope 携带 matched_count+ids_truncated（truncated_sample 进 checks.features）；selection_history MAX_HISTORY_IDS 8→50 + matched_count/ids_truncated 史代字段 + previous 句柄披露同权。
3. **前端上行修复**（bus.ts）：emitFeatureSelected/emitTableSelectionChanged `.slice(0,8)`→50 + matched_count + ids_truncated；map-panel 框选传递 matched_count；**无稳定 id 图层框选 count-only 上行**（不再零事件）；contract.ts/contract-manifest.ts 镜像 + manifest 双闸更新。
4. 测试：后端 11 新测试（test_selection_range_disclosure.py）+ 2 旧测试语义更新；前端 5 新测试（emitters.test.ts SW-02 describe）。

## 9. 尚待清偿（本 PR 后续轮次）

- SW-02 残余：chart categories 上行 8→20（与 selection-store 类别≤20 对齐）；bbox 谓词进 IR（"刚才框选的区域"可指代）。
- SW-04：map-op undo 扩 symbol 族 + redo 栈。
- SW-05：AgentRunPanel 订阅 projection-invalidations 实时刷新。
- SW-08：chart 键盘可达。
- SW-09：display-confirmation required 试点 + 完成卡聚合导出态（feature flag 纪律）。
- SW-10：J1-J8 旅程回放落地（INTERACTION_REPLAY_GOLDENS.md + UX_JOURNEY_EVIDENCE.md）。
