# G3D03 PR 重叠矩阵（2026-10-26，开放 PR = 0）

无未合并 PR，重叠风险全部来自**已合并 master**。逐项核对（重叠 → 复用策略）：

| 已合并 PR | 内容 | 与本方向关系 | 决策 |
| --- | --- | --- | --- |
| #1760 科学守门 | MethodUseContract、三段守门、oracle | 新工具必须走同一路径 | **复用**：descriptor + parameter_contracts + build_evidence + science_contract_v4 ratchet |
| #1761 工具对账 | 8 面对账引擎 | 新工具自动纳入审计 | **复用**：新工具走既有注册面；本地跑 `scripts/check_tool_capability_conformance.py` 验证 |
| #1762 任务级验收 | 21 场景 dispatch 语料 | 本方向不新增任务场景 | 只读参考 |
| #1759 数据集资格 | role binding、preflight | DEM 资格归 G3D01/资格层 | 不动资格判定；测量工具只消费已验证栅格（`validate_data_path` + guard） |
| #1755/#1756 导出/Pi 契约 | MapSpec 导出、Pi 结果契约 | 工具结果形状须兼容 | 输出 FeatureCollection（dispatch 自动 ref 化）+ 标量统计，沿用 inline_small 政策 |
| #1622/#1623 RenderWork/Interaction IR | 渲染执行信封、交互事件 | M5 只到 MapSpec 图层，不碰渲染 | 图层经既有 converter；不建第二渲染状态 |
| #1617 语义缩放 | RepresentationAtScale | 不触碰 | — |
| #1758 issue 清偿 | 93 项盘点 | 无 terrain 量测相关 | — |
| #1672 deep-review 清偿 | 41 项 | 无重叠 | — |

**结论**：无开放/近期合并 PR 实现「地形剖面 / LOS 通视线 / 坡面距离 / 地表面积 / 垂直基准治理」；
已有 viewshed 不重写（仅消费其存在性）。G3D01（DEM 资格）与 G3D02（场景渲染）worktree 并行存在，
本方向不侵入其模块（`app/services/gis_harness/data_qualification.py`、`frontend/components/map/`、
scene/tiles 相关）—— 接口边界：schema 工具输出（GeoJSON/标量 + 证据块）。
