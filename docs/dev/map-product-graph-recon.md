# Semantic Map Product Graph — Phase 0 勘察（recon）

- 日期：2026-09-14
- 基线：`origin/master` = `580b33e9`（PR #1272 ads-v1 合并后）
- 分支：`cartography/semantic-map-product-graph-v1`（独立 worktree `webgis-mspg-v1`）
- 方法：主 Agent 亲读核心生产文件（product_graph/product_facets/product_action/product_templates/
  map_product_service/planner/tools/session_plan/mapspec_schema/composition_templates/
  map-product-completion-runtime-audit/pi-host-seams）+ 1 个只读 subagent 面扫描
  （PR diff 冲突面 / ADR 编号 / 审计 finding 复核 / 调用方清点）。

## 1. 生产调用链 before

```text
用户请求（"成都小学分布情况"）
  ↓ Pi（唯一 Agent Host，webgis_execute 单代理）            [ChatEngine 旧路共用 ToolRegistry]
webgis_map_intent（tier1, tools.py:411）
  ├─ resolve_map_request_intent → MapRequestIntent（scope/subject/task/output_intents）
  ├─ MapProductPlanner.plan_from_intent（确定性，memo 跨调用）
  │    recipe 裁决（RecipeRegistry.select_candidates）+ 模板裁决（TemplateSelector）
  │    → MapProductPlan{data_requirements, analysis_steps, map_layers, components,
  │                     statistics, charts, fallbacks, template_selection, …}
  └─ result["plan"] ──apply_tool_result──▶ gis_chapter（SessionPlan envelope，Redis，
                                            会话锁内 supersede/replace）      ← 计划真相
  ↓ Pi 逐个调数据/分析工具（poi_query / spatial_aggregate / admin 边界 …）
  ↓ 每个结果：apply_tool_result → _mark_progress（能力行状态 + bound_ref）
  ↓
webgis_map_product（tier2, tools.py:582）
  ├─ plan 连续性回放（同 memo 键）+ finalize_with_profile（eligibility 复检/降级链）
  ├─ 角色绑定 _resolve_binding（#784 计划标签优先 / type_role_map 兜底 ← CA-P2-3 第三份硬编码）
  ├─ 缺层补齐（heatmap/points 经 convert_analysis_to_mapspec_layer → mapspec_store.layer_upsert）
  ├─ 组件回填（legend 族 layerId、title）→ mapspec_store.layout_set（整表替换，保留用户浮动面板）
  ├─ provenance→capability 回填 + artifact_lineage + assess_completeness（binding-based）
  └─ result{map_product_evidence, completeness, guidance, mapspec…}
       ├─ merge_map_product_result → chapter{completeness, recipe_id, workflow_contract,
       │                                     methodology_warnings, fallbacks}        ← 证据合并
       └─ MapProductEvidence（evidence.py:256）→ PiAgentHarness.compute_map_product_completeness
  ↓
turn 收尾：bridge maybe_finalize_map_product（G18 additive task_complete.map_product）
  + completion/pipeline.py _validate_all → gis_chapter["map_product"]（map_product_block）
  + evaluate_cartographic_session（ADR-0071 共享制图闭环：verdict/self-heal/quality）
  ↓
派生只读投影（绝不持久化，ADR-0076/0085）：
  build_product_graph / build_facet_completion（product_graph.py）
  ← 消费者：session_plan [GIS Plan] 投影行、workflow_engine、analysis_graph
  ↓
渲染真相：MapSpecDocument v1.2（mapspec_schema.py；sources/layers/layout{components,
  frames, labels, component_links}/thresholds）→ live MapLibre + export（resolve-components 共享）
  ↓
项目级版本账本：MapProductVersion（map_product_service，ADR-0092/0099；五维 diff、
  fork/restore/merge、append-only）← workflow run 完成时 auto-record
```

### before 图的结论（缺口定位）

1. **产品语义无持久文档**：view 构成/视图关系/交付意图/声明只散落在
   chapter 行（计划）、composition 模板槽位（静态库）、MapSpec components（渲染态）三处，
   turn 间没有"这张产品是什么"的一等对象。
2. **视图关系零显式化**：same-dataset / derived-statistic / comparison / overview-detail /
   chart-linked-to-map / shared-legend / shared-extent 只以 `options.layerId`、
   `component_links`（v1.2 显式边，仅组件级）等局部字段隐式存在；产品级关系图不存在。
3. **completeness 是 binding-based**（planner.assess_completeness + facet 契约只回答
   "组件族缺席是否欠账"），没有语义级产品完整性
   （"用户要 district comparison → 对比视图真的在？"、"source note 在？"、
   "chart 与 map 同一过滤数据？"、"export 覆盖整个产品？"）。
4. **产品编辑只有组件级突变**（webgis_component_update 单组件），没有"去掉右边的统计图 /
   改成柱状图 / 加主城区插图 / 只保留耕地 / 改 16:9"的产品语义编辑 → 受影响视图局部重编译。
5. **CA-P1-3 五源组合抽象并存**（composition_templates ∥ template_registry v9 预设 ∥
   product_templates ∥ recipes ∥ build_default_components），CA-P2-3 第三份 type_role_map ——
   抽象收口的窗口。

## 2. 与最近 PR 的重叠矩阵

图例：`new`=本方向新建文件；`touch`=本方向将修改；PR 列 = 该 PR 是否触及同一文件。

| 能力/文件 | 本方向动作 | #1270 | #1273 | #1274 | #1275 | #1276 | #1277 | #1278 | #1279 | 结论 |
|---|---|---|---|---|---|---|---|---|---|---|
| product_spec.py 等新产品模块（gis_harness） | new | – | – | – | – | – | – | – | – | 干净，零冲突 |
| product_graph.py（扩展投影消费 spec） | touch | – | – | – | – | – | – | – | – | 干净 |
| gis_harness/tools.py（webgis_map_product 接编译器 + 新工具） | touch | – | ✔ | – | – | ✔ | – | – | – | 文本冲突风险，合入序在后；语义正交（1273=QC 收敛、1276=capability graph 投影） |
| session_plan.py（merge_map_product_result 增量键） | touch | – | – | – | – | – | ✔(SessionPlan V2 重构) | – | – | #1277 重构该文件 —— 保持 merge 函数小而局部，降低冲突面 |
| map_product_service.py（产品账本） | 不改/只读 | – | – | – | – | – | – | – | – | 全库零占用 |
| app/lib/cartography/*（symbology/label/layout/composition） | 只读消费 | – | ✔(5 文件) | – | – | – | – | – | – | 不改这些文件 |
| mapspec/**、MapSpec schema | 只读消费 | ✔(coordinator) | – | – | – | – | – | – | – | 不改 |
| agent_pi_bridge.py | 不改 | – | – | ✔ | – | – | ✔ | – | – | 不改（evidence 经工具结果自然上行） |
| frontend | 本轮不改 | – | – | – | – | – | ✔(session-plan 族) | – | – | 产品 spec 后端先行；前端消费留接口 |
| ADR 编号 | 新增 0183+ | 0159 | 0159 | 0180 | 0180 | 0181 | 0180 | 0182 | 0182 | master 最大 0179，在途最大 0182 → 本方向取 **0183** |

## 3. 已有能力（复用清单 —— 只消费不重做）

| 能力 | 单一真相 | 本方向用法 |
|---|---|---|
| Recipe（资格/降级链） | `gis_harness/recipes.py` RecipeRegistry | spec 引用 recipe_id；编译器不复制 eligibility |
| 产品模板（archetype/layer_roles） | `gis_harness/product_templates.py` + TemplateCatalog | spec 引用 template_id + archetype；产品类型库从 PRODUCT_ARCHETYPES 演进 |
| 组合模板（槽位/必需性） | `app/lib/cartography/composition_templates.py` + component_registry | 编译器把 view/component 语义解析为槽位满足，槽位语义仍是 composition 的裁决者 |
| 组件描述符 | `component_registry.py`（V7 注册表对齐校验） | 组件选择只经 registry，不建第二份目录 |
| 图表 kind 契约 | `chart_kinds.py`（七态状态机） | chart view 的 kind 词表同源 |
| 计划真相 | SessionPlan envelope（会话锁 + supersede） | MapProductSpec 以 additive key 存 chapter（见 decisions D2） |
| 渲染真相 | MapSpecDocument v1.2 + lifecycle_engine（CAS/revision） | 编译产物经既有 mapspec_store 意图通道落 spec |
| facet 完成度投影 | `product_graph.py`（ADR-0085 派生只读） | 扩展为可消费 product_spec 的输入（仍派生、仍只读） |
| 证据类型 | `evidence.py` MapProductEvidence + `product_lineage.py` | M7 evidence 挂 spec 视图，harness 转录面不推断 |
| 版本账本 | `map_product_service.py`（append-only） | 不改；spec digest 后续可作为 ledger 输入（本批不做 DB 变更） |
| 完成管线 | `gis_harness/completion/`（validators/pipeline） | M8 完整性验证是其**语义侧补集**，不改其裁决 |
| 意图理解 | intent_semantic.py / ADR-0150 | spec 的 goals/claims 来源，不重写 |

## 4. 审计 finding 仍成立性（本方向相关）

| finding | 状态 | 本方向处置 |
|---|---|---|
| G1-G5（turn 终态/导出 placement/视口） | 主修复已落地（completion/、resolve-components、finalizer） | 不重做 |
| G16 completeness 不含视口/组件核验 | 已由 completion validators 补 | M8 只做**语义完整性**（产品构成 vs 用户要求），与渲染/视口核验正交 |
| G20-G23 | 显式 defer（ADR-0081 follow-up） | 不吞入 |
| CA-P1-3 五源组合抽象 | **仍成立** | M0/M5 收口方向 = product spec 作为语义消费者统一引用五源，不在本批合并五源本体（风险面过大），但给出唯一语义 owner |
| CA-P2-3 type_role_map 第三份硬编码 | 仍成立（tools.py:686） | 本方向不动该映射（1273/1276 在途），PR body 记录 follow-up |
| chart/statistics 导出 LIVE-only（G4 残留） | 仍成立（component_renderers.py:91-97） | spec 的 delivery/completeness 把 export 覆盖缺口**如实披露**（validator 输出），不在本批实现导出渲染 |
| H-8/H-9 前门可达性 | 1273/1276 在途处理 | 不碰 |

## 5. 不做清单（防重复施工裁定）

- 不重写 MapSpec runtime / lifecycle_engine / completion pipeline（渲染与完成真相已存在）。
- 不重做 adaptive symbology / label / layout / narrative / publication / visual judge（AC V7-V11 波次）。
- 不把 product schema 做成第二个 MapSpec：不收 paint/legend_spec/placement 细节。
- 不新建通用 agent loop / 不复制 Pi tool loop；新工具仍走 ToolRegistry 单一执行真相。
- 不在本批合并 CA-P1-3 五源本体（仅建立唯一语义 owner 与引用边）。
- 不写完整 ExecutionGraph scheduler（方向 5 未合并；编译器只产 refs/需求）。
- 不复制 Goal Evaluator（方向 7 未合并；暴露 required claims/views/completeness 证据即可）。
- 不做成都小学专用逻辑（golden corpus 用合成 fixture 覆盖该形态）。
