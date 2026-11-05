# E0 事实审计 — 12 组多轮操作事件流追踪计划

> 目标：逐操作回答「哪个事件进后端、哪个只停留前端、Agent 下一轮看到什么、
> 刷新/重连/换会话后什么丢失」。产出 characterization 测试（先钉现状，
> 期望断言随实现轮引入）。

| # | 操作序列 | 追踪面（IR kind / situation 分区 / store） | 预期断言方向（待事实核实） |
|---|---|---|---|
| 1 | 点选学校 → 下一轮问「统计一下」 | feature_selected → interaction.selected_feature → 投影 `[GIS 情境]` | 选择是否进 situation？agent 能否拿到 dataset_ref+feature_id 做统计？ |
| 2 | 框选区域 → 「这一区域有多少学校」 | feature_selected(selected_ids) / aoi_drawn → scope | AOI 几何本体留在客户端（digest only）——分析如何取到几何？（缺口的候选） |
| 3 | 隐藏图层 → 恢复 | layer_visibility_changed(mutation-linked, user) + user_hidden_layers | mutation_verified 交叉校验 + user-wins 投影 |
| 4 | 修改样式（透明度/配色） | layer_opacity_changed / layer_style_changed | mutation 结局 + attempted_not_applied 披露 |
| 5 | 撤销/重做 | workbench undo（inverse replay）+ MapSpec revision | undo 是否走同通道 CAS；会话切换清栈 |
| 6 | 变更地区（换研究区） | viewport_settled + scope_name + MapSpec 换层 | 旧 selection 是否被筛掉（scope 失效） |
| 7 | 再次分析（同选择换参数） | selection 持续性 + analysis.plan_progress | 选择跨轮存活与 revision 一致性 |
| 8 | 新会话恢复（刷新/重连） | outbox 水印 + ledger + situation snapshot | 选择/地图态恢复路径；localStorage 非权威 |
| 9 | 图表刷选 → 地图联动 | selection-store(chart 源) + chart-map-link | 刷选是否上报 IR？（缺口的候选：chart_brush） |
| 10 | 属性表定位 | table-panel → map locate | 是否有事件/仅前端 |
| 11 | 时间窗调整 | temporal（MapSpec time 契约 + timeline UI） | scrub 是否 settle 上报（ADR-0227 曾以「无 UI」拒收，#1877 后是否有钩子） |
| 12 | 敏感编辑（框选→改属性→确认/撤销） | feature_edits 投影 + EditProposalCard 确认门 | 确认门复用 + undo 链 |

每行产出：现状（implemented/partial/missing）+ 证据（file:line）+ 缺口结论。
