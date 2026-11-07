# ADR-0287：情境工作台范围披露与诚实终验闭环（SW-02/SW-04/SW-05/SW-08/SW-09）

- 状态：Accepted（G05-SITUATED-WORKBENCH）
- 日期：2026-11-08
- 关联：ADR-0227（Interaction IR）、ADR-0278（map_operations 事务）、
  ADR-0285（情境交互闭环）、Issue #2004、#1974（前置）

## Context

G05 审计（planning/G05-SITUATED-WORKBENCH/BASELINE.md）发现"地图—对话情境
一致性"的核心断裂不在架构而在**上行有界化吞掉事实**与**终验静默**：

1. **#2004 族（P0）**：框选 23 要素时 UI 如实反馈"已框选 23 个"（本地
   selection-store 持 50 ids + matched_count），但 IR 上行在前后端双侧被
   静默截到 8（`bus.ts .slice(0,8)` + `contracts.py value[:8]`），且
   `matched_count` 不在白名单——Agent 只见 8 个 id、无总数，"这些学校"
   的分析范围被静默缩小。无稳定 id 字段的图层框选则**零上行**（模型完全
   失明）。图表类别上行同样截 8（与 selection-store ≤20 失配）。
2. **SW-04（P1）**：map-op 回执 `invertible` 只覆盖显隐/透明度——Agent
   改符号尺寸/持久 paint 后用户无法 Ctrl+Z（尽管 before/after 事实已在
   回执里）。
3. **SW-09（P1）**：终验四层（工具 success / MapSpec 接受 / 渲染 ACK /
   用户确认）结构可区分，但 display-confirmation 默认 auto 模式下 UI 完全
   静默——「渲染已发生」被无声地当作闭环，无任何"自动确认（未请求用户）"
   披露。
4. **SW-05/SW-08（P2）**：可解释执行面板手动刷新；图表类别选择无键盘路径。

## Decision

### D1 选择范围披露契约（interaction_ir.v1 additive）

- `feature_selected` / `table_selection_changed` 白名单 +
  `matched_count`（真实命中数，有界整数 ≤10^7）与 `ids_truncated`
  （**服务端权威派生**——以归一化前原始 id 数对比上限，客户端申报一律
  覆写；仅当 selected_ids 实际保留于载荷时派生，防幽灵标记）。
- `selected_ids` 专属元素上限 **50**，三方对齐：前端 selection-store
  `MAX_SELECTED_IDS` = IR `MAX_SELECTED_IDS_PAYLOAD` = analysis_scope
  `MAX_SCOPE_FEATURE_IDS`（manifest 双闸锁漂移）。`categories` 对齐
  selection-store ≤20。通用 list ≤8 纪律不变（per-key 覆写，不放松面）。
- 选择 kind（含 chart_selection_changed）载荷字节预算 4096B（50×32 字符
  id 集 ≈1.9KB；512B 会把"截断"恶化成"整事件拒绝"）。通用 512B 不变。
- `feature_selected` 白名单 + `bbox`（4 元数值；消费侧 `_bbox4` 数值
  校验）——框选区域作为空间范围可指代。
- 投影/声称/验证/史代四层贯通：`_selection_view` 透传披露键；
  `ClaimedScope`/`ValidatedScope` 携带 matched_count+ids_truncated
  （`truncated_sample` 进 `checks.features`）；selection_history
  MAX_HISTORY_IDS 8→50 + 史代披露字段（previous 句柄披露同权；部分取消
  是用户决策、摄入截断是静默截断——两者以摄入层披露区分，不用计数推断）。
- 无 id 图层框选：count-only `feature_selected`（layer_id+matched_count
  [+bbox]）上行——"模型失明"比"无 id 集是空集"更不诚实。

### D2 map-op 可逆覆盖面（map_operation.v1 additive）

`invertible` 扩展至 `set_layer_paint`/`set_layer_symbol`：before 全数值
在场即可反演（MapLibre paint 键数值对经 `patch_layer_style` 用户通道
反向提交）；before 缺值（无从恢复"原本缺失"语义）诚实回落不可逆。前端
`styleCommand`（undo/redo 经 `commitLayerStyleAndCommit`）与回执录制
扩展；缺值不录。

### D3 终验收口披露行（UI additive，零后端行为变化）

`FinalAcceptanceLine`（被动无交互）：任何确认模式下，渲染观察代次 +
用户确认态必须可见——auto 模式明确披露"自动确认（auto 模式，未请求用户
确认）"，required ack 后呈现"用户已确认显示"，无裁决时"暂无裁决"。
四层终态各归其位：工具执行/MapSpec 接受由 ReceiptCard 承载（status/
revision/effects），渲染观察/用户确认由本行承载——不聚合出第二真相。
auto 模式的代次复查由 MapSpec live generation 事件驱动（非轮询）。

### D4 面板实时性与键盘可达（UI additive）

- AgentRunPanel 订阅 `projection-invalidations`（interaction ack/投影
  失效同源广播），500ms 防抖重拉 analysis-graph；会话身份复核。
- 图表类别键盘选择：原生 `<select>`（Tab/方向键/Enter 全等价）与点击
  同链（publishSelection + IR 上行）；空值项=清除联动；选择能力缺席
  （无 bound layer/非 ready）不渲染——不虚构入口。

## Consequences

- **兼容**：全部 additive（新白名单键/新常量/新 UI 元素）；旧事件
  （无披露键）按缺席语义处理（单选/旧多选不变）；manifest 双闸同步。
- **预算**：单事件最坏 4KB（选择 kind）；ledger 环 96KB 预算不变
  （最坏 24 条满载事件 + dropped 披露）。
- **不做**：不改 display-confirmation 默认 auto（部署级 env 决策，本 ADR
  只保证 auto 下诚实披露）；不建视口史代（"上一张图"留待后续）；redo
  仍限命令级（workbench redoStack），map-op 服务端无 redo 语义。
- **Oracle**：选 20 → Agent 见 20（或 50 样本 + matched_count 披露）；
  框选区域可指代；Agent 改符号可 Ctrl+Z；终验静默消除；金标语料
  （box_select_20_range_faithful 等 4 条）钉扎回放确定性。
