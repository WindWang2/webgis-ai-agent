# ADR-0091: Interactive Spatial Workspace Runtime V4

- 状态：Accepted（2026-09-01）
- 关联：ADR-0076（SessionPlan 单一计划真相）、ADR-0081（完成度终验）、
  ADR-0082（Artifact Runtime）、ADR-0085（Goal→ProductGraph）、
  ADR-0086（Render Observation）、ADR-0088（Autonomous GIS Product Runtime）、
  ADR-0089（Raster v3）、ADR-0090（GIS Product Runtime V3 与 Workspace
  交互模型 —— 本 ADR 是其 deferred 项的兑现）
- 分支：`feat/gis-interactive-runtime-v4`

## 1. Context

审计（`.audit/runtime-v4-audit.md`，双路独立 Explore + 主 agent 逐文件
实证）确认：执行侧（ProductGraph / facet completion / runtime repair /
finalizer）在 master 已完整；ADR-0090 deferred 清单全部仍未解决 ——
map↔chart↔table 无 table 宿主、brush/extent_change 零实现、组件无真删除、
selectionField 无自动生成、filter-only 变化全量 recompile（一帧闪烁）、
「过滤命中 0」不可披露。此外 ToolCatalog 无阶段感知（assembly 前门可被
预算挤出）、`map_completion.py` 1830 行单文件、磁盘栅格非一等产物。

## 2. Decision

### 2.1 SelectionContext V2（§8-11）

保留单一选择上下文原则（绝无 mapSelection/chartSelection/tableSelection
三套 store）。V4 增量：

- **谓词描述符**（封闭词表 `bbox | field-in-values`）：框选命中超过 id
  上限（50）时携带**有界谓词** + matched_count，不把数万 id 写进前端状态；
- **id_field**：发布侧解析的稳定要素 id 字段（`FEATURE_ID_KEYS` 链；
  `$id` 哨兵映射 MapLibre `['id']` 表达式）—— map 框选/表格点选/shift
  多选共享同一要素身份，map↔table 的 id 过滤投影双向可编译；
- **epoch guard**：会话切换 bump 世代；brush mousedown→mouseup 跨越切换
  的迟到发布丢弃（ADR-0090 deferred「selection session safety」）；
- **ViewportContext sibling 模块**：相机落定（300ms 二道 debounce + 量化
  指纹去重 + epoch 取消）发布**有界空间上下文**；extent_change 绝不抢占
  选择上下文 —— 视口不是选择。消费分级：cheap 派生（表格视口行过滤，
  WeakMap bbox 记忆）自动跟随；expensive GIS 分析不订阅（属于
  product/action intent 决策）；
- **过滤表达式类型纪律**：字段侧一律 `['to-string', …]` —— MapLibre
  `in` 是严格 indexOf 相等，数字 id（OBJECTID/osm_id 常态）与字符串
  haystack 不归一即零命中（review 双路独立发现的 BLOCKING）。

### 2.2 Filter Fast Path（§13）

`diffSpecs` 新增 `filter` 变更类：层定义**除 filter 外深度相等**时归类为
filter-only，runtime 经 `map.setFilter` 应用 —— 零 remove/add、零 source
重拉、无闪烁。source 更新仍然强制 recompile（守卫序不变）；label 子层
同步继承过滤翻转；filter-only patch 不触发全量 z-order 重同步。

### 2.3 LayerFilterEvidence（§14）

独立派生证据（不扩 LayerStatus 词表）：`inactive|active|empty|invalid|
stale|unknown`。封闭算子求值器只支持 adapter 实际发射的表达式（all/any/
==/范围/in + 裸 $type/$id 旧式 + 裸字面量叶），语义严格对齐 MapLibre
（严格相等、数值范围、Multi* 几何族）；未知算子/形状 → **unknown 不猜**。
计数扫描仅对内联 GeoJSON ≤20k 要素单遍执行；MVT/超限层如实 unknown ——
绝不为徽标扫 100k+ 要素。`empty`（过滤命中 0）与 `invalid`（字段不存在）
在 Layers tab 可见（此前完全不可披露）。

### 2.4 table_panel（§9/§10）

第 18 个组件类型（仅 interactive —— 表格是工作区交互面不是制图产物面，
无导出 parity）：

- 双数据通道：`options.tableRef`（表 artifact ref → 新增 table-artifacts
  端点，前缀收紧到表族）或 `options.layerId`（HUD 图层属性表；MVT 层经
  预留的 `attribute-table` reason 按需水合）。MapSpec 只持绑定引用；
- 虚拟化（固定行高窗口渲染，DOM 行数 ∝ 视口）；行数据引用共享零复制；
  排序/过滤作用在行索引；行数上限 50k + 截断披露；
- 跨视图联动：行点击发布 `source='table'`（id_field 协议）→ map id
  过滤；map/brush → 行高亮 + 定位；chart 类别 → 行过滤（同字段同类别）；
  可选视口行过滤。

### 2.5 Component Lifecycle V3（§17-20）

- 意图：`RemoveComponentIntent`（真删除 ≠ enabled=False 隐藏）/
  `DuplicateComponentIntent`（仅多实例；新 id 冲突避让 + floating 偏移
  clamp 到有界域）/ `RebindComponentIntent`（per-type 字段白名单 +
  通道互斥 + 锁内 layerId 存在性验证）。纯函数
  `remove/duplicate/rebind_component` 与 `mutate_component` 同源；
- 同一入口三宿主：lifecycle engine（事务/COW/CAS）、用户路由
  （`remove/duplicate/rebind_component` intent bodies）、agent 工具
  （`webgis_component_update action=remove|duplicate|rebind`）；前端
  `commitComponentLifecycle` 走共享 CAS 串行链 —— 无第二套 semantic
  state 写路径；
- 删除后收敛：dock 按「id 离开 spec」prune；面板卸载清理其选择；导出
  消费 MapSpec 自动一致；finalize 对契约族重评估；
- **user-wins**：用户删除经 world_state provenance（origin=user 的
  RemoveComponentIntent）记录，finalizer 的 add_component 修复**拒绝
  复活**用户删除的默认组件（agent 删除仍修复 —— 对照语义有测试锁定）。

### 2.6 selectionField 自动生成（§15）

GeoJSON 字段映射路径（bar/pie；line 的 x 轴常为时间/数值，伪推导不如
省略）自动携带 `chart.selectionField`（name_field ?? x_field —— 确定性
推导不是猜测）；`data` 是 ref 且恰一图层族以它为源时自动解析
`options.layerId`；_plain {name,value} 路径无法可靠推导 → 如实省略。

### 2.7 Tool Surface（§27-31）

`compile_tool_surface` 纯派生投影：输入内存 SessionPlan（单一计划真相）
+ registry 元数据，输出 `{phase, preferred_tools, allowed_domains,
fallback_tools, hidden_tools, evidence}`。**不是 planner/agent/持久状态**。

阶段模型 planning→data→analysis→assembly→final（当前 pending 步骤的
tool_family 派生；product_status 显式事实覆盖）。ToolCatalog 集成是
**additive**：阶段域并入关键词激活（并集，安全网不替换）；preferred
前门（assembly 期的 webgis_map_product 等）预算豁免。hidden v1 恒空 ——
隐藏关键词命中的 tier-2 只会制造死路径；tier-3 维持
list_available_tools 自救语义，词面保留供未来策略。

### 2.8 Completion 包化（§33）

`map_completion.py`（1830 行）拆为 `completion/` 包：contracts（常量/
finding 词表/dataclass）→ inputs（gather_completion_inputs）→
validators/（execution/artifacts/layers/components/semantics/layout/
viewport_export 纯函数：snapshot → Findings[]）→ repairs（Findings →
有界 Mutation[]）→ pipeline（编排/持久化/SSE 投影）。原模块是 86/86
符号恒等的兼容 shim；1496/1496 代码行原位搬迁（行为保持，测试零变化）。

### 2.9 Raster 一等产物（§22-23）

磁盘会话栅格（`ref:raster/<id>` PNG）加入 ArtifactRegistry 生存期：

- cartography 层落盘 seam 铸造即登记（type/bbox/存储语义/层绑定有界
  元数据；文件路径与 URL 是实现细节）；
- `probe_ref` 统一探测面：store descriptor 或 O(1) 磁盘 stat（线程卸载；
  charset 白名单 + 定义模块取 BASE_STORAGE_DIR）—— sweep 不再把存活
  PNG 误判 expired；GC unlink 走活引用复检（chapter 行 + MapSpec sources
  含 imageRef + 组件 chartRef/tableRef）+ fail_on_degraded（破坏性路径
  需要真锁）；
- completion inputs / layers validator / runtime repair 对栅格 ref 与
  store ref 同面（缺失 PNG → artifact_expired 披露，不再静默跳过）。

## 3. State Ownership（不变式重申）

```text
persistent product truth  MapSpec（唯一 desired）、ArtifactRegistry（指针血缘）
transient workspace state selection / viewport / dock / pending / overrides /
                          filter evidence / chart+table artifact caches
runtime observation       RenderObservation / render evidence / filter evidence
```

全部新增 transient 状态经 session-cursor.resetLiveState 连带清空
（含 epoch bump）；零新增持久真相。Pi 仍是唯一 agent 宿主；SessionPlan
仍是唯一计划真相（tool surface 是其纯投影）；MapSpec 仍是唯一地图真相
（表格/选择/谓词绝不入 spec）。

## 4. Performance 契约

- 选择有界（ids≤50 / categories≤20 / 谓词 values≤50 / properties≤8
  标量 / 事件环≤16）；viewport 上下文有界（bbox+zoom+指纹）；
- filter 证据：内联 ≤20k 单遍，MVT/超限 unknown；证据 store ≤128 层；
- 表格：DOM 行数 ∝ 视口（5000 行 → <60 DOM 行）；行 50k 上限；索引排序
  零复制；columns/options memo（滚动 tick 不重建模型）；
- brush：命令式矩形覆盖层（60Hz 拖拽零 React 渲染）；queryRenderedFeatures
  渲染面命中（不拉数据）；label 子层剔除 + 族内去重计数；
- reconciler filter-only → setFilter（无层重建）；filter-only patch 不跑
  全量 z-order；
- 工具面：preferred 预算豁免是有界清单（≤4 前门），总预算不变（测试
  锁定 filler 仍被截断）；
- raster 探测 O(1) stat（线程卸载），GC unlink 只删「GC 态且不在活引用
  集合」。

## 5. User-wins 语义

用户显隐/拖放/折叠（既有守卫）+ **用户组件真删除**（provenance 记录 →
finalizer 修复不复活默认组件）。用户决策经 CAS（expected_revision）与
agent 突变互斥；agent 生命周期动作的绑定验证在引擎锁内复核 layerId。

## 6. Failure Semantics

- 未知 ≠ 失败：filter evidence 的 unknown（MVT/超限/未知算子）不渲染
  异常徽标；probe_ref 存储抖动 → 不进 refs（不虚构 expired）；
- 组件渲染失败降级卡片不崩 chrome；table artifact 拉取失败 TTL 后可重试；
- 删除的收敛依赖「id 离开 spec」既有语义（dock prune / 卸载清理），
  不新增清理编排；
- setFilter 的 style-spec 拒绝 → lastError（与 addLayer 失败同款重试
  语义），经 render observation 披露。

## 7. Alternatives（拒绝）

- **四套选择 store（map/chart/table/legend 各一）**：状态空间爆炸，
  跨视图一致性无单一锚点；
- **SelectionPredicate 全 SQL 化**：第二门语言 + 注入面；封闭 bbox/in
  词表覆盖当前全部真实交互；
- **extent_change 进 selection store current**：视口不是选择 —— 相机
  移动会清掉用户正在看的选择；
- **filter-only 变化继续 recompile**：闪烁 + MapLibre 重编译成本，
  选择过滤恰是最高频路径；
- **LayerFilterEvidence 并入 LayerStatus**：词表膨胀（7→12 态）且把
  「数据面事实」与「渲染面状态」混为一谈；
- **工具面隐藏关键词命中的 tier-2**：制造死路径；preferred 豁免 +
  域并集达成目标（前门可达 + 预算有界）且零破坏；
- **COG writer / 统一 RasterSource 抽象 / ModelProfile**（本轮明确
  deferred，见 §9）。

## 8. Compatibility

- SessionPlan / MapSpec schema 零破坏（组件新增 table_panel 类型为
  additive）；map_completion 全符号 shim 兼容；
- ToolCatalog surface=None 行为与既有完全一致；
- generate_chart 契约 additive（selectionField 只在可推导时携带）；
- 前端 catalog JSON 重新生成（18 类型）；所有既有套件零修改通过
  （2281 → 2350 前端 / 455 → 534 后端均为纯增量）。

## 9. Deferred

- COG writer / validator / range-read 就绪度验证（§24）与统一
  RasterSource/RasterReader/WindowedExecution 抽象（§25）—— raster v4
  本轮只做会话产物一等化（注册/探测/GC/完成度），数据面管线维持
  ADR-0089 的 windowed 契约；
- ModelProfile / 执行器画像（§32）：现有 Pi 执行策略无安全接缝，
  仅记录架构意向；
- hover 事件的完整发布（词面已就位，tooltip 通道现状已满足需求）；
- 图例（legend）作为选择源发布 filter 事件；
- 选择谓词的消费面扩展（chart 按谓词聚合高亮 —— 需廉价聚合通道）；
- MVT 层的服务端 bbox 查询/聚合增强（§36 的服务端部分）；
- dock 跨会话持久化；百万级 descriptor/服务端路径的合成压测扩容
  （150k 契约测试已锁定客户端有界性）；
- applyFilterSafe 失败的退避策略（现与 addLayer 失败同款语义）；
- brush 的多边形/套索模式（协议已可扩展：谓词词表是封闭集）。
