# feat(g3d03): 三维量测契约 V1 —— 地形剖面/LOS 通视/地表距离/地表面积 + 垂直基准治理（ADR-0274）

## 基线与重叠
- 基线 `origin/master` = `1115d683a5c8343e44d8a9777e4ce05e073c29e5`（2026-10-26）
- 开放 PR = 0；重叠矩阵见 `planning/g3d03-scientific-analysis/PR_OVERLAP_MATRIX.md`
  （近期合并 #1760 科学守门 / #1761 工具对账被**复用**而非重复；既有 viewshed 不动）

## 问题证据
全库此前**不存在**任何三维量测能力（grep 证据见 BASELINE.md）：地形剖面、两点
通视 LOS 与遮挡点、坡面/地表距离 vs 水平 vs 斜距、真实地表面积，以及**垂直基准
概念**（`vertical_datum` 全库零命中，`GISDatasetDescriptor` 无字段）—— 任何把
DEM 高程与外部高度来源静默混算的路径都是科学错误。

## 交付（详见 docs/adr/0274-3d-measurement-contract.md）
- `app/lib/geo_analysis/measurements.py`：4 个纯 NumPy 确定性算法
  （terrain.profile / terrain.line_of_sight / terrain.surface_distance /
  terrain.surface_area）+ `resolve_vertical_reference` 垂直基准治理
  （闭合词表 + 跨 datum 混算类型化拒绝 + ft 精确 0.3048 折算 + unknown 诚实披露）
- LOS：可选地球曲率/大气折射（R=6371008.8 m，k=0.1307，R_eff=R/(1−k)，
  bulge=s(D−s)/(2R_eff) 加地形侧）、首遮挡点、双向对称披露、nodata error/skip
- 4 个增量工具（同构既有 viewshed 模式：apply_contract → 护栏读取 → 算法 →
  科学证据块）；产物 GeoJSON（3D）走 dispatch 既有 ref 化 → MapSpec 图层通道
- 误差语义：半步加密收敛指示 delta_rel + 方法局限披露，不虚构解析误差界

## 数值验证（oracle 为独立手算解析值）
- 斜平面精确（surface=4·√1.25、area=(n−1)²·√1.5、ascent=s·L）
- 抛物线闭式 ∫√(1+4t²)dt = 64.991556 对照（弦和离散 ~0.11%，容差 rel=2e-3）
- LOS 曲率 vs 400k 点独立解析网格四场景（|Δmin_clearance|<8e-3 m）
- terrain.json oracle 216→229 cases（+13，manifest 同 PR regen）

## 测试与治理（本地，未等 CI）
23（lib 解析）+8（工具）+6（MapSpec 接线）新增；既有 terrain/gis 回归 852+
passed 零漂移；四工具 8 面对账 overall=pass；science ratchet/g02/corpus
pin/execution catalog/benchmark/quality 生成物账本全绿（计数钉扎 237→241
随注册表合法增长并注释）。≥12 组类型化科学负例（含垂直基准混算拒绝、超采样
RESOURCE_SCALE_MISMATCH、协作取消、nodata 政策）。完整命令与结果见
`planning/g3d03-scientific-analysis/EVIDENCE.md`。

## 残余限制与回滚
- 垂直基准换算（EGM96↔椭球）不内置（拒绝混算而非假装换算）；GeoTIFF 无 z
  元数据，datum/单位靠声明 + 披露
- 前端 3D 剖面/高亮消费留给 G3D02 方向（本 PR 交付 typed 通道并 schema 验证）
- 建筑高度/体积、净空扫描不做（无实证数据源，不虚构）
- 回滚：revert 本 PR 单个 merge commit 即恢复（纯 additive，无存量函数改动；
  仅 g02 计数钉扎与生成物需随 revert 同步回退）

## 独立评审
两轮独立 review 报告见 `planning/g3d03-scientific-analysis/INDEPENDENT_REVIEW.md`。
