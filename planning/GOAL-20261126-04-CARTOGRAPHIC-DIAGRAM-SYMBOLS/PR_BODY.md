# GOAL-20261126-04｜地理统计图符地图与复合专题制图表达（04 线）

## 用户旅程（本 PR 闭合）
1. **Agent 旅程**：`webgis_map_product` 出图后 → `webgis_diagram_symbol_inspect`（资格事实：源类型/几何/字段）→ `webgis_diagram_symbol_plan`（零突变：资格会计/公共尺度/布局避让证据/图例）→ `webgis_diagram_symbol_apply`（preview 零突变 / commit 单笔 CAS 挂块）。"成都各区学校类别组成和总量"→ composition_pie/donut（总量面积编码可选）；"各区两年公共服务数量并排"→ series_bars grouped/stacked（0 基线正负）；"地区产业结构"→ segmented_proportional（扇区面积∝值，明确非风玫瑰）。
2. **UI 旅程**：图层面板 → 统计图符（新挂载面板）→ 选 kind/类别×字段/尺寸政策 → 计划（展示布局会计+图例预览）→ 应用（与 Agent 同 service 同 CAS 链）。
3. **地图可见**：MapSpec 2.2 additive `layer.diagram_symbol` → live MapLibre CustomLayerInterface（2d）逐帧渲染（pan/zoom/pitch/DPR 自适应；min_zoom LOD；避让位移带引线）；PNG 导出经 map canvas 自动含图符。
4. **单幅发布同源**：后端 SVG twin（oracle 全链）→ WeasyPrint PDF；前端 vector SVG twin 分支（位级同 oracle）；图例走既有 legend_spec categorical 通道（live/出版同源）。

## 架构与文件范围
- 纯库 `app/lib/cartography/diagram_symbols/`（contracts/geometry/qualification/legend/layout：角度/面积/长度语义唯一权威，GEOMETRY_DECIMALS 单点量化，typed 错误面，确定性避让+全量会计）
- MapSpec **2.2 additive** `layer.diagram_symbol`（schema 只承载；版本链/upgrader/ts 投影/前后端漂移闸三侧同步）
- 表达模型 pack（composition_glyph_map / multi_series_glyph_map / segmented_proportional_glyph_map，validate 零错误）
- 服务 `app/services/diagram_symbol_authoring/` + 工具 `webgis_diagram_symbol_{inspect,plan,apply}` + 路由 `/api/v1/diagram-symbols/*`（UI 与 Pi 共 service 共 CAS）
- 前端 `frontend/lib/mapkit-diagram-symbols/`（TS 位级孪生 oracle+layout+GL custom layer+svg-emit；320 例随机差分 parity 与 Python 逐位一致）+ HUD 面板

## 相对最新 PR（#2282/#2283/#2284）的新增量
地图上 feature 锚定的复合统计图符（此前全链查重确认只有浮动 chart_panel 与单字段比例圆）；G47 Dorling（示意坐标纸面图）正交不重叠。

## Pi pin
vendor/pi = ef4a1cfc（上游正式 release v1.6.1，gh release view + ls-remote 双验），本线未改 gitlink。

## 科学/安全/兼容限制（诚实披露）
- 组成比例要求非负+分母显式（负值 typed 拒绝）；条形允许正负（0 基线）；分段比例拒负值
- 极端跨度（max/min>10⁴）typed 拒绝（最小图符不可辨）；要素超帽（>20k）typed 拒绝建议聚合
- vector(MVT) 源无逐要素属性 → typed 拒绝（不是静默跳过）；ref 未水合 → 结构化披露
- polygon 锚点=代表点（非质心——差异显式建模）；位移>阈值带引线
- 前端 TS 孪生的 polygon representative point 为 scanline 实现（GEOS 语义近似；POINT 锚点逐位一致，方型面逐位一致；差异已在代码注释声明）
- 未等待线上 CI（本地验证为准，见下）

## 实际测试（本机实测，flock 错峰）
- 后端：新增 127 用例全绿（图符纯库 54/布局 19/SVG twin 19/authoring 14/性能 2 + schema/corpus 既有面复验）；cartography lane 全量在回归中复跑（基线 5 失败为 master 既有：pin 漂移/carto_ir KeyError/组件 variant 契约，干净 master 临时 worktree 复现归档）
- 前端：图符链 72 vitest（oracle 56+集成 10+svg 6）；caro-ir 175；全量 vitest 5912 passed/5 failed（漂移闸 2 项已修归零；geoai 2+i18n 1 为 master 既有，pristine 复现归档）；typecheck 仅剩 2 项既有基线错误
- ruff（CI 版 0.16.8，CI 逐字命令）：基线 29 项既有 → 本线 0 新增；mypy 基线 1 项既有 → 本线 0 新增
- 性能基线：后端 1k 全链 86ms、5k 候选帽 135ms；前端 1k 帧链（build+layout+三角化）131ms——实测数字入账 planning/TEST_EVIDENCE.md
- 未跑：后端全量 lane（not cartography/perf/real_services 的 ~33k 用例）与 Playwright e2e——本线触碰面在 cartography lane+契约层+导入边界内已覆盖，全量 lane 留给 CI；browser 级真浏览器旅程未自动化（GL custom layer 的 vitest mock 已覆盖绘制管线，真实浏览器帧留给 CI e2e/人工验收）

## 独立审查
- A（制图科学/数值/合同）与 B（安全/同源/资源/性能）独立子代理逐行审查 + C（交互旅程）见 planning/REVIEW.md；P0/P1 修复清单与复审结论随后续 commit 附上。

## 迁移/回滚
- 纯 additive：无迁移；回滚 = revert 分支（无 DB/schema 破坏性变更；MapSpec 2.2 词表后向兼容 2.1 文档）

## 交叉冲突提示
- 共享接线点：app/tools/__init__.py、app/main.py（追加式注册）、frontend version.ts/contract-manifest.ts（版本词表 2.2）、mapspec-runtime/symbol-law（证据面词表+2 事件）、types.generated.ts（再生成产物）——均为薄接线；与 01/02/03 在飞分支无文件重叠（其 planning 目录未在本线触碰）
