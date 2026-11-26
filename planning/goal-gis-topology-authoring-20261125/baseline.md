# Baseline — goal-gis-topology-authoring-20261125

## 基线版本
- origin/master SHA: `8fe3d76634bfb7052f438325c7db3e0ef3dad040`（2026-11-25/26 fetch 确认）
- HEAD 合并：#2266（G21 有向 OD）
- 本任务分支：`zcode/goal-gis-topology-authoring-20261125`，相邻独立 worktree `../webgis-wt-gis-topology-authoring-20261125`

## 开放 PR 避让矩阵（2026-11-26 核查）
| PR | 分支 | 主题 | 与本域交集 |
|---|---|---|---|
| #2272 | feat/gis-science-method-lab-20261124 | GIS 科学方法实验引擎（02方向） | 无域文件交集；共享接线 app/main.py、app/tools/__init__.py、page.tsx、i18n methodExperiment.json |
| #2273 | feat/gis-trusted-data-fusion-20261124 | 多源实体匹配/融合（03方向） | 无域文件交集；共享 app/main.py、app/models/__init__.py、app/tools/__init__.py、i18n dataFusion.json |
| #2274 | feat/carto-multiscale-generalization-20261124 | 多尺度综合（04方向） | 无域文件交集；共享 app/tools/__init__.py、i18n map.json |
| #2275 | feat/contextual-gis-scenario-harness-20261124 | 情境多方案推演（05方向） | 无域文件交集；共享 app/main.py、app/tools/__init__.py、i18n workbench.json |
| #2276 | zcode/goal-pi-native-gis-host-20261125 | 原生 Pi host/GIS runtime | 无文件交集（本分支不改 Pi vendor/bridge） |
| #2277 | zcode/goal-gis-model-workflow-authoring-20261125 | 可视化模型作者工作流 | 无文件交集；共享 main.py/models/tools 接线，本分支未触碰 |
| #2278 | zcode/goal-carto-design-series-20261125 | 可复用设计规范/地图系列 | 无文件交集；共享 main.py/tools 接线，本分支未触碰 |
| #2267–2271 | dependabot | 依赖升级 | 无域交集 |

**结论**：7 个开放功能 PR（#2272–#2278）均不实现拓扑约束编辑/共享边编辑/线网辅助数字化，且与本分支最终 diff 无文件交集。origin/master 最终 fetch 仍为 `8fe3d7663`，无漂移。
**共享接线策略**：
- 工具尽量留在既有 `feature_edit_tools.py` / `topology_workbench_tools.py`（不新增模块文件 → 免改 `app/tools/__init__.py`）
- 若必须新增 route 文件 → app/main.py 仅加一行 include
- i18n 新键放独立命名空间 JSON（如 `topologyEditing.json`）→ 免改共享 map.json/chat.json

## 近期合并 PR 中本域相关
- #2167 (G44)：字段契约已挂入 feature_edit（attr_ops/executor/contracts：contract_violation 等码）——必须复用，不重写
- #2168 (G45)：coordinate_operations 域（派生矢量重投影）——仅通过既有 API
- #2121 (G38)：data_join_authoring 域——不触碰
- #2266 (G21)：flow 域——不触碰
- #2253/#2245：Pi bridge/运行时对齐——不触碰 Pi vendor/bridge

## 已有能力（master 实证）
- `app/services/feature_edit/`（G14）：封闭 op 词表 set_fields/rename_field/repair_geometry/move_by/snap_to_ref/create_features/delete_features/replace_geometry/split_feature/merge_features；plan 状态机 pending/applied/rejected/expired/conflicted（TTL 600s，≤8 活跃）；journal undo/redo（128 条 FIFO、64KB/entry 预算）；policy confirm_all/allow_safe_attribute（默认 confirm_all）；_wfid 稳定身份 + FEATURE_ID_KEYS 链；数据集级 topology 声明（no_overlap/no_self_intersection/no_duplicate_points，容差默认 1m）
- `app/services/topology_workbench/`（G31）：规则 polygons_no_overlap/area_coverage/objects_covered_by_target/cross_layer_no_overlap/line_endpoints_connected；角色绑定 required+scoped/required+complete；快照预算（50k 要素、200k 候选对、5k findings、120s/规则）；locator（wfid/natural_id/snapshot_index）；witness blob；修复/例外流
- `app/services/feature_proposals/`：提案生命周期（审查/批准/rebase/冲突）
- G44 字段契约在编辑写入链上统一执行

## 缺口（本任务对象，待 recon agent 报告后细化）
- 共享边/邻接结构索引（跨要素临时拓扑）：未见
- 共边顶点移动/插入/删除的邻面同步：未见（replace_geometry 是单要素语义）
- 线网受控连接/noding/trace 辅助：未见（无网络编辑算子）
- 跨要素 impact plan（选中对象→受影响邻接对象→前后摘要）：部分（plan 面向单数据集 targets，无邻接传播）
- UI 拓扑编辑工作台：待 recon
- Pi 侧约束编辑工具：待 recon

## 特征化（当前行为）
待 recon agent 返回后补充可复现的当前行为描述与测试基线。
