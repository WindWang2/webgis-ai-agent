# AC-07 图面整饰自动排版 — P0 勘察报告（只读产物）

> 线：adaptive-cartography/07-layout-auto-compose · ADR-0156 · 基线 origin/master@1fd4b035
> 姊妹产物：`ac-07-component-matrix.csv`（组件矩阵）、`ac-07-baseline.json`（20×4×2 口径基线原始数据）、`ac-07-plan.md`（实施计划）、`ac-07-decisions.md`（决策日志，随实现更新）

## 1. 组件清单真值（校正任务书）

- **类型词表 20**：`ComponentType` 联合（app/services/gis_harness/components.py:18-41，含 basemap 占位）；
- **后端 descriptor 19**：`_SEED_DESCRIPTORS`（component_registry.py:263-590，无 basemap）；
- **前端 live 渲染器 18**：`registerComponentRenderer` 注册（basemap/export_layout 无 live 渲染器）；
- 任务书「22 个组件」按上述三口径分别校正为 20/19/18（矩阵 CSV 全量证据）。

## 2. 关键现状（与任务书 delta）

| 维度 | 真相 | 对本线的影响 |
|---|---|---|
| inset_map | **全链 native**：前端 inset-map.tsx（静态 SVG 投影，不 mount 第二 maplibre）、后端 runtime_status="native"（registry:538）、export drawChromeInset 同链 | P6 收窄：native 测试锁定 + source 隔离断言 + hierarchy 变体等残留 + 文档漂移修正（gis_harness/components.py:33-37 注释仍写 planned） |
| 三检查自愈 | COMPONENT_OUTSIDE_CANVAS 已 fail+auto_safe+suggested_fix；LAYOUT_COLLISION warning 无建议；COMPONENT_LINK_CYCLE fail+not_repairable（断环靠渲染回退 (priority,id) 序） | P1 = LAYOUT_COLLISION 修复建议 + 断环建议 |
| solver | V2 solve_component_layout + V3 solve_layout_v3（width_units/碰撞组/avoid_zones/compact/诊断）；**V4 不存在** | P1 实现 V4：选位 + 冲突自愈策略链 |
| fallback 注入通道 | live chrome（map-spec-chrome.tsx:46-58）、export（export-chrome.ts:586-603+629-667，enabled=false 不注入）、render-observation 镜像（:214-219）、**后端 include_chrome 不注入**（mapspec_to_svg.py:446 user-wins） | 后端不注入是 parity 缺口：版面中间层需裁决（本线决策：live/export 主动补全 + 记录 decision；后端 print 通道维持 user-wins 不虚构，中间层携带 provenance 供其采纳） |
| legend_spec v2 | 后端构造器只写 v1 字段 + `unit`；`k/nodata_label/out_of_range_label/clip_policy` 均不存在；03 线 schema 未合入（参考 webgis-wt-ac-03/docs/dev/ac-03-legend-spec-v2.schema.json） | P7 按 schema fixture 驱动实现消费端（局部窄类型，不改共享 types.ts） |
| scale_bar | nice-number 米/km 标注 + dual_unit 英制；**无 1:xx 数字比例尺** | P3 |
| north_arrow | 仅网格北 rotate(-bearing)；descriptor 声明 dual_convention 变体但前端静默回退 Compass；**无真北/磁偏角** | P4 |
| graticule | 间隔唯一由 zoom 表决定（options 仅 color/dark）；角标仅底/左内缘；**密度不可调、无图廓注记带** | P4/P5 |
| 图例族 | nodata 条目已消费（legend-model 末尾追加）；`unit` 字段存在但**图例卡/色条不渲染 unit**；无 out_of_range | P7 |

## 3. 版面三检查的调用面与语义边界（P0 第 2 项）

- 入口 `evaluate_cartography_semantics(mapspec, source_profiles=None)`；**canvas/版式不是参数**：
  - LAYOUT_COLLISION：zone 计数模型（ZONE_CAPACITY + top-center exclusive + singleton 重复 + 悬空 layerId + floating 矩形重叠），与画布尺寸无关；
  - COMPONENT_OUTSIDE_CANVAS：画布硬编码 1280×720（semantic_checks.py:2518），安全区来自 `layout.margins`；
  - COMPONENT_LINK_CYCLE：requires/under 图环（Kahn 残量），与画布无关。
- **版式维度只能经两条路径影响检查**：a) solve_layout_v3 按 profile 重排 position；b) `layout.margins` 改变安全区。本次基线两条都用了（见 §4）。

## 4. 20 MapSpec × 4 版式告警基线（P0 第 2 项核心数据）

样本：golden corpus `build_cases()` 取 20（10 profiles 矩阵 + 10 variants 变体钉选）；
4 版式 = corpus profile 词表映射（viewport=屏幕 4:3、presentation_16x9=屏幕 16:9、a4_portrait、a4_landscape）；
每版式配对应 margins。双口径：

| 口径 | 评估次数 | LAYOUT_COLLISION warning | COMPONENT_OUTSIDE_CANVAS fail | COMPONENT_LINK_CYCLE fail |
|---|---|---|---|---|
| **native**（compose 直出 raw position，未经 solver） | 80 | **72（90%）** | 0 | 0 |
| **corrupt**（native + 注入负象限浮动统计面板） | 80 | 72 | **80（100%）** | 0 |

- native 根因单一：`exclusive zone top-center has 2 components`（title+subtitle 同落 top-center；compose 管线写 raw position，不经 solver 时必触发）；
- 四版式计数完全相同——印证检查是 zone 计数模型，版式敏感性目前只存在于求解器侧（写入中间层设计约束）；
- COMPONENT_LINK_CYCLE 在 corpus 域恒 0（无 component_links 输入）——断环策略的测试域在构造 spec（单测覆盖）；
- P8「归零」的可度量定义：对同一批 spec 应用 P1 修复建议（change_anchor / 隐藏 / 钳制）后，native 72→0、corrupt OUTSIDE 80→0。

## 5. fallback 触发条件与频率（P0 第 3 项）

| 通道 | 触发条件 | corpus 域频率 |
|---|---|---|
| live chrome | `hasType('north_arrow'|'scale_bar')` 为假（resolveMapComponents ∪ dock 归属） | **0%**（20 案例 compose 产物必含 chrome 族） |
| export | 类型整体缺席才注入（enabled=false 视为用户显式关闭，不注入） | 同上 0% |
| render-observation | 镜像 live 规则，`fallback:true` 如实上报 | 同上 |
| 后端 include_chrome | 不注入（user-wins） | —（parity 缺口，见 §2） |

结论：fallback 的真实触发域是**未经 compose 管线的 spec**（用户/Agent 手写、外部导入）。P2 主动补全的目标域即此；命中率度量改为前端单测口径（构造无 chrome spec，断言补全后 `__fallback_*` 不再注入），corpus 域 0% 作为基线记档。

## 6. layout solver 能力边界（P0 第 4 项）

- **V2**（solve_component_layout）：确定性单遍 first-fit：requested → fallback_zones → ZONE_POSITIONS 邻接 → 溢出处置（optional 抑制 / required 保留+warning）；5 个 page profile。
- **V3**（solve_layout_v3）：V2 严格超集 + width_units 列单位、collision_group 组互斥、avoid_zones 内容避让、compact 收紧、conflicts + fallback_plan_zh 诊断。
- **共同边界**：全部是「选位」——冲突的终点是抑制/保留 + 告警，无自愈动作链；无断环；无决策工件。
- ComponentComposer（gis_harness）：选位=模板 position_zone 优先 + descriptor 默认位；约束支持仅槽位元数据（allowed types / max_count / exclusivity 由 composition_validation 裁决）；无几何约束、无画布感知。
- component_graph：图投影 + Kahn 检测环（不诬指下游），`topological_component_order` 遇环回退 (priority,id) 序渲染；**检测不修复**。

## 7. P0 结论（进入 P1 的依据）

1. P1 的修复建议器以 `plan_layout_repairs`（新建 component_composer.py）+ `solve_layout_v4`（layout_solver.py 增量）落地，动作词表 change_anchor / shrink / collapse_to_overflow / hide_lowest_priority；
2. 断环以 `break_component_cycles`（component_graph.py）落地：每环断开最低权重边（权重=端点 priority 和，平局 id 字典序），产出 remove_links 证据；
3. semantic_checks 仅动两个检查的建议段：LAYOUT_COLLISION warning + auto_safe 建议（quality_loop 不吃 warning，行为零回退）；COMPONENT_LINK_CYCLE fail + auto_with_semantic_risk 建议（断边是语义手术，需 explicit intent —— 与 quality_loop 现有语义严格对齐，不制造不可执行的 auto 通道）；
4. 版面描述中间层（CompositionDescriptor）需携带：autofill/fallback provenance、修复轨迹、版式 profile、margins、chrome 增益（numericScale/declination/graticule config）——作为 live/export/backend-SVG 三通道对账的单一语义源。
