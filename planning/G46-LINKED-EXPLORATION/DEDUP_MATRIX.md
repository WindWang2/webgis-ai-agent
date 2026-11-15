# DEDUP_MATRIX — G46（每项追到生产调用链）

| 要求 | 状态 | 依据 |
|---|---|---|
| 图表种类/浮动/停靠/折叠 | present | chart_kinds.py L109-193（19 kind）；chart-panel.tsx 七态 |
| chart→map 单类别发布（transient） | present | chart-panel.tsx L238-268 `handleSelectCategory` → publishSelection('select', selected_categories 恒单元素) |
| 图表卸载清同层 chart 选择 | present | chart-panel.tsx L210-217（单 current 语义下两图互顶 —— 本域新增并存条件 store，不动该行为） |
| SelectionStore 单 current + in/bbox 谓词 | present | selection-store.ts L105；不替换 |
| 服务端权威完整集合 handle（where scope 已存在） | present | selection_handles.py create_selection L323、scope `{kind:'where'}` |
| chart_panel 多实例 | present | component_registry.py L465-490 cardinality=multiple；目录无 instance 参数（options 承载） |
| typed where 闭合 AST + 校验 + 求值 | present | feature_pages.py L295-477 |
| 服务端聚合 where+bbox + 帽 | present | feature_aggregate.py L76-191；但 **other 桶无精确 typed 值集**（partial，本域自建分桶补齐，不改共享文件） |
| bbox 粗滤 + revision guard | present | layer.py L130-285 |
| 强版本冻结/ABA 防护 | present | bivariate snapshot.py freeze_source/check_anchor（直接 import） |
| 持久化 CAS 模式 | present | G38 模型 + 0107 迁移（照抄模式） |
| Pi 工具注册/dispatch/有界回执 | present | registry.py + _TOOL_MODULES |
| SVG 导出 layer.filter + chart_panel 渲染 | present | mapspec_to_svg.py L1962-1983/L941；**eq/ne null 语义与 MapLibre 不一致**（partial → 本域修正） |
| 三格式出版 | present | print_layout.py /export pdf\|svg\|png |
| paged-features where 下推 + total/truncated | present | paged-features.ts + layer.py（结果表复用） |
| **多作者条件并存（跨维度 AND/同维度 OR）** | missing | SelectionStore 单 current；无组合条件 store |
| **组合条件 → MapLibre 表达式精确编译（typed identity）** | missing | 现有 getSelectionFilterExpression 用 to-string（1/"1"/true 混同） |
| **服务端组合求值 + facet 统计（自排除口径）** | missing | 无此服务 |
| **数值区间图控件（between 条件 UI）** | missing | chart kinds 有 histogram 但无区间条件发布 |
| **exploration_chart_panel 组件（控件身份）** | missing | 目录无此类型 |
| **命名持久视图（保存/重开/失效/CAS）** | missing | 无 exploration_views 表 |
| **Pi inspect/preview/apply/save/open 工具** | missing | 无 linked_exploration_tools |
| **冻结导出（组合条件进图面 + 图表统计同范围）** | missing | 导出不消费组合条件 |
| E01-E34 验收场景 | missing | 全部待建 |

## 兄弟任务重叠

- G47 #2125：改 layer-style-panel.tsx（SymbolAssetPanel 区域）、hud.json、cartography-result-card.tsx —— 本域不碰 layer-style-panel；若同时改 hud.json/messages.ts 只做 additive 行，合流时解冲突。
- G42-G44/G48 已有 worktree 尚无 PR —— 无重叠（completion/goal repair、context receipt、字段合同、配准）。
