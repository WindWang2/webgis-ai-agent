# GOAL-20261127-03 DEDUP MATRIX — 既有能力 vs 本任务增量（base f34cb69dd）

判定口径：`present`=生产链已有且满足本线验收；`partial`=存在但不满足验收、
需兼容扩展；`missing`=不存在；`owned-by-other`=兄弟任务线所有权。

| # | 能力 | 状态 | 证据（生产调用链） | 本线动作 |
|---|---|---|---|---|
| 1 | 面内随机/系统/分层布点（点生产） | present | `app/lib/geo_analysis/spatial_sampling.py`（SRS/系统/分层 equal·proportional）；`app/tools/sampling_tools.py` Pi 工具 | 只读复用其投影拒绝采样思路；新设计用**栅格分层框**，不重写该模块 |
| 2 | 像元全量混淆矩阵评估 | present | `app/services/modelops/evaluation_service.py` + `app/lib/modelops/evaluation.py` | 保持不动，仅作独立对照（WP4 对照路径） |
| 3 | 混淆矩阵专题表达模型 | present | `app/lib/cartography/model_packs/polygon_statistical.py:299` confusion_matrix_map | 复用表达惯例；估计表/图走既有 chart/MapSpec |
| 4 | 抽样调查设计合同（总体/框/层/单位/纳入概率/权重/digest） | missing | 检索 inclusion_probability / accuracy_assessment / area_adjusted 无生产命中 | 新建 `app/services/survey_sampling/`（WP1） |
| 5 | 栅格分类图分层抽样框（层面积等面积/测地、NoData/掩膜处理） | missing | spatial_sampling 只吃矢量面属性分层；无等面积面积计算 | 新建（WP2） |
| 6 | 可复现分层选样（不放回、per-stratum n_h、纳入概率π、权重 w=1/π、设计 digest） | missing | 无 | 新建（WP2） |
| 7 | 参考标签调查记录（状态机：未访问/待核查/已标注/争议/不可判/拒绝；盲标；预测≠真值） | missing | 无 | 新建（WP3） |
| 8 | 设计一致加权误差矩阵 + OA/UA/PA + 面积估计 + CI（Olofsson 2014） | missing | 无 | 新建（WP4） |
| 9 | oracle/全查方差归零/合成重复抽样覆盖检查 | missing | 无 | 新建（WP4） |
| 10 | Pi 工具 design/preview/sample/validate_reference/estimate/map | missing | 无 survey_* 工具 | 新建（WP5） |
| 11 | 调查工作台 UI（设计参数、样点状态、地图选点、矩阵/区间表） | missing | 无 | 新建（WP5） |
| 12 | 栅格取值采样（probe/profile/站位长表） | present（G39） | `planning/G39-RASTER-SAMPLING-PRODUCTS`（已交付） | 不同域（取值 vs 调查估计）；不触碰 |
| 13 | 科学方法实验（#2272）、任务数据供给（#2286）、多源联动（#2285）、图符（#2287）、参数化工作流（#2277） | owned-by-other | 已并入 master | 仅最小接线复用，不重写 |
| 14 | Pi host/provider 层变更 | owned-by-other（01 线独占） | goal 文件 §2 | 本线只消费现有运行能力，不改 vendor/pi |

## 探查线索澄清

- `area_adjusted`/`inclusion_probability`/`accuracy_assessment` 在 app/ 零命中
  （仅 polygon_statistical 别名 `accuracy_map` 为表达模型）→ 与 goal §4 陈述一致。
- 目录名零命中不证明功能不存在；上表逐条以生产调用链证据判定。
