# ADR 0285 — 情境化人机协同交互闭环（Situated Agent Workbench vNext）

- 状态：Proposed（随 zcode/goal-situated-agent-workbench-vnext-20261105 PR）
- 日期：2026-11-06 · 基线 master 40cda057a
- 关联：ADR-0227（Interaction IR V1）· ADR-0252（GIS Situation）· ADR-0278（map
  operation 事务）· ADR-0277（feature editing）· ADR-0268（turn journal）·
  ADR-0281（recovery）· ADR-0059（Selection 不是 MapSpec）
- 编号说明：本地最终整合为避免与其他 open PR（含 #1975）的 ADR 撞号，
  将本 workbench ADR 从 0283 调整为 0285；adr watermark 推进至 285，
  保留主修复 migration watermark 103。

## 背景

任务书（05_HUMAN_AGENT）要求把既有资产贯通为「Agent 与用户共同维护一个可信地图
情境的多轮制图与交互分析」。两轮全量事实审计（后端 + 前端，见
`planning/situated-agent-workbench-vnext/SOURCE_INVENTORY.md`）确认的断点：

1. **选择不成为受控分析输入**：IR/`_cartographic_context_observation` 里的 selection
   只有 feature_edit 与 scene3d 相机两个消费者；`gis_situation.queries` 结构化查询
   零生产调用；分析工具需 LLM 显式传 ref/bbox，无「当前选择」服务端验证通道；
   `active_aoi` 投影零消费者。
2. **词表缺真实钩子的交互**：图表类别选择（chart-panel onClick）、表格行选择
   （table-panel）只进前端 selection-store 不上报；temporal 时间轴（#1877 后有 UI）
   无 IR kind——三者都是 ADR-0227「有真实钩子才收词」纪律下今日应收而未收的。
3. **多轮指代无句柄**：IR 投影只保留当前唯一 selection（deselect 即清空），「刚才
   那组学校」不可解析；「上一张图」无 per-generation 引用实体。
4. **无一体化 turn 链投影**：turn_journal 与 IR 账本无衔接（共同锚仅 mutation id），
   「用户显式改变 / Agent 提议未提交 / 已执行可信观测」三类状态无统一可解释面。
5. **恢复只读图层不读交互事实**：前端刷新恢复图层/相机/workbench doc，但不消费
   服务端 IR/situation 投影；状态面（分析范围/选中对象/结果来源/等待验证/可重试）
   缺失。
6. **e2e 无多轮交互旅程**：j1–j9 零选择/框选/图表联动覆盖。

## 决策

### D1 AnalysisScope：声称（纯函数）与验证（IO 壳）分离

`gis_situation/analysis_scope.py`：`ClaimedScope` 从 IR ledger 投影 + pre-turn 快照
纯派生（编译期零 IO，进 `situation.analysis.selection_scope` fact，additive
default unknown）；`validate_analysis_scope` 在 session store 只读验证五链（实现序：ref 所有权→宿主层在架→content_revision
一致→要素存活→有界披露），全 reason
封闭词表，失败不猜默认。AOI 面消费 canvas affordance 环既有有界几何。新只读工具
`webgis_selection_scope`（独立文件注册，不碰 #1972 占用的 tools.py）返回
ValidatedScope，agent 显式传参给既有分析工具——**不向 ToolDispatchService 注入
隐式 scope**。

### D2 IR 词表三连收（全部有真实钩子，同 commit 落前端 emit + manifest 双闸）

`chart_selection_changed`（chart-panel onClick）/ `table_selection_changed`
（table-panel 行点选）/ `temporal_window_settled`（temporal commit，mutation-linked
带结局，replace_pending）。不收：图例分级过滤（无 settle 钩子）、measurement_clear
（仍无钩子）——纪律边界如实记录。

### D3 选择史投影（多轮指代证据面，纯函数）

`selection_history.py` 从 ledger 环重建 ≤6 个「选择代」（epoch_seq/ids/ref/
superseded/cleared）；`webgis_selection_scope` 接受 `selection_ref:
current|previous|<epoch_seq>`；不可解析时 typed reason + 最小澄清候选（沿
#1874 resolver 模式），绝不随机挑数据。「上一张图」以 mapspec fingerprint 短码 +
receipt 环披露（不建第二实体）。

### D4 TurnChain 投影（只读、不写权威、不建调度）

`turn_chain.py` join turn_journal × map_operation receipts × IR mutation-linked
事件 × provenance × observation sequence，产出 per-turn 变更分类
（user_explicit / agent_proposed_pending / executed_observed，纯规则：origin +
receipt 状态 + observed_seq ≥ mutation_revision）；迟到旧观察不升格为
executed_observed。端点 `GET /chat/sessions/{sid}/turn-chain` 与
`GET .../situation`（JSON 投影 + inconsistencies + selection_history + scope）
挂新 route 文件，require_owned_session。

### D5 恢复 = 服务端事实源读取（localStorage 非权威）

前端 boot/重连时 GET situation → 状态面即时恢复；scope usable 时经 selection-store
重放地图高亮 filter（复用既有 filter expression）。不做交互史全量重放（ledger 环
淘汰即如实披露 dropped）。

### D6 Undo 只补录制不造原语

后端回执 additive `invertible` 披露（presentation 组可逆；remove_layer 不可逆——
数据层撤销归 feature_edit 既有 /undo）。agent 起源 commit 若未入前端 undo 栈，在
receipt-bus 消费处 additive 录制反向命令（反向重放既有 CAS 通道，符合 ADR-0278
「回退=再提交一次 mutation」）。

### D7 工作台状态面（最小 UI，V4 token + a11y 模式沿用）

`situation-status-bar.tsx`：分析范围/选中对象（含来源）/结果来源/执行中/等待验证/
可重试六态；role="status" + aria-live polite；zh/en i18n；暗亮双主题经既有 token。

### D8 Journey 验收（j10–j17）

两层旅程：后端回放层 8 组（J1–J8：多轮指代/框选 AOI/图表上行/user-wins/换区筛除/
刷新恢复/迟到旧观察/跨会话 fail-closed，真实 dispatch 链）；浏览器层 j10–j12
（@smoke 恢复与六态、降级），mock 档为基线；real 档按环境尽力并如实标注。

## 不做（红线重申）

- 不建第二 selection/scope/chain 真相——全部投影只读派生自权威 store；
- 不自动向工具注入 scope（agent 显式传参，执行权仍归 ToolDispatchService）；
- 不改 map_operations/feature_edit 的事务与确认语义（只加 additive 披露/录制）；
- 不碰 #1972（`app/tools/analysis_workflow_tools.py`）与 #1965（map-panel 内部/observation/
  viewCommands）占用面；map-panel 若接入只走其既有 props/事件面之外的最小 diff；
- 不虚构表现层强原子 undo；GIS job 撤销/重试遵循 ADR-0281 处置词表。

## 验证

S0 characterization（12 组操作现状追踪）→ 每切片 TDD（红→绿→回归）→ 后端
interaction_ir/situation/新模块 + 前端 interaction/selection/workbench 邻域回归 →
manifest 双闸/openapi/scope-matrix/api-docs 生成物再生 → journey mock 全绿 →
独立 review gate（P0/P1=0）→ PR。


### Independent pre-landing validation corrections

The desired host layer and its canonical session source reference are checked from one MapSpec read before accepting a claimed dataset ref. `source_ref_mismatch` and `layer_unavailable` are closed failure reasons; ref/ref_id/url follow the existing MapSpec source resolver. An explicitly cleared or chart-replaced current selection does not revive an older pre-turn selection. `previous` remains an explicit historical handle. Missing revision stamps remain disclosed as revision=unavailable, not proof of revision equality.

Client projection completion is guarded by request and live-session generation, including reset and same-session request reordering. Persisted interaction acknowledgements, committed-map changes and receipt changes invalidate the status view through one coalesced, single-flight refresh with a trailing update; there is no periodic polling. Undo recording verifies the original receipt session after module loading and keeps at most 128 dedup identities per session.

Selection recovery preserves the emitting view's resolved `id_field` through the bounded IR payload, snapshot and claimed scope, so the existing selection-store can rebuild the real map filter. No identity field is guessed for legacy records. An empty IR ledger may use pre-turn evidence; an explicit current clear or replacement takes precedence. Only box/lasso affordance geometry can supply an analysis AOI; measurement/highlight geometry cannot. The latest AOI deletion remains effective beyond the recent-interaction display window. Temporal mutation completion, including failure, checks the original session and remembers a detach across A→B→A switches before emitting IR or applying a response.
