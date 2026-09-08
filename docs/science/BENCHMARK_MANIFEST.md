# Benchmark Manifest（自动生成 · science-v3 Wave 10）

> **本文件由注册表生成，请勿手工编辑。** 事实源：AlgorithmDescriptor 的
> 规模/精度/资源声明（complexity、approximation_class、resource_envelope、
> backend_variants、cancellation_profile、tolerance）。
> 再生成：`python scripts/gen_science_benchmark_manifest.py`。
>
> 口径：本 manifest 是**声明面**投影；运行时硬闸在实现层
> （ResourceScaleMismatch / RasterResourceGuard），benchmark 结构门消费
> 同一批声明。空字段 = 未声明（不构成承诺）。

统计：59/180 算法进入 heavy 清单（cpu/memory=high 或声明了资源/变体）。

| 算法 | 复杂度 | 精度 | 资源包络 | 变体(窗口) | 取消 | 容差 | 成本 cpu/mem | 执行策略 |
|---|---|---|---|---|---|---|---|---|
| `data.ingest.pipeline` | — | — | — | — | — | — | medium/high | ASYNC |
| `density.analytical.mixed` | — | approximate | — | — | — | — | high/medium | CELERY |
| `interpolation.block_kriging` | 块离散化 2×2 + OK 系统（−γ̄(B,B) 修正） | approximate | — | — | — | — | high/high | CELERY |
| `interpolation.cokriging` | 协同定位系统 O(m·(k+2)³)（MM1 近似） | approximate | — | — | — | — | high/high | CELERY |
| `interpolation.idw` | O(n log n + m·k)（cKDTree 邻域 k=5） | — | — | — | — | — | high/high | CELERY |
| `interpolation.indicator_kriging` | T × OK（T=阈值数 ≤20；概率面 T×H×W 内存线性放大） | exact | — | — | — | — | high/high | CELERY |
| `interpolation.kriging` | 拟合 O(N_fit²)（N_fit≤2000）+ 预测 O(m·(k+1)³)（k≤24, chunk 1024） | exact | — | numpy_batched(numpy,[8,100000],exact);scipy_linalg(scipy,[100001,500000],exact) | — | — | high/high | CELERY |
| `interpolation.model_compare` | Σ 方法 CV 预算走查（cv_budget 上限；固定确定性顺序） | — | — | — | — | — | high/medium | CELERY |
| `interpolation.natural_neighbor` | Delaunay O(n log n) + Sibson 面积裁剪/ Watson walk 逐格 | exact | — | — | — | — | high/medium | CELERY |
| `interpolation.rbf` | O(n³) 系统分解 + O(m·n) 求值（RBF_HARD_CAP 类型化拒绝） | exact | — | — | — | — | high/high | CELERY |
| `interpolation.regression_kriging` | OLS 趋势 + 残差 OK + 协变量 IDW 近似（目标栅格通道） | approximate | — | — | — | — | high/high | CELERY |
| `interpolation.universal_kriging` | OLS 趋势 O(n·d²) + 残差 OK（同 kriging 窗口） | exact | — | — | — | — | high/high | CELERY |
| `interpolation.variogram_selection` | 6 家族 × 有界拟合 + AICc 排名（样本 ≤2000） | — | — | — | — | — | high/medium | INLINE |
| `network.accessibility` | — | — | — | — | — | — | high/medium | ASYNC |
| `network.centrality` | — | — | — | exact_brandes(networkx,[,2000]);sampled_brandes(networkx,[2001,∞]) | — | — | high/high | ASYNC |
| `network.closest_facility` | — | — | — | — | — | — | high/medium | ASYNC |
| `network.gravity_access` | — | — | — | — | — | — | high/medium | ASYNC |
| `network.huff_interaction` | — | — | — | — | — | — | high/medium | ASYNC |
| `network.isochrone` | — | — | — | — | — | — | high/medium | ASYNC |
| `network.isochrone.local` | — | — | — | — | — | — | high/medium | ASYNC |
| `network.location_allocation` | — | — | — | — | — | — | high/medium | ASYNC |
| `network.mclp_exact` | NP-hard（MILP 分支定界，最坏指数）；模型 O(n+m) 变量、O(n+Σ|N_i|) 约束，规模由需求×候选乘积闸约束 | — | — | milp_highs(scipy,[,25000]) | — | — | high/high | ASYNC |
| `network.od_matrix` | — | — | — | — | — | — | high/medium | ASYNC |
| `network.optimize_route` | — | — | — | — | — | — | high/low | ASYNC |
| `network.pcenter_exact` | — | — | — | milp_highs(scipy,[,25000]) | — | — | high/high | ASYNC |
| `network.pmedian_exact` | — | — | — | milp_highs(scipy,[,25000]) | — | — | high/high | ASYNC |
| `network.route_optimization` | — | — | — | — | — | — | high/medium | ASYNC |
| `network.service_area.multi` | — | — | — | — | — | — | high/medium | ASYNC |
| `network.shortest_path` | — | — | — | — | — | — | high/medium | ASYNC |
| `point_pattern.cross_pcf` | — | — | — | — | — | — | high/medium | THREAD |
| `point_pattern.ripley_k_env` | — | — | — | — | — | — | medium/high | THREAD |
| `point_pattern.space_time_k` | — | — | — | — | — | — | high/medium | THREAD |
| `raster.algebra` | — | — | — | — | — | — | high/medium | THREAD |
| `raster.resample.grid` | — | — | — | — | — | — | high/medium | THREAD |
| `remote.change.raster` | — | — | — | — | — | — | high/medium | THREAD |
| `remote.ica` | — | — | — | — | — | — | high/high | THREAD |
| `remote.mad_change` | — | — | — | — | — | — | high/high | THREAD |
| `remote.mnf` | — | — | — | — | — | — | high/high | THREAD |
| `remote.ndvi` | — | — | — | — | — | — | medium/high | THREAD |
| `remote.pca` | — | — | — | — | — | — | high/high | THREAD |
| `sar.glcm_texture` | — | — | — | — | — | — | high/high | THREAD |
| `sar.multitemporal_speckle` | — | — | — | — | — | — | high/high | THREAD |
| `sar.speckle_filter` | — | — | — | — | — | — | high/high | THREAD |
| `spatial.gwr` | — | — | — | — | — | — | high/medium | THREAD |
| `spatial.hotspot.local` | — | — | — | — | — | — | high/medium | THREAD |
| `spatial.kde.contours` | O(N·grid) | approximate | — | — | — | — | high/high | CELERY |
| `spatial.kde.surface` | O(N·grid) | approximate | — | — | — | — | high/high | CELERY |
| `spatial.mgwr` | — | — | — | — | — | — | high/medium | THREAD |
| `spatial.sar_ml` | — | — | — | eigen_dense_symmetric(numpy,[,4000]) | — | — | high/medium | THREAD |
| `spatial.sem_ml` | — | — | — | — | — | — | high/medium | THREAD |
| `stats.h3_hotspot` | — | — | — | — | — | — | high/medium | THREAD |
| `stats.h3_lisa` | — | — | — | — | — | — | high/medium | THREAD |
| `stats.local_geary` | — | — | — | — | — | — | high/medium | THREAD |
| `stats.st_dbscan` | — | — | — | — | — | — | high/medium | THREAD |
| `temporal.hotspot` | — | — | — | — | — | — | high/medium | THREAD |
| `terrain.aspect` | — | — | — | — | — | — | medium/high | THREAD |
| `terrain.hillshade` | — | — | — | — | — | — | medium/high | THREAD |
| `terrain.sink_fill` | O(N log N) | — | — | — | — | — | medium/high | THREAD |
| `terrain.slope` | — | — | — | — | — | — | medium/high | THREAD |
