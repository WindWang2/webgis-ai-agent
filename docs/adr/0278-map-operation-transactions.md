# ADR-0278：地图表现层操作事务与最终显示确认 human seam（Track A）

状态：Proposed（随 zcode/gis-harness-map-operations-vnext PR）
日期：2026-10-31 · 基线 master 4b3629c90

## 背景

Agent 对「修改当前地图」的多项请求此前只能逐工具调用（N 次独立 dispatch、
N 个 step_result），无统一预览/回执/幂等键；混合 intent 无原子包
（`apply_gis_mutation_batch` 仅 PatchLayerPresentation）。
`FinalDisplayConfirmation`（ADR-0134 D6）只有后端判定 seam ——
`record_display_ack` 无任何路由/前端调用方，`GIS_FINAL_DISPLAY_CONFIRM=
required` 模式永远无法 confirmed。指代解析（「这个图层/上一个结果/保留
其余」）无正式通道（`resolve_layer_ref` 是首匹配语义，无歧义披露）。

## 决策

### D1 组合层而非平行核心
`app/services/gis_harness/map_operations/` 是对既有 mutation 门面的组合层：
presentation 组（visible/opacity）聚合走 `apply_gis_mutation_batch`（单事务
单 revision）；其余 intent（paint/symbol/move/viewport/component/remove）顺序
走 `apply_gis_mutation`，每步以链上 revision 作 CAS 基线。**不虚构强原子**：
任何一步 superseded 即停链，剩余效果 `chain_aborted` 披露；MapSpec 仍是
唯一 desired state，无第二状态仓库。

### D2 操作级幂等
`operation_id`（UUID）持久于 map_state `_map_operation_receipts` 有界环
（16，最新在前）；重放返回存证回执（`replayed:true`）且零新 revision。
底层 `mutation_id = op:<operation_id>:<slot>` 引擎级 dedup 兜底。

### D3 局部成功披露、不自动补偿
表现层效果彼此独立（无损坏性中间态），失败/被抢占不自动回滚 —— 补偿写
会与 user-wins 竞争（回滚本身是一次新 mutation，可能覆盖并发用户决策）。
回执逐效果披露 `applied/skipped/failed/planned + reason_code`，由下一轮
agent/用户决策。GISAction executor 的补偿链保持未接线（独立演进面）。

### D4 敏感人环
`remove_layer` 是敏感效果：agent 工具 commit 只产 pending 回执
（`confirmation_required` + 效果载荷），owner 经
`POST /chat/sessions/{sid}/map-operations/{operation_id}/commit` 批准后以
origin=user 落盘（引擎对 user origin 强制 expected_revision；批准时现读
revision，不信任预览时旧值）。镜像 visual-repairs plan→approve 先例。

### D5 display-ack 新鲜度门（fail-closed）
`POST /display-confirmation/ack` 的 `render_seq` 必须：
1. == 当前最新观察代次（`_cartographic_observation.sequence`）—— 用户
   确认的是此刻看到的渲染；2. ≥ 存储裁决的 `render_observation_seq`。
通过后写 ack（幂等）并 force finalizer 重跑 → `task_complete` 披露刷新。
旧代次 ack / 无裁决 / auto 模式均明确拒绝（409 typed）。ack 可信度来自
require_owned_session 的 owner 身份，与客户端可写 map_state 身份字段无关。

### D6 required-pending typed 披露
`read_stored_map_product` 在 required 模式追加 `display_confirmation`
块（`{mode, status: confirmed|pending, needs_user_action, required_render_seq}`），
auto 模式保持旧键形状零漂移。「地图已裁决 ready」≠「用户已看到/认可」。

### D7 指代解析歧义纪律
resolver 输入全部来自权威只读投影（MapSpec 层清单 / IR 投影 selection
与 recent_interactions / provenance 环尾 / 回执环）。名称子串多命中 →
clarification 候选（≤6，id/名称/来源），**不写任何状态**；
`focused_layer`（IR selection）、`last_result`（回执环最近 applied →
provenance 兜底）。「保留其余图层」= 只改列出的 target，回执披露
`untouched_layer_count`。

### D8 前端布线（冲突最小）
回执经 `commands:[{command:"map_operation_receipt"}]`（catalogue 新条目
+新 slice 文件）→ receipt-bus 单例（会话守卫/有界 4/幂等去重）→
MapHarnessCards。显示确认卡自水合：挂载 5s 兜底 + MapSpec live
generation 变化 debounce 2s 复查（不抢首批观察窗口 —— cartography-race
测试对 fetch 计数有精确断言）；auto 模式永不显示。不触碰
use-sse-stream/adapters/message-reducer（PR #1873 占用面）。

## 后果

- 正面：F-A1（多动作单回执）/F-A2（user-wins+superseded）/F-A3（required
  确认闭环）/F-A4（跨刷新恢复）/F-A5（跨会话 fail-closed）全部有自动化
  证据；Pi 与 legacy 共享同一工具面（ToolDispatchService 单点）。
- 代价：混合 intent 的多 revision 前进（每 intent 一个事务）—— 与既有
  引擎语义一致，回执如实披露 revision 链。
- 已知限制：`set_layer_paint` 持久面仅透明度族 9 键（尺寸/颜色经
  `set_layer_symbol` 白名单读改写 UpsertLayer）；图层 filter 不在效果
  词表（MapSpec 编译器 filter 输出是 issue #1809 的独立修复面）；
  display-ack 拒绝语义（用户说「显示不对」）走对话而非 typed 通道。
