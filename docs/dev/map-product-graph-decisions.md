# Semantic Map Product Graph — 决策日志（decision log）

规则：每条决策 = 背景 → 备选 → 裁定 → 理由/回滚面。任务书假设与代码不符时，
追加"任务书假设 → 实际代码 → 调整"条目。

## D1（M0）语义产品唯一 owner = MapProductSpec（新），MapProductPlan 保持执行计划语义

- 背景：master 已有五个"产品"概念族——MapProductVersion 账本（ADR-0092/0099，项目级
  版本证据）、MapProductPlan（planner，draft→finalized 执行计划）、ProductGraph（ADR-0085
  派生只读投影）、MapProductTemplate/MapCompositionTemplate/CartographyRecipe（静态类型
  库）、chapter["map_product"]（完成块证据）。CA-P1-3 记录五源组合抽象并存。
- 备选：A) 把 MapProductPlan 升级为持久语义产品文档；B) 新建 MapProductSpec 作为
  产品语义文档，Plan/账本/投影保持原语义，spec 被投影与账本消费；C) 扩展
  MapProductVersion 行存语义图。
- 裁定：**B**。
- 理由：Plan 是"怎么执行"（数据需求/分析步骤/资格/降级），语义产品是"产品是什么"
  （视图构成/关系/声明/交付），生命周期与消费者都不同；A 会让计划真相承载第二职责
  （违反 ADR-0076 单一真相精神），C 需要数据库迁移且把语义图绑进 append-only 证据行
  （编辑语义无法表达）。B 让 spec 成为：intent 的下游（goal→spec）、编译的上游
  （spec→MapSpec/图表/组合）、投影的上游（spec→ProductGraph）、完整性验证的对象。
- 回滚面：spec 是 additive 对象；删除 spec 相关键/工具参数，全部行为回到 master 语义。

## D2 spec 持久化位置 = SessionPlan chapter 的 additive key（`chapter["product_spec"]`）

- 背景：任务书要求 versioned schema。可选：Redis 独立对象 / chapter 内嵌 / DB 表。
- 裁定：chapter 内嵌 `product_spec`（序列化 dict，自带 `spec_version` + `revision`
  + `digest`），经 `merge_map_product_result` 的 presence 语义合并。
- 理由：chapter 已有会话锁、supersede 归档、resume 锚定、投影管线；独立 Redis 对象
  会造出第二个会话真相与第二套并发问题；DB 表过早（跨会话产品引用是账本职责，
  本批不动 DB）。spec 自带版本号 + digest，未来可无损升级为独立存储或进账本。
- 约束：spec 有界（views ≤ 12、组件引用 ≤ 32、evidence 条目有界），对齐 envelope
  的 bounded 纪律。

## D3 编译器 = 纯函数 `compile_product_spec`，落图仍走既有 mapspec_store 意图通道

- 裁定：`MapProductSpec → ProductCompileResult`（视图计划/组件指派/图表 spec/组合
  槽位满足/决策与降级披露/digest）是纯函数；**编译器不直接写 MapSpec**，
  `webgis_map_product` 消费编译结果，仍经 mapspec_store 的 layer_upsert/layout_set
  通道落图（CAS/revision/事务不变）。
- 理由：不复制 Pi/tool loop，不做第二写路径；确定性可测（同输入同 digest）；
  user explicit choice 优先级在编译器内统一裁决（spec.overrides > intent 显式 >
  recipe/template 缺省），替代散落在工具体里的 if 链。
- 回滚：编译器输出只是"建议+披露"，工具可整体回退到既有组装路径（保留为 fallback）。

## D4 产品编辑（M6）= 新 tier2 工具 `webgis_product_edit`，语义操作词表 + 受影响视图重编译

- 裁定：编辑操作落在 spec 图上（remove_view / replace_component / add_view /
  set_view_filter / set_delivery / toggle_component …），返回
  affected_view_ids + 重编译结果；不销毁未受影响视图的语义与证据。
- 理由：`webgis_component_update` 是渲染组件级突变（单组件、CAS 面向用户拖拽），
  语义编辑的粒度是视图/关系/交付；两者互补不重叠。spec.revision 递增 + digest 变化，
  与 mutation_revision（MapSpec 面）是两层各自身份。
- 回滚：工具失败 → spec 不变（先验证后提交，事务语义在纯函数侧保证）。

## D5 ADR 编号取 0183（编号占用核验：master ≤0179；在途 PR 占 0180×3 / 0181 / 0182×2）

- 裁定：本方向主 ADR = `0183-semantic-map-product-graph-v1.md`；若 PR 时点 0183
  已被并行分支占用，rebase 时顺延为当时最小未占用号并在 PR body 归因（不询问用户）。

## D6 测试基线 = scoped + cartography marker；全量回归只跑受影响域

- pytest 默认 addopts 带 --cov；迭代用 `--no-cov -p no:cacheprovider` + 指定测试文件；
  里程碑收口跑 `-m cartography` 与 gis_harness/session_plan 相关目录（`-n 2` 若 xdist
  可用，否则串行）。

## D7 任务书假设 vs 实际代码（"代码即事实"记录）

| 任务书假设 | 实际代码 | 调整 |
|---|---|---|
| `MapProductPlanner`/`webgis_map_product` 可能不存在，需寻找 | 都已存在且是 CORE 前门工具（tools.py:411/582） | 演进而非新建；编译器作为组装工具的确定性内核 |
| MapProductSpec/MapProductGraph 不存在 | ProductGraph 已存在（ADR-0085 派生投影）；"MapProductSpec"名称未占用 | 新建 spec 文档；投影扩展为可消费 spec，不建第二张图 |
| product templates 需从零建库 | PRODUCT_ARCHETYPES + SEED_PRODUCT_TEMPLATES 已有 6 原型 7 模板 | M3 类型库 = 原型→视图构成映射（product shape），模板/配方仍是被引用的类型库 |
| M2 "component graph" 需新建 | MapSpec v1.2 已有 component_links + component_graph.py（组件级） | M2 做的是**视图级**产品关系图（view graph），与组件图分层：产品图边可落到 component_links/annotations，但不重复其推导 |
| completeness validator 可能已有 | planner.assess_completeness 是 binding-based；completion validators 是渲染/视口侧 | M8 做**语义完整性**（要求 vs 构成），三者正交互补 |
| 方向 5 ExecutionGraph / 方向 7 Goal Evaluator | 均未合并（open PR 不含） | 按任务书并行接口：编译器只产需求 refs；validator 暴露 required claims/views 证据 |

## D8 兼容性红线（review 阶段复核用）

- `webgis_map_intent` / `webgis_map_product` 的既有 result 键全部保留（contract_version
  不因 additive 键提升；若评审认为需要，跟随仓库惯例单独决策）。
- chapter 旧会话无 `product_spec` → 一切消费者按缺失降级（与空契约同语义：不虚构）。
- 不改 `mapspec_schema.py` 的 MapSpec 词表；产品视图种类词表独立于 COMPONENT_TYPES，
  映射关系在编译器单一登记。

## D9（最终 review 修复记录，Subagent B 四轴复核）

Subagent B 独立 review 结论：P0=0，P1=5，P2=10。处置：

| finding | 级 | 处置 |
|---|---|---|
| toggle_component 开关语义反转（enabled=true 走删除） | P1 | 修复：enable/disable 各走 patch 通道，缺省 True，关闭绝不滑向删除 |
| remove_view 按 type 全表删组件跨视图误伤 | P1 | 修复：组件必须可归因（options.layerId==layer_hint 或 id==component_hint），不可归因 → unapplied 披露不删 |
| 组件物理同步局部失败静默 | P1 | 修复：失败项全部入 unapplied_effects（对账账本） |
| 编辑/组装读-改-写竞态（锁外读 spec） | P1 | 修复：双层 CAS —— 工具侧落物理面前重读 digest 预检；merge 侧 base_spec_digest 不符即拒写 + product_spec_conflict 披露 |
| replace_component 赋值绕过 pydantic 界 | P1 | 修复：product_spec 全模型 validate_assignment + payload op 白名单消毒（键白名单+值截断） |
| merge 硬编码 12 | P2 | 修复：import MAX_VIEWS |
| override 裁剪可丢 remove_view 撤回证据 | P2 | 修复：结构性账豁免裁剪（按 target 去重），validator 上限 = MAX_OVERRIDES+MAX_VIEWS |
| merge 注释错位 | P2 | 修复 |
| _CHROME_FAMILY_TYPES 第二份白名单 | P2 | 修复：删除，直接消费 template.default_components |
| kind→组件族映射三份 | P2 | 修复：VIEW_KIND_COMPONENT_FAMILIES / SPEC_KIND_TO_FACET 单一真相迁入 product_spec，三方引用 |
| produce_product_layer 裸 except 无堆栈 | P2 | 修复：logger.exception |
| chart_kinds 裸 import | P2 | 修复：护栏 + chart_kinds_unavailable 降级披露 |
| merge 无 relations 界 | P2 | 修复：MAX_RELATIONS 封顶 |
| comparison/time 投影恒 pending（假欠账） | P2 | 修复：chart 族承载在场即 done |
| contract chart:required 与 spec chart 节点双计数 | P2 | 复核：dedup by kind 已防双节点（spec 块后行、kind 去重），无代码改动需要；复核记录在案 |
| override.payload/filter 尺寸界 | P2 | 修复：随 payload 白名单消毒（键数+值截断） |

修复后回归：新增 4 条 review 回归测试（CAS×2、结构账豁免、comparison 承载）+
payload 消毒测试扩展；product 系 116 全绿；gis_harness 全目录 1410 passed
（仅 2 个 master 预存失败，归因见 ledger）；cartography 门禁 933 passed；
session-plan/tool-meta 34 passed。
