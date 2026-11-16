# PLAN — G46 多图联合筛选与持久探索视图

## 架构总览

```
作者 UI (ExplorationPanel + exploration_chart_panel 组件实例)
   │ 发布/移除/清空条件（per-chart 控件身份）
   ▼
ConditionStore（前端，多条件并存；与 SelectionStore 分离）
   │ 组合 where AST（跨维度 and + 同维度 or/in）
   ├─→ 服务端 /linked-exploration/preview（freeze_source 同代快照 → evaluate_where 组合求值
   │     → 匹配计数 + per-chart facet 统计 + where-scope selection handle）
   ├─→ 地图 live filter（where AST → MapLibre 表达式精确编译，typed identity）
   ├─→ 结果表（/features?where=组合 服务端分页 + total_matching）
   └─→ 保存命名视图（exploration_views 表，G38 CAS 模式，源锚 ref+revision+fingerprint）
重开视图：按锚 check_anchor → stale 则明确要求重预览；apply 原子写 chart 组件进 MapSpec
导出：冻结 active plan → MapSpec 副本受影响层 filter AND 组合表达式 → print-layout compile 三格式
Pi：webgis_exploration_inspect/preview/apply/view_save/view_open（同一 service）
```

## 工作包 → 实现顺序

### M1 后端领域服务（WP01-05）
1. `app/services/linked_exploration/contract.py`：ExplorationChartSpec（kind=category|range；field；agg）、ExplorationCondition（condition_id、chart_id、field、typed values / 区间端点）、组合编译 `compile_combined_where(conditions)`（每维度：category→in(+null eq) / range→and(gte,lte)；维度间 and）、plan digest、MapLibre 表达式编译 `compile_maplibre_filter(where)`（eq/ne null 语义对齐）。
2. `app/services/linked_exploration/snapshot.py`：薄 re-export `freeze_source`/`check_anchor`/`SourceAnchor`（from bivariate snapshot —— 不复制）。
3. `app/services/linked_exploration/service.py`：
   - `inspect_source(session_id, ref, ...)`：冻结 → 字段 schema → 类别候选（kept/other typed 值集，帽）+ 数值字段 min/max/分布建议
   - `build_chart_data(...)`：分类条形图（count/sum/mean/min/max per 类别桶 + other_values 有界）与数值区间直方图（固定桶数、闭开规则 [a,b) 末桶闭、null/非数值计数）
   - `preview_conditions(...)`：组合 where → 完整匹配计数 + 每图 facet 统计（自排除自身条件口径 + 全条件匹配集口径，两套）+ where-scope selection handle
   - 视图 CRUD：save/list/open/delete/rename + CAS + 锚校验
4. `app/models/linked_exploration.py` + `migrations/versions/g46_linked_exploration_views.py`（挂 g40_cartographic_symbol_assets 后）
5. `app/api/routes/linked_exploration.py`：REST（require_owned_session）
6. `app/tools/linked_exploration_tools.py`：Pi 工具（registry.register + 有界回执 + reconcile_via）
7. mapspec_filter.py eq/ne null 语义修正（3 行 + 测试，MapLibre 一致）

### M2 前端（WP06-07）
1. `frontend/lib/linked-exploration/contracts.ts`（与后端 contract 同构）+ `api.ts`
2. `frontend/lib/linked-exploration/condition-store.ts`（外部 store：charts 注册、条件增删、组合 where 编译、MapLibre 表达式编译、generation epoch）
3. `frontend/components/linked-exploration/exploration-panel.tsx`（G38 挂载模式；源绑定/添加图表/条件 chips/保存切换/恢复/结果表内嵌分页）
4. `exploration_chart_panel` 组件类型（component_registry additive → 重跑 export → parity；渲染器复用 ChartCore；点击类别/桶发布条件）
5. map-panel 接线：组合 filter AND 进 selectionFilters 面（不动 runtime）
6. i18n linkedExploration.json zh/en + messages.ts 登记

### M3 持久视图闭环（WP08）
- apply 原子性：chart 组件批量 patch + 失败回滚（mapspec expected_revision CAS）
- 服务重启恢复：视图从 DB 读；跨会话打开同项目视图

### M4 导出（WP09）
- 冻结 active plan + 源版本 + 匹配集合统计 → MapSpec 副本 filter 注入 → print-layout compile 链三格式 + manifest（plan digest、匹配数、facet 口径说明）
- 表格/结果下载走 data_delivery 既有入口

### M5 竞态/预算（WP11）
- 前端：generation epoch + AbortController；条件去抖合并；迟到响应拦截
- 后端：扫描帽（复用 AGG_SCAN_LIMIT 语义）、桶帽、条件数/深度帽（FILTER_MAX_*）、导出冻结期再校验

### M6 验证（WP12）
- 单元：contract（typed identity E06/E07/E08/E12、组合 E03）、服务（facet 分母 E11、other 桶 E13、快照 E21/E22）、oracle（独立手算 fixture）
- 集成：REST 授权/CAS/失效（E19/E20/E25/E27）、导出回读（E30）
- Pi journey：真实子进程 + 扩展 + dispatch（E32）
- 前端：condition-store、panel 组件测、map filter 合成、chart 组件多选
- e2e journey j24（键盘 E33、回归 E34）

## 完成门
E01-E34 全部有真实证据；两图一地图分类+数值联合旅程 + 命名持久视图 + 真实 Pi 调用 + SVG/PNG/PDF 同范围成果。
