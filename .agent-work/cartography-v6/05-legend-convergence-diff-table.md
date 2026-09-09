# Legend 推导收敛 —— 六点语义 diff 表（ADR-0120 W5，R1-M7 交付物）

单源：前端 `frontend/lib/map-kit/legend-model.ts` deriveLegendModel；
后端镜像 `app/lib/cartography/render_scene.py` derive_legend_items。
验收标准：**逐调用点行为等价**（有意 delta 逐条登记 + 闸更新）。

| 调用点 | 收敛前语义 | 收敛后 | delta 判定 |
|---|---|---|---|
| render-scene.ts legendEntryCount/legendTitle（oracle） | 独立计数：categorical 原始长度；graduated cap；continuous(min/max)=3；nodata +1 | entryCount=model.entries.length；标题兜底 deriveLegendTitle（零条目仍保留字段标题） | 等价（W4 fixtures 全部保持绿）；delta：仅 nodata 条目时 hasNodata 语义一致 |
| vector-svg-export legendItemsOf | cap+labels、#888 兜底、bivariate→null、`_fmtNum`（1e3 阈值 k / toFixed(1)） | model 直投 | **有意 delta**：区间标签改 formatLegendValue（10k 阈值 + zh-CN 整数分组，与 live 同源）—— 同一数值 live 与导出标签自此一致 |
| export-chrome drawChromeLegend（canvas） | graduated **无 cap、忽略 labels**、独立 fmt、标题回退"未知字段" | model 条目 + formatLegendValue + 标题回退"图例" | **有意 delta**（修正确性缺陷）：用户 labels 现在进 canvas 导出；超 palette 条目不再绘制（与 vector/live 一致）；无字段 spec 标题"图例" |
| export-chrome entryCount（>8 披露计算） | categorical/graduated 专用计数（continuous=0） | model.entries.length | **有意 delta**：continuous 现计 3（<8 不触发披露，行为无感）；categorical/graduated 等价 |
| live legends.tsx legendEntries（DOM） | graduated 无 max(0) 防负；categorical **丢弃**无色条目；legacy entries[] 无过滤；continuous/bivariate→[] | model 委托；continuous/divergent/bivariate 保留 []（专用组件承接） | **有意 delta**：无色 categorical 条目以 #888 兜底（不静默丢数据）；graduated 负防护；slice(0,8) 呈现语义不变 |
| compiler.ts extractLegendForLayer（html 模板 LegendDef） | paint 符号族推导（point/line/polygon/gradient），**无 legend_spec 输入** | **不收敛**（登记为独立遗留通道：html 模板的图形符号图例，输入是 paint 不是 legend_spec，语义不可合并） | 保留（diff 表登记，非第二图例事实源） |

闸更新清单：
- `export-chrome.test.ts` "breaks 生成区间标签"：0.0 – 10.0 → 0 – 10（formatter 收敛）。
- `render-scene.parity.test.ts` + 后端 `test_render_scene_parity.py`：W4 fixtures 全绿（oracle 等价证明）。
- 新增 `legend_model` golden corpus（4 文件）+ 双侧 parity 测试（pytest 13 / vitest 9）。
