# G24 DUPLICATION_MATRIX — 既有能力 vs 本方向增量

BASE `b8f2a6ecc`。原则：master 已有能力只复用 + 回归证明，不重建同名模块。

| 能力 | 现状证据（master） | owner/PR | 本任务剩余增量 | 共享接口 | 明确不做 |
|---|---|---|---|---|---|
| 单击权威回填（MVT） | `use-feature-selection.ts:134` + `/feature/{fid}` + `id_mode=positional` | #667/#668/Track B（已合并） | — （复用；mvt-pick 复用其回查语义） | `/layers/data/{ref}/feature/{fid}` | 不改回填语义 |
| 分页属性表 | `/features` 端点 + `paged-table-data.ts` | W12/Track B vNext | 选中集视图（handle 分页）—— 本 PR | `fetchFeaturePage` 同构 | 不重做排序/where |
| typed where AST / 聚合 | `feature_pages.py` / `feature_aggregate.py` | #2074 前序 | — | `parse_where_param` 校验复用 | 不扩谓词词表 |
| 框选有界投影 | `brush-select.ts` + SW-02（#2004 已修） | ADR-0091 | 截断→服务端句柄（本 PR） | `projectBrushHits` 不变 | 不吞 #2064 通用修复 |
| G14 编辑事务/undo/lineage | feature_edit 服务族 + REST + `vector_*` 工具 | #2075 | 编辑手势对 MVT 的拾取接线（本 PR）+ ambiguous_target 防线 + selection lineage 消费 | preview/decision/undo/lineage 不变 | 不改 CAS/journal/拓扑 |
| G16 标注 | label authoring（featureId str 寻址） | #2077 | — （其 ADR 预留的「ref 源权威复核」消费侧缺口由本方向 pick_detail/身份链统一间接弥合；标注代码不动） | FEATURE_ID_KEYS 同链 | 不动 label 布局器 |
| G19 字段计算 selection scope | `ScopeSpec{selection_ref,…}` 校验完整/revision | #2074 | materialize 端点提供可靠 selection_ref（本 PR，测试钉） | `field-compute/plan` | 不改 ScopeSpec |
| G18 导出/交付 | data_delivery 工具族 | #2080 | —（选择集导出经 materialize/分页即可达，未新增 UI） | materialize ref | 不新建导出通道 |
| Pi 原生发现/嵌套 | ToolRegistry tool_search/codemode | #2081 | 两个新工具按既有 tier/side-effect 规范注册 | `@tool` 装饰器 | 不动 Pi vendor（G22） |
| 3D 拾取 / query_map_features | pickScene3d / 渲染面命令 | G3D02 / goal03 | — （保持渲染面事实语义） | — | 不改其语义 |
| OGC/fabric 瓦片代理 | ogc_service_tools / tile_service | GOAL-02 | —（readonly 远程瓦片限制：无 ref 即无权威回查 → 本方向不改其行为，A27 由「无 ref 不进编辑通道」边界覆盖） | — | 不做 fabric 句柄 |

## 重复实现检查结论

- 无同名「selection handle」既有实现（搜索 `selection_handle/opaque/tile_pick` 零命中，调查员报告 C16）；
- `mvt-pick` 与 `use-feature-selection` 回查语义一致但角色不同（编辑手势 vs 点选快照），共享 `feature/{fid}` 端点，未复制端点逻辑；
- `selection-table-data` 与 `paged-table-data` 并存（后者管全图层页、前者管句柄页），接口同构、错误分类各自 typed —— reviewer 关注点已登记（见 REVIEW.md）。
