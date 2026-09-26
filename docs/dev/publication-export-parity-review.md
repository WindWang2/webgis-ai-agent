# F14 — 独立 Review 报告与清偿（Subagent C，2026-09-26）

Review 对象：`origin/master(9e1ad229)...HEAD`，11 commits（docs→WP5）。
方法：只读代码审查 + 实证探针 + 串行测试（9 组套件全绿）。
**Verdict：NEEDS_FIXES** → 本文档逐条记录清偿/处置；P0×1、P1×6 全部修复，
P2×8 中 6 项修复、1 项文档化、1 项经证据复核不成立（详见 P2-6）。

## P0（必须修）—— 已全部修复

### P0-1 单点 series 的 chart_panel → 整页空白出版图 + 零诊断 ✅
双层修复：
1. `svg_charts._render_line_family`：`len(vs) < 2` 的序列画孤立 marker，
   不再进入 `i/(len-1)` 除零路径（`svg_charts.py` review 标注 P0-1）；
2. `mapspec_to_svg._render_chrome_groups` 装配循环逐组件 try/except →
   `component_skipped_invalid` + omitted 回执 —— 单组件缺陷的降级粒度
   收敛到该组件，绝不放大为整页编译失败。
回归：`test_review_fixes_f14.py::test_p0_single_point_series_keeps_page`
（单点序列 + 3 点序列混合 → 全 chrome 在位 + 图表面板在位）。

## P1（必须修）—— 已全部修复

### P1-1 anchor stack 无画布越界钳制 ✅（附坐标系统勘误）
左上栈 `_tl_fits`（下界 canvas_h - m - 40）与右下栈 `_br_fits`（上界
m + 40）容量守卫：越界面板**不画**并落 `_record_omitted(publication_
component_omitted, 版面容量不足)` + `publication_layout_truncated` 披露。
**勘误**：reviewer 探针的「y=1178 超出 400 画布 2.9 倍」不成立 —— chrome
装配在 dpi 缩放坐标系（`scaled_height = 400 × 300/72 ≈ 1667`），y=1178 在
viewBox 内；真实越界需要 >10 个面板。守卫按缩放坐标系一致实现并在 20 面板
探针下验证触发（10 渲染 / 10 省略 / 10 披露 / max y 1474 < 1667）。

### P1-2 图表截断回执双重失真 ✅
`_parse_chart` 返回截断前原始合法点数（`raw_count`）；点级截断按原始数计
（"chart points 70→64"）；条级截断只属柱/条族且用独立文案
（"chart categories 15→12"）；line 族不再误套 MAX_BARS。装配层消费
`render_chart_panel` 返回的**注记清单**逐条如实入披露（恒定 detail 删除）。
附带修正（review 未发现的同根缺陷）：bar 族此前把 series 当类目行 —— 单序列
N 点被画成 1 类目 N 序列、grouped_bar 未按类目转置；现按视角拆分
（`series_rows` / `category_rows`），类目行转置后 MAX_BARS/标签语义正确。

### P1-3 多 colorbar 重复 gradient id ✅
`render_colorbar` 增 `gradient_id` 参数，装配按序号派发
`chrome-cb-grad-{n}`；回归断言两色带 id 互异且各自 `url(#id)` 恰一次。

### P1-4 annotation callout/静态卡二选一 ✅
镜像 canvas 语义：callout 投影成功 → 只画 callout（不再追加静态卡）；
投影失败且有静态文本 → 降级静态卡（只画一次）；投影失败且无文本 →
`component_skipped_invalid` 回执。回归：
`test_p1_callout_success_skips_static_card`（chrome-annotation 组恰 1）。

### P1-5 幂等门第四键缺验证前捕获守卫 ✅
与 rows 漂移守卫完全同构：验证前捕获 `validated_product_fp`，锁内比对
fresh，偏离 → `product state changed mid-run — persist skipped`（不落块，
留给下一触发点重验）。回归：接线源级断言（守卫先于 `save_session_plan`）
+ 条件语义断言（mid-run receipt 落章必改指纹）。

### P1-6 collapsed 面板在 publication 链画全量 ✅
装配层读 `comp.collapsed`（ResolvedComponent 既有字段）：置位 →
`_render_collapsed_bar`（标题条 + 「已折叠」尾注，30px），统计/图表/表格/
披露/注记族全覆盖 —— canvas E-2 同语义（live 折叠、导出亦折叠）。
回归：`test_p1_collapsed_panel_renders_folded_bar`（无正文行泄露）。

## P2（建议修）—— 6 修复 / 1 文档化 / 1 不成立

| 项 | 处置 |
|---|---|
| P2-1 map_border `<g class="chrome-map-border">` 包装使「既有 10 族逐字节不变」表述不严 | 文档化：wrapper 为 corpus marker 契约的一部分（map_border 唯一无 class 族），设计文档已明示；其余 9 族输出表达式与 master 逐一相同，无消费方 pin 旧字节 |
| P2-2 catch-all 对 basemap/export_layout/label_layer 发噪音 | ✅ 豁免三非 chrome 型（`test_p2_non_chrome_types_exempt_from_catch_all`） |
| P2-3 多层截断无尾注 + 16/24 常量三处重复 | ✅ omitted 溢出发 `diagnostics_truncated` meta（"component omissions N→16"）；上界常量归一到 `export_lineage._MAX_COVERAGE_*` 单源 |
| P2-4 `svg text {font-family}` 规则无声改变无内嵌路径的字体解析面 | ✅ 规则只在 `@font-face` 注入时输出（`test_p2_svg_text_rule_only_with_embedded_font`） |
| P2-5 `.diagnostics.json` sidecar 永不 GC | ✅ sweep 与 `.owner` 同纪律：随主件删除、孤儿超龄清除（`test_p2_sidecar_gc_orphan_and_with_primary`） |
| P2-6 vertical colorbar hi/lo 标签倒置（latent） | **复核不成立**：`x1=0,y1=0,x2=0,y2=1`（objectBoundingBox）首色在顶，hi 标签 `ry+8` 亦在顶 —— 方向一致；publication 链当前仅 horizontal 路径。结论与证据记录于本文件，不改代码 |
| P2-7 表格高度估算差致栈重叠 / `_clamp_canvas_dpi(0)` 记 72 / atlas 未传 dpi | ✅ `_table_height` 单点（渲染/装配同表）；`0 → 未记录`；atlas 三调用点透传 `ctx.dpi` |
| P2-8 `_parse_stats_rows` 双写常量 / None item 虚计 / `_parse_chart` 截断前 O(n) | ✅ 常量引 `svg_marginalia.STATS_MAX_ROWS`、截断计数剔除 None item；O(n) 读入受请求体 50MB 上限兜底（接受并披露） |

## Review 后回归（本地，串行）

```
tests/unit/test_review_fixes_f14.py + tests/unit/test_export_semantic_corpus.py → 59 passed
tests/unit/gis_harness/test_product_state_gate.py 等 4 套（reviewer 复核）→ 34 passed
```
清偿后全量邻域（corpus/gates/publication/export/paths）在最终 commit 前重跑，
结果见 PR 描述「测试证据」节。
