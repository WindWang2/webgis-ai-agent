# DEDUP_MATRIX — 全链查重证据（三路审查汇总，master 59eb3f527）

## 已存在（不重建）
| 能力 | 状态 | 证据 |
|---|---|---|
| chart_panel 浮动面板（recharts，pie/donut/stacked_bar/grouped_bar/rose/radar 18 类） | 已实现 | frontend/components/map/map-components/chart-panel.tsx:488；lib/chart-adapter.ts:5-10 |
| 单字段比例符号（circle-radius interpolate，linear/area_sqrt） | 已实现 | app/services/thematic_authoring/builder.py:138-160,425-434；frontend thematic-authoring-panel.tsx:75-78 |
| 导出 canvas 面板图绘制（含饼弧/堆叠条） | 已实现 | lib/map-kit/export-chrome.ts:1994-2200 drawChromeChartPanel |
| Dorling 卡通图（示意坐标，非地理锚定） | G47 已完成 | planning/G47-DORLING-CARTOGRAM（所有 WP ✅） |
| OD flow（G21）、多尺度综合（#2274）、设计系列（#2278） | 已合并 | git log origin/master |
| MapSpec→MapLibre headless 编译 + live diff/patch | 已实现 | mapspec-compiler/compiler.ts:508；mapspec-runtime/runtime.ts:110,602 |
| 标签引擎/障碍/碰撞（前后端 twin） | 已实现 | label_engine.py / label_collision.py / mapspec-compiler/label-solver.ts |
| PublicationIR + SVG twin + WeasyPrint PDF + 单帧 PNG | 已实现 | mapspec-to-svg.ts；publication-export.ts；exporter.ts:1850 |
| 组件注册表 + ABI + 7 槽 chrome + CAS component patch | 已实现 | map-components/registry.ts；resolve-components.ts；component-mutation.ts |
| 字段合同/聚合表（webgis_field_contract_*、webgis_layer_aggregate） | 已实现 | app/services/field_contracts/；feature_aggregate.py:76-110 |
| CAS/revision/undo/checkpoint/单笔 CAS apply 模式 | 已实现 | gis_world_state/mutation.py:376-546；mapspec/agent_undo.py；G47 同款 |

## 确认缺失（本线目标，正是任务书核心增量）
| 缺口 | 证据（缺失） |
|---|---|
| **地图上 feature 锚定的饼/环形图符** | MapSpecLayer.type 枚举无 chart 类型（types.generated.ts:208）；GL paint 路径无 pie；全库 grep 无 per-feature pie glyph |
| **地图上 feature 锚定的多序列条形/堆叠条形图符** | 同上，仅 circle-radius 单值；无 GL custom layer for charts |
| **多变量分段比例符号（同 geo anchor 多统计量）** | 不存在 |
| per-feature DOM overlay 跟随 pan/zoom（仅 debounce annotation + 瞬态 Popup） | annotation.tsx:83-100（debounce）；map-panel.tsx:1740（移动即关） |
| SVG 导出 twin 中的图符绘制 | mapspec-to-svg.ts 仅 layers/labels；svg-marginalia.ts 仅 legend/north/scale |
| 共享图符几何/path 工具（饼弧、堆叠条 rect） | 仅 export-chrome canvas 内联弧 + cartogram geometry.ts，无可复用模块 |

## 结论
任务书 E1 判断与代码实况一致：浮动面板≠图符地图；单字段比例圆≠复合统计图符。本线新增 = MapSpec additive 组件/图层合同 + GL 2D custom layer live 渲染 + 共享几何 oracle + SVG/PDF/导出同源 + Agent/UI 双入口。G47 的「数量语义→资格→几何 descriptor→单笔 CAS→出版 adapter」架构顺序直接可借。
