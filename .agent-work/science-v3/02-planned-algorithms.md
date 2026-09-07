# 02 Planned Algorithms（V3 增强计划 · 逐域审计汇总）

> 来源：8 份域审计的 §8 建议清单。原则：成熟可验证方法 native 化；
> 依赖缺失（statsmodels/mgwr/spreg/pykrige/gstools/scikit-image/pulp）的方法
> 一律 numpy/scipy/libpysal/esda native 实现；每项带真实方法引用与测试策略。

## 已实现（本分支，见 git log）

- Backend SDK V3（Wave 1）：ResourceEnvelope/ApproximationClass/NumericalTolerance/
  CancellationProfile/BackendEvidence —— 非算法但为下列算法声明变体提供契约位。
- 修复包：indicator 守卫 + typed uncertainty（geostat F1/F2）、openness/horizon
  内存重构 + 水文组合链 + D8 引用 + viewshed R3 引用（terrain P0）、
  strict 波段语义（optical HIGH）。
- 进行中（实现 agent）：GWR/MGWR 局地 SE+t、FCLS 线性解混、NDWI 拆名、
  MAD χ² dof 修正、空间统计接线修复。

## 待实现（按优先级）

### P1（本分支目标内）
| 域 | 项 | 方法引用 | 依赖可行性 |
|---|---|---|---|
| 地统计 | OK/UK prediction interval 面 + LOOCV z-score 校准指标 | Isaaks & Srivastava 1989 | 高（stddev 已一等产物） |
| 地统计 | 各向异性自动拟合（directional scan → 几何椭圆喂 OK/UK） | Webster & Oliver 2007 | 高（directional_variogram 已有） |
| 地统计 | robust variogram（Cressie-Hawkins/Dowd）opt-in | Cressie & Hawkins 1980; Dowd 1984 | 高（逐 bin 归约替换） |
| 点格局 | Emerging Hotspot Analysis（用已有 Gi*+Mann-Kendall 组装，顺带修复 temporal.hotspot 语义失配） | Nelson et al.; ESRI EHA 语义 | 高 |
| 点格局 | 多变量 local Geary（esda.Geary_Local_MV 委托）；bivariate local Moran 收尾 | Getis-Ord; Anselin | 高（esda 2.9） |
| 网络 | MCLP（MILP，scipy HiGHS）+ church_revelle1974 归位 | Church & ReVelle 1974 | 高（<150 行） |
| 网络 | 启发式族（LA/accessibility/gravity/huff）统一 n×m 规模闸 | — | 高 |
| SAR | vh_ratio 文案契约修正 + acquisition_comparability 守卫接线 | — | 高 |
| SAR | ENL 置信区间 + coherence Fisher-z CI | — | 中 |
| 光学 | QA/SCL 位解码契约；threshold_change 工具面暴露 | — | 高 |
| 地形 | breaching（Lindsay 2010 hybrid） | Lindsay 2010 J.Hydrol | 中（Dijkstra on grid） |
| 地形 | HAND（Gharari 2013） | Gharari et al. 2013 HESS | 中高 |
| 地形 | D8/D∞ parity 属性测试套件 | — | 高（无新实现） |
| 统计 | spatial block CV 推广到回归族 | Roberts et al. 2017 | 高 |

### P2/P3（记录，后续分支）
- 地统计：log/normal-score 变换、KED、nested variogram、indicator 单调化、
  SGS 条件模拟（caller_seeded）、全 CoK（MM2/LMC）、时空克里金 foundation。
- 地形：Shreve 链级、流域层级（Pfafstetter）、hypsometry/Horton 比、
  solar radiation（Fu & Rich 2002，approximate）、R3 viewshed 精化、多尺度 TPI/openness。
- 网络：容量约束选址、turn penalty 契约化、accessibility inequality、
  E2SFCA 变体、multimodal/turn-restriction 契约、β 标定。
- 光学：medoid 合成、PCA 增量 + 符号约定、tasseled cap ETM+/MSS、
  red_edge 角色消费者、indices 扩展。
- 统计：SDM/SAC（descriptor 如实 planned 起步）、MGWR AICc 选带宽、
  hotspot 时间演化。
- SAR：极化 RVI、Quegan 谱域滤波、ray-casting 阴影、热噪声 ESA IPF 引用。
- 点格局：K 族 DCLF+BH、多色 join count、K_st 时间平移边缘校正、
  geodetector q 最优分层。

## 明确不做（非目标）
ML 训练基础设施、深度学习分割/检测平台重写、GPU 专有路径（无环境依据）、
为数量注册 fake-native 算法。
