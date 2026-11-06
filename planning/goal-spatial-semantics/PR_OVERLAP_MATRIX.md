# PR_OVERLAP_MATRIX — GOAL 01 空间语义契约

基线 master@6c5e71c2c。open PR：无（2026-11-01 查证）。open issues：S 系列
审查汇总 + #1809-#1847 High 单项，均与本方向无直接重叠（#1841-#1843 为既有
安全高风险，本任务不复制其信任边界，也不在范围内修复）。

| 既有 PR | 主题 | 与本任务关系 | 策略 |
|---|---|---|---|
| #1868 (c777c5962) sdo 数据准备 | inspect→propose→prepare→bind + SpatialInspectionReceipt | **extend**：receipt 的 crs_source 四态/axis_order_guessed 词表是新契约输入；不重写其内部 | 复用词表与函数；不改 data_preparation/ 内部 |
| #1869 (4b6fc72fc) goal-02 分析工作流 | AnalysisWorkflowContract 薄编排层 | **reuse**：envelope.crs/units、UNIT_VOCABULARY、metric_crs_policy 是组合面先例 | 只引用；spatial_semantics 契约可被其消费但不改其代码 |
| #1870 (9b7fb521b) carto-design | 表达模型候选/决策 trace/语义检查 | **avoid 重写**；MapSpec schema 我不做第二套 | 仅在工具适配层把单位证据接到既有 legend_spec["unit"] 通道 |
| #1871 图册编排 / #1873 要素编辑 / #1874 地图操作事务 / #1875 linked views | 各自域 | **avoid**：不触碰其目录 | 无文件交集（除 app/tools/__init__.py 单行增量——各 PR 均为追加模式，冲突面 1 行） |
| #1760 G02 科学方法契约 | MethodUseContract（静态算法目录） | **extend**：新 SpatialSemanticContract 是其实例层兄弟（数据×操作×执行），组合不重叠 | MethodUseContract schema v1 不动；新契约引用 algorithm_id/crs_class |
| #1761 G03 工具能力对账 | 8 面对账引擎 | **reuse**：conformance 检查器会扫工具声明；新工具注册时给出完整声明面 | 新工具 unit_semantics/capabilities 按规范声明 |
| #1771/#1835 g3d01/a DEM 供给 / #1773 g3d03 三维量测 / #1830 g3d05 | 3D 域 | **avoid**：3D 有自己的垂直基准治理（ADR-0274） | 不碰 scene3d/ |
| #1836/#1834 g3dv2-b/a | 3D 场景运行时 | **avoid** | 无交集 |

## 新 PR 交叠复核时点
- [x] M0 开始前（2026-11-01，本文档）
- [ ] M4 工具接线前
- [ ] 提交 PR 前（A5 步骤 4）

## 共享文件触碰面（最小粒度）
- `app/tools/__init__.py`：+2 行（spatial_semantics_tools 注册）——追加模式。
- `app/tools/categories.py`：+1 行模块分类——追加模式。
- `app/tools/spatial.py` / `advanced_spatial.py`：适配层内嵌（无签名变更）。
- `app/services/geocompute/ops.py`：G2 修复 1 处（crs member 保留）。
- `app/tools/cartography.py`：1 处 crs 硬编码改真实值。
- frontend：graduated 图例 unit 显示（组件内最小改动）。
