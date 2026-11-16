# G46-LINKED-EXPLORATION BASELINE

- 执行时间：2026-11-15T15:47Z 起
- base SHA：`655f769ca17f17c5797da13b4c299b8fa600e370`（origin/master，G40 #2124 已合并）
- branch：`zcode/g46-linked-exploration-20261115T154748Z`
- worktree：`../webgis-worktrees/g46-linked-exploration-20261115T154748Z`
- 研究快照基线 `34f4e3670` 已过期：G40 #2124 已合入 master（迁移 head 现为 `g40_cartographic_symbol_assets`）。
- Open PR：#2125（G47 Dorling cartogram，兄弟任务，无文件重叠预计：本域不碰 cartogram_authoring/print_layout_schema；可能小重叠 hud/layer-style-panel.tsx 与 i18n hud.json —— 合流时处理）。
- Open issue：#2123（master CI 基线失败报告 —— 既有问题，不是本任务实测；本地跑本域测试为准，不把全仓清债当目标）。
- 主工作区有用户未提交内容（.claude/skills/*、vendor/pi 修改）——不动主工作区。

## 已合并可直接复用的权威（追到生产调用链）

| 能力 | 位置 | 复用方式 |
|---|---|---|
| typed where AST（闭合算子集 + 深度/长度帽 + 求值） | app/services/feature_pages.py L295-477 | 直接 import `_validate_ast`/`evaluate_where`/`where_digest`；组合 = and(terms)+每维度 or/in 叶 |
| 同代原子冻结（强版本双查 + session_lock + 深拷贝） | app/services/bivariate_reliability_authoring/snapshot.py L139-222 | 直接 import `freeze_source`/`check_anchor`/`SourceAnchor` |
| where-scope 选择句柄（服务端权威完整集合） | app/services/selection_handles.py（create_selection L323） | 组合条件 → where scope handle；地图/表格/导出消费 |
| 聚合（count/sum/mean/min/max + 扫描/组数帽） | app/services/feature_aggregate.py L76-191 | facet 统计复用语义；本域分桶自带 typed kept/other 值集（不改共享文件） |
| chart_panel 多实例（cardinality=multiple） | app/lib/cartography/component_registry.py L465-490 | 本域新组件类型 `exploration_chart_panel`（additive 注册） |
| MapSpec 组件写入通道 | mapspec_store.patch_component(component_id, upsert=True) | 图表实例写入 committed spec（视图 apply 原子生效） |
| MapLibre filter 子集（前端+SVG 导出共享） | app/services/mapspec_filter.py | 组合 where → MapLibre 表达式编译器；eq/ne null 语义修正为 MapLibre 一致（本域 adapter 工作） |
| SVG 导出 layer.filter + chart_panel 渲染 | app/services/mapspec_to_svg.py L1962-1983, L941-942 | 导出图面精确反映组合条件；图表统计印进导出 |
| 三格式出版 | app/api/routes/print_layout.py /export（pdf|svg|png） | 本域导出经冻结 MapSpec 副本 + print-layout compile 或同链 |
| 持久化 + CAS 模式 | app/models/data_join_authoring.py + migrations/versions/0107 | exploration_views 表照抄模式 |
| Pi 工具注册 | app/tools/__init__.py `_TOOL_MODULES` + registry.register | 新模块 linked_exploration_tools.py |
| G38 面板挂载模式 | frontend/app/page.tsx（dynamic import + 开关 store + 命令面板） | ExplorationPanel 同模式 |
| paged-features where 下推 | frontend/lib/data-plane/paged-features.ts（FilterAst） | 结果表复用 |

## 本域不做（所有权边界）

- 不替换 SelectionStore（G24 权威）——新条件 store 与 hover/焦点/选中分离
- 不建第二查询引擎/聚合库——求值走 evaluate_where
- 不碰 G40 symbol/texture、G44 字段合同、G45 坐标、G47 cartogram、G48 配准
- 不做跨源 join/位置关联（G38 所有权）
- 不修全仓 CI 基线（issue #2123）
