# INTERFACES — G46 对外合同

## REST（/api/v1/linked-exploration/*，require_owned_session + session_id query）
- POST /inspect {ref} → {anchor, feature_count, fields[{name,type,category_sample,min,max,is_sample}], sample_note}
- POST /preview {ref, charts[], conditions[], create_handle} →
  {anchor, conditions_digest, combined_where, match_count, match_count_exact, total,
   map_filter, charts[{facet{statistics}, full{statistics}}], selection_handle?}
- GET /views → {views[]}（不含 plan）
- POST /views {title, plan} → view 行（保存时重冻核对锚）
- GET/PATCH/DELETE /views/{id}（PATCH 带 expected_revision CAS）
- POST /views/{id}/open → {state: ready|stale, plan, preview?, stale_reason?}
- POST /views/{id}/apply {expected_revision?} → {applied_components[], count}
- POST /export {fmt: svg|png|pdf, dpi, view_id?|plan, title?} → refs-only 回执

## Pi 工具（registry；side_effect 如实声明）
- webgis_exploration_inspect（cacheable_read）
- webgis_exploration_preview（cacheable_read；回执有界 top 桶）
- webgis_exploration_view_save（state_mutation；session_state）
- webgis_exploration_views（cacheable_read；无 id 列表/有 id open）
- webgis_exploration_view_update（state_mutation；CAS）
- webgis_exploration_apply（state_mutation；map_mutations=("component",)；reconcile_via=mapspec_event）
- webgis_exploration_export（artifact_creation；result_size_policy=ref_offload）

## 计划合同（schema_version=2）
- ExplorationPlan：{anchor{ref_id,content_revision≥1,content_fingerprint}, charts[], conditions[], base_scope_note}
- ChartSpec：chart_id/kind(category|range)/field/aggregation(count|sum|mean|min|max)/value_field/bins(2..32)/layer_id
- CategoryCondition：values[]（typed scalar ≤64）+ include_null（OR eq-null）
- RangeCondition：gte/gt/lte/lt（各≤1 端，有限数）
- 组合：**条件间 AND；同分类条件内 values OR**；facet=排除自身 chart 条件

## 持久化
- exploration_views 表（迁移 g46_linked_exploration_views ← g40_cartographic_symbol_assets；
  空库 upgrade head 实测通过；id 保留已在 git-common-dir webgis-id-reservation.lock 登记）

## 前端
- condition-store（外部 store）：bind/register/remove chart、set/remove/clearChart/clearAll、
  getExplorationFilterExpression（MapLibre typed 精确）、epoch 世代（session-cursor 接线）
- exploration_chart_panel（MapSpec 组件 cardinality=multiple；catalog/工厂/ComponentType 词表三处登记）
- map-panel：组合过滤 AND 进 selectionFilters（与 transient 选择分立；一次 setFilter 快路径）

## 语义合同（三处同一：evaluate_where / MapLibre live / mapspec_filter 导出）
- in/eq/ne：严格 typed（bool≠数字；1≠"1"；int/float 跨型相等）
- eq null：命中缺席或显式 null（== null；mapspec_filter 已修正对齐 MapLibre）
- between/gte/gt/lte/lt：闭开如名；非数值（含 bool/NaN/Inf/字符串）不命中
- 直方图桶 [a,b) 末桶闭；类别 other 桶 ≤64 溢出值集可筛、超限 other_filterable=false

## 共享文件改动清单（全部 additive）
- app/services/mapspec_filter.py：== null/!= null 语义修正（+测试）
- app/contracts/cartography_components.py：ComponentType +1 词
- app/services/gis_harness/components.py：工厂 +1
- app/lib/cartography/component_registry.py + generated catalog：组件 +1
- app/tools/__init__.py / app/main.py：模块注册各 +1 行
- app/models/__init__.py：模型注册
- frontend map-panel.tsx / page.tsx / messages.ts / commands.json / resolve-components.ts /
  map-components/index.ts：局部接线
