# 06 Uncertainty Gaps（不确定性缺口汇总）

> 词表（uncertainty.py，封闭）：scalar_uncertainty / field_uncertainty /
> raster_uncertainty / statistical_significance / sensitivity_envelope /
> validation_metrics / monte_carlo_summary / approximation_disclosure（V3 新增）。
> 类型化块体系完备（UncertaintyMeasure/measures/p 值/多重校正/分位数）。

## 体系性缺口与处置

| 缺口 | 处置 |
|---|---|
| declared uncertainty → 实际产出 无机器校验（基建 G1） | ✅ Wave 8：uncertainty_producer_tests 映射 + validate() AST 校验；kriging 已接线种子 |
| 近似后端额外不确定性无披露槽位 | ✅ Wave 1/8：approximation_disclosure 词表 + ApproximationDisclosure 块 + backend 出窗/非 exact 披露 |
| 声明了但工具不产出（hotspot/h3 族、indicator→已修、GWR sensitivity_envelope） | 🔧 分域 agent 修复中；原则：要么真实产出 typed 块，要么收窄声明 |
| 全域 uncertainty_outputs=[]（地形 26 个、光学 RS 34 个） | ⏳：DEM 垂直 RMSE 误差传播、ENL CI、Fisher-z CI 等入 02-planned P1/P2 |

## 域级要点

- **地统计**（最佳实践）：kriging 方差为一等产物；IDW 诚实「无理论方差，
  LOOCV 残差分位数」；RK 三处一致披露 rk_variance=残差克里金方差。
  缺口：prediction interval 面、z-score 校准指标（P1）、变异函数参数 CI（P2）。
- **统计**：置换 p 值 + BH-FDR 校正完备（Gi*/LISA）；GWR/MGWR 缺逐系数
  局地 SE/t（🔧 进行中）；join count 抽样模型披露已更正。
- **点格局**：seed=42 置换契约逐位钉住；per-radius K p 值缺 FDR/BH、
  multiple_testing 字段全域空串（P1）。
- **网络**：可达性不确定性天然低；MILP gap 未报告（HiGHS exact → gap=0
  可声明）；heuristic 解无 optimality gap 披露（P1：加 gap 字段）。
- **SAR**：ENL 无 CI、coherence 无 Fisher-z CI（P1）；intensity/amplitude、
  linear/dB、calibrated/uncalibrated 四区分的实现-披露一致性为全仓最佳之一。
- **光学**：RS 域 0 个 uncertainty_outputs；严格说光学指数是确定性闭式
  波段运算（诚实为空），真缺口在输入质量（反射率值域校验 ⏳）与
  云/掩膜语义（QA 位解码 ⏳）。
- **地形**：DEM 误差传播（slope/curvature/TWI 的 MC 或解析传播）为最大
  科学缺口（P2）；方位离散不确定度可解析（≤360/N）未输出。

## Honesty 原则（对齐 Goal 8）

- 不把 IDW interpolation error 冒充 kriging variance —— 已由审计确认实现遵守。
- 不把 residual kriging variance 冒充 total predictive uncertainty —— RK 三处披露+专项测试。
- 协变量来自近似插值必须披露 —— RK 的 IDW 协变量路径已披露 approximate=True。
