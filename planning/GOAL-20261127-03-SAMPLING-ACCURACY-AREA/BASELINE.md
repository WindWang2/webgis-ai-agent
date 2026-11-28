# GOAL-20261127-03 BASELINE — 概率抽样调查、专题图精度核查与面积估计

- 执行时 base：`f34cb69dda6150ee4135e6d2a476708149d9eef7`（origin/master，2026-11-27 快照一致）
- 分支：`zcode/goal-probability-sampling-map-accuracy-20261127`
- worktree：`/home/kevin/projects/webgis/webgis-probability-sampling-map-accuracy-20261127`
- vendor/pi gitlink：`ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe`（Pi v1.6.1，2026-11-25，与 goal 参考一致）
- 执行日：2026-11-27。启动审计时开放 PR：无（`gh pr list --state open` = []）。开放 issue：无。
- 在研兄弟分支（本地未合并，本任务不依赖、不触碰）：
  - `zcode/goal-pi-adaptive-execution-20261127`（01 线，worktree webgis-pi-adaptive-execution-20261127）
  - `zcode/goal-moving-object-events-20261127`（02 线，本地分支）
- 近期合并相关 PR：#2272（02 科学方法实验）、#2286（任务数据供给）、#2285（多源联动探索 g46）、#2287（04 图符）、#2283/#2280（01 Pi host/governance）、#2277（参数化工作流）。均已并入 master 基线。

## 已核对的复用基线（生产调用链）

| 接缝 | 位置（base SHA） | 事实 |
|---|---|---|
| 空间抽样纯函数 | `app/lib/geo_analysis/spatial_sampling.py` | `random_points_in_polygons`（逐面 SRS，投影空间拒绝采样）、`systematic_grid_points`（随机起点格网）、`stratified_points_in_polygons`（stratum_field 属性分层，equal/proportional 面积权分配，大盘尼余数）。输出 FC 属性 polygon_index/sample_id/stratum。**不支持**：栅格分层源、纳入概率/权重、调查设计合同、估计 |
| Pi 抽样工具 | `app/tools/sampling_tools.py` | `sample_random_points`/`sample_systematic_grid`/`sample_stratified_points` 薄包装，descriptor 驱动 scientific_evidence，`ref_offload` 结果策略 |
| 像元全量评估 | `app/services/modelops/evaluation_service.py` | segmentation/classification/detection 全量混淆矩阵指标（`app/lib/modelops/evaluation.py`）、泄漏审计、分区/边界/漂移；16M 像元物化预算；owner_scope 恰好一维（session_id/project_id）；artifact 经 `publish_json_artifact` |
| 制图模型包 | `app/lib/cartography/model_packs/polygon_statistical.py:299` | `confusion_matrix_map` 仅是**表达模型**（图斑 from/to 双字段可视化），非统计估计 |
| 科学证据 | `app/lib/gis/scientific_evidence.py` + `app/lib/gis/algorithm_registry.py` | descriptor 驱动假设/局限/出处 |
| CAS DataObject | `app/services/lakehouse/data_object.py` | `publish_data_object(...)` 全 CAS 不可变对象 |
| durable jobs | `app/services/jobs/lifecycle.py`、G23 runner 模式 | kind String(20) 加性扩展 |
| 栅格读取 | `app/lib/geo_raster/reader.py` | `read_window` 受预算读、`read_full` 预算闸 |

## 本线唯一目标（区别于现状）

从**抽样框→概率设计→独立参考观测→调查状态→设计一致估计**的可证闭环：
加权误差矩阵（Olofsson 2014 good practices, DOI 10.1016/j.rse.2014.02.015）、
OA/UA/PA、类别面积估计与置信区间。无概率设计或无参考标签时**不得**把
描述性准确率伪装成总体精度（诚实标 not_evaluated）。

排除：遥感训练模型、重复像元全量 ModelOps 评估、重写空间抽样布点、
数据融合/供给、轨迹/发布/工程包（02/04/05 线所有权）。
