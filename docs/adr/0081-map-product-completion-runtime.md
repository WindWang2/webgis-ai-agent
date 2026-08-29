# ADR-0081: GIS Map Product Completion Runtime 与 Live/Export Parity

- 状态：Accepted（2026-08-30）
- 关联：ADR-0080（Runtime v3 / PlanGraph / runtime-layer-registry）、ADR-0076（SessionPlan 是 Pi-path 计划真相）、ADR-0070（组件运行时 v2 / placement）、ADR-0074（布局 QA 与修复预算）、ADR-0079（Runtime v2 / GISMutationBatch / user-wins）、ADR-0062/0063（cartographic verdict / production gate）
- 分支：`feat/map-product-completion-runtime`
- 审计基线：`docs/dev/map-product-completion-runtime-audit.md`（5 个独立审计角色的合并发现）

## 1. Context

Runtime v3 之后，系统在"计划/执行"侧已经单一真相（SessionPlan 行状态 → PlanGraph
派生投影；runtime-layer-registry 单账本），但**完成侧**存在系统性断层：

1. **turn 在 `agent_settled` 结束时对最终地图产品零检查**——"任务完成" =
   "LLM 停止说话"。DAG 全 complete 与"地图真的完成、可见、正确"之间没有任何
   确定性检查；`finalize_display` 仅靠 SYSTEM_PROMPT 约束调用。
2. **capability complete = "tool 返回 ok"**：空 FeatureCollection 也推进行状态；
   bound_ref 过期（4h TTL）无人察觉。
3. **视口无人负责**：MVT 大层跳过全量 fetch 后聚焦链断裂、restore 不取景——
   "层在、视口看不见、无人修复"是真实可达状态。
4. **Live 与 Export 的组件语义分叉**：exporter 不读 placement（0 处引用），
   图例数据源是 HUD 而非 spec 组件、指北针旋转符号相反、连续色条退化为离散
   色块、chart/statistics 仅 live、PDF 文本层 subtitle 只读请求参数、后端支持
   矩阵输出与实现相反的声明。

## 2. Problem

- 需要一个确定性的完成度裁决："mandatory DAG complete **+** 地图产品校验通过
  = 最终地图完成"，且不新建第二套事实源、不让 LLM 维护状态、不绕过 harness；
- 修复必须有界、确定性、低风险，用户显式决策（user-wins）优先；
- Live 与 Export 必须从同一 placement/组件语义出发，语义一致（不要求像素级
  相同）。

## 3. Decision

### 3.1 Completion Runtime（后端）

新模块 `app/services/gis_harness/map_completion.py`：**派生运行时逻辑**，只读
既有真相（章节扁平行 / MapSpec / session artifact descriptors / composition
模板 cardinality / renderer 支持矩阵），产出有界结果：

```text
MapCompletionResult {
  status: pending | needs_repair | complete | failed
  findings[]   (code/severity/target/detail/repair, ≤12)
  repairs[]    (≤6)
  viewport_status / layer_status / component_status / export_status
  result_bbox  (ref descriptor bbox 并集)
  summary      (单行 ≤120 字符)
}
```

**它是 derived runtime logic。它不是第二套 MapSpec、不是第二套 SessionPlan、
不是第二套 runtime-layer 真相、不是第二套 required/optional 组件 schema**
（组件必需性复用 composition template 的 slot cardinality）。

### 3.2 Validators（全部纯函数，输入一次聚合）

- **execution**：复用 `build_plan_graph`（单一计算源）——mandatory 节点未全
  complete/skipped → `pending` + `needs_execution`（failed/unavailable 明确
  披露为"欠重试/欠降级"，finalizer 绝不自己重跑算法）；
- **artifacts**：complete 行无 bound_ref → `artifact_missing`；ref 不在
  session store → `artifact_expired`；descriptor feature_count=0 →
  `empty_result`（空结果有明确语义：不 complete）；
- **layers**：planned result layer 缺席 → `layer_missing`；source 不在册 →
  `source_missing`；结果层 desired-visibility=none → `layer_hidden`
  （可修复）；完全无数据层 → `no_result_layer`；
- **components**：模板 required 组件缺失 → `component_missing`（可修复）/
  禁用 → `component_disabled`（可修复）；单例重复、孤儿 layerId → warning；
- **layout**：floating 矩形重叠（与 semantic_checks 同几何语义）→ warning，
  **user-pinned 只披露不挪动**；
- **viewport**：bbox 可派生 → `repairable`（相机真相在前端，见 3.4）；有层
  无 bbox → `invalid` + warning；
- **export parity**：enabled 组件是否全部有导出消费方（支持矩阵派生的
  desired-state 判定；渲染级 parity 由共享 resolver + 测试锁定）。

### 3.3 Bounded Repair Loop

`MAX_FINALIZATION_PASSES = 2`：validate → repair → revalidate，每轮 repair 后
重读 MapSpec（修复改变 desired state）；修复通道**全部复用既有突变入口**
（用户/agent 同一事务语义）：

- `add_component` / `enable_component` → `mapspec_store.patch_component`
  （upsert 走 mutate_component 同一工厂，无第二套默认值）；
- `show_layer` → `apply_gis_mutation_batch`（GISMutationBatch）——
  **user-wins 守卫免费获得**：用户显式隐藏的结果层，修复被拒并如实披露为
  needs_repair（有回归测试锁定）。

不可修复的 error 直接落 failed；repair 通道全部失败即停（不空转）。

### 3.4 Harness 集成（触发点）

- **每个成功工具结果后**（bridge `apply_tool_result` 之后）：幂等门 =
  章节 `map_product.status==complete` 且 checked_revision == 当前 MapSpec
  revision 才跳过——任何后续突变自然重新满足终验条件；
- **turn 收尾**（`agent_settled` 映射 task_complete 之前）：兜住不经
  SessionPlan 的展示类命令与新 desired state。

结果写回 `gis_chapter["map_product"]`（additive 单键、有界），投影层只读：

- `[GIS Plan]` 块尾追加一行 `Map product: final | needs repair(...) |
  incomplete(...) | pending`（§9 的有界披露形式）；
- `task_complete` SSE 增添 additive `map_product` 字段（前端可区分"turn
  结束且地图已验证"与"turn 结束但未验证"）；
- 独立 `map_finalization` SSE 事件携带 bbox/状态给前端 finalizer。

### 3.5 Pi Boundary（不变）

Pi 每轮仍只看有界投影（首行 + [GIS Plan] 块 + 新增的一行 Map product）。
完成度裁决、修复、幂等门全部在 Harness 侧确定性推进；Pi 不解析长文本、
不维护完成态。**无 Pi fork、无第二 agent loop。**

### 3.6 前端视口终验（相机真相在前端）

`frontend/lib/map-product/finalizer.ts` + `map_finalization` 命令
（viewCommands）：

- 视口与结果 bbox **相交 → 不动相机**（用户正在看结果）；
- 不相交 → `navigation.fitBounds` 一次（degenerate bbox 由 minSpan 拓宽、
  maxZoom 上限；**空结果无 bbox → 不修复**，绝不 fit 到空集）；
- 修复在 `runCameraCommand` 内执行——用户手势仲裁/自中断/superseded_by_user
  语义对 finalizer 同样生效（不与用户抢相机）；
- 每个载荷至多一次相机动作（有界，无重试风暴）。

### 3.7 Live/Export Parity（共享解析层）

`frontend/lib/map-components/resolve-components.ts`：live（MapSpecChrome）
与 exporter 共用的纯函数解析——anchor 优先级 `placement.anchor > 旧
position > 类型默认`（与 live DEFAULT_POSITION 同表，无第二套默认值）、
floating 像素坐标、enabled/text/variant/layerId 归一化。

Exporter 在 spec 组件在场时构建 `ExportChromeModel`（export-chrome.ts）：

- **placement parity**：七槽锚点映射画布槽位（title top-center、scale bar
  bottom-right、legend bottom-left——与 live 缺省一致）；floating 坐标按
  画布/视口比例缩放（确定性换算，无像素级承诺）；
- 指北针旋转符号修正（`-bearing`，与 live 同向；旧导出为 `+bearing`）；
- 连续色条绘制渐变 ramp + min/mid/max + unit；heatmap 图例携带量化口径；
- 图例/色条出场由 spec 组件 enabled 门控（HUD 发现降级为兜底）；
- **statistics_panel / chart_panel 导出**：统计卡 + 确定性静态图表
  （bar/line/pie/scatter），数据来自与 live 相同的 `options.chart` /
  `chartRef` artifact 协议（无第二套图表 schema）；collapsed 面板导出折叠条；
- attribution 读 spec 组件文本；PDF 文本层 subtitle 使用与 canvas 相同的
  请求 > spec 链；
- 无 spec 组件的旧会话 → legacy 固定槽路径，行为不变。

后端支持矩阵（component_renderers.py）与 descriptor `exporter_support`
同步到现实，catalog 再生——"单一事实源"目录不再输出虚假声明。

### 3.8 运行时层修复（顺带，§Source Lifecycle follow-up）

- **隐藏复活修复**：renderer 的 `setLayoutProperty('visibility')` 同步
  账本 layerDef.layout——style reload 的定义重放恢复隐藏态，不再复活为可见
  （`recordRuntimeLayerVisibility`，两个 seam：updateLayerStyle /
  setLayerStackVisibility）；
- **source 所有权转移**：`unregisterRuntimeLayer` 清扫时把 sourceDef 移交
  仍引用该 source 的存活兄弟层（重放不丢 source）；最后引用者出账仍干净。

## 4. Failure Semantics

- finalization 本身异常 → best-effort 日志，**绝不阻断工具返回/turn 收尾**
  （增值信号语义与 graph 投影一致）；
- 修复通道失败 → 留给下一轮披露（passes 有界，不重试风暴）；
- `needs_execution`（pending）→ 交还 DAG/Harness 重试语义；
- 前端相机修复被用户手势 superseded → 如实 failed（不抢相机）。

## 5. Performance

- validators：图层/组件 O(N)、布局 O(C²)（C=chrome 组件数，个位数）、
  bbox 全部来自 O(1) ref descriptor（无 GeoJSON 复制、无逐 feature 扫描）；
- 终验幂等门：complete + revision 一致时零成本跳过；
- `build_plan_graph` 真实计划毫秒级（ADR-0080 既有性能契约）；
- 前端：每 finalization 载荷至多一次相机动作；导出 chrome 模型构建是一次
  纯派生（chartRef 拉取仅 chart_panel 大载荷时发生）。

## 6. Compatibility

- `webgis_*` 工具结果形状不变；contract_version 不动；
- SessionPlan SSE 事件名与首行投影格式不变（新增 `map_finalization` 事件与
  `task_complete.map_product` 字段均为 additive）；
- MapSpec / 组件 schema 零变更（placement 消费是既有字段）；
- 无 spec 组件的旧会话导出走 legacy 路径（行为不变）；
- 唯一行为变更（均为审计记录的缺陷修复）：指北针导出方向、图例导出场次、
  PDF subtitle 链、隐藏命令层在 style reload 后保持隐藏。

## 7. Rejected Alternatives

- **Completion 状态持久化为独立模型**——第二事实源；改为章节 additive 单键
  + 派生投影；
- **finalizer 触发重跑 GIS 算法**——违反 Pi/Harness 边界；`needs_execution`
  披露交还重试语义；
- **服务端校验相机**——相机真相在前端（uncontrolled）；服务端只保证 bbox
  可派生，相交判定与修复在前端命令通道（复用手势仲裁）；
- **MapRenderScene 中间层**——审计后判定共享 resolver 已达成语义一致，
  为架构美观新增持久派生层违反"不为美观加层"；
- **导出布局引擎重写**（完整 constraint solver）——保持第一版：anchor 槽位
  + floating 缩放 + 现有堆叠语义；auto 重排记为 follow-up。

## 8. Deferred Work

1. 完整导出布局引擎（anchor 组件超限时的 auto 重排、像素级防撞）；
2. inset_map / graticule / map_border 渲染器（两侧均未实现）；
3. source 生命周期治理的完整 GC（ref 生命周期所有，账本所有权转移只是
   防御性补全）；
4. FIFO 256 驱逐静默丢重放、remount 绕过 renderer 注册（viewport culling
   不再作用于重放层）、HUD 行不随 spec 重同步、reorder_layer 对 spec 层
   无效、custom 层不可交互；
5. 服务端 /export/pdf（reportlab）与 /export/geojson 孤儿路径清理；
6. MVT 跳过 fetch 的聚焦兜底与 restore 取景的系统性方案（当前由前端
   finalizer 的视口修复覆盖完成时场景）。

## 9. Completion Contract（一句话）

```text
all mandatory capabilities complete  +  map product validation pass
        = final map complete
```

DAG 完成只是终验的**前置条件**；完成态由 `MapCompletionResult` 确定性地
表达，经有界投影披露给 Pi 与前端，修复在有界轮数内自动执行，用户显式
决策永远优先。
