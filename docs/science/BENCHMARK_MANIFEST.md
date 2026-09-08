# Benchmark Manifest（自动生成 · science-v3 Wave 10）

> **本文件由注册表生成，请勿手工编辑。** 事实源：AlgorithmDescriptor 的
> 规模/精度/资源声明（complexity、approximation_class、resource_envelope、
> backend_variants、cancellation_profile、tolerance）。
> 再生成：`python scripts/gen_science_benchmark_manifest.py`。
>
> 口径：本 manifest 是**声明面**投影；运行时硬闸在实现层
> （ResourceScaleMismatch / RasterResourceGuard），benchmark 结构门消费
> 同一批声明。空字段 = 未声明（不构成承诺）。

统计：97/192 算法进入 heavy 清单（cpu/memory=high 或声明了资源/变体）。

| 算法 | 复杂度 | 精度 | 资源包络 | 变体(窗口) | 取消 | 容差 | 成本 cpu/mem | 执行策略 |
|---|---|---|---|---|---|---|---|---|
| `data.ingest.pipeline` | — | — | — | — | — | — | medium/high | ASYNC |
| `density.analytical.mixed` | — | approximate | — | — | — | — | high/medium | CELERY |
| `interpolation.block_kriging` | 块离散化 2×2 + OK 系统（−γ̄(B,B) 修正） | approximate | 24B/feat pairs≤200000 feat≤500000 | — | chunk_boundary | rtol=0.001,atol=1e-09 | high/high | CELERY |
| `interpolation.cokriging` | 协同定位系统 O(m·(k+2)³)（MM1 近似） | approximate | 24B/feat pairs≤200000 feat≤500000 | — | chunk_boundary | rtol=1e-06,atol=1e-09 | high/high | CELERY |
| `interpolation.cokriging_lmc` | 逐目标 (k1+k2+2)³ 系统求解 + LMC 拟合 O(N_fit²) | exact | 32B/feat feat≤500000 | — | chunk_boundary | rtol=1e-09,atol=0 | high/high | CELERY |
| `interpolation.dasymetric` | — | — | 128B/feat | — | none | rtol=1e-06,atol=1e-06 | medium/medium | THREAD |
| `interpolation.directional_variogram` | O(pair_budget)（入口确定性分层抽稀 ≤2000 + 行步幅 20 万对上限） | — | pairs≤200000 feat≤2000 | — | chunk_boundary | rtol=1e-09,atol=0 | medium/low | INLINE |
| `interpolation.external_drift_kriging` | 同 UK：拟合 O(N_fit²) + 预测 O(m·(k+3)³)（漂移约束 ×2 乘子） | exact | 32B/feat pairs≤200000 feat≤500000 | — | chunk_boundary | rtol=1e-06,atol=1e-09 | high/high | CELERY |
| `interpolation.idw` | O(n log n + m·k)（cKDTree 邻域 k=5） | — | 24B/feat cells≤1500000 | — | coarse | rtol=1e-06,atol=1e-09 | high/high | CELERY |
| `interpolation.indicator_kriging` | T × OK（T=阈值数 ≤20；概率面 T×H×W 内存线性放大） | exact | 24B/feat pairs≤200000 feat≤500000 | — | chunk_boundary | rtol=1e-06,atol=1e-09 | high/high | CELERY |
| `interpolation.kriging` | 拟合 O(N_fit²)（N_fit≤2000）+ 预测 O(m·(k+1)³)（k≤24, chunk 1024） | exact | 24B/feat pairs≤200000 feat≤500000 | numpy_batched(numpy,[8,100000],exact);scipy_linalg(scipy,[100001,500000],exact) | chunk_boundary | rtol=1e-06,atol=1e-09 | high/high | CELERY |
| `interpolation.model_compare` | Σ 方法 CV 预算走查（cv_budget 上限；固定确定性顺序） | — | 32B/feat | — | none | rtol=1e-09,atol=0 | high/medium | CELERY |
| `interpolation.natural_neighbor` | Delaunay O(n log n) + Sibson 面积裁剪/ Watson walk 逐格 | exact | 8B/cell feat≤200000 cells≤4000000 | — | none | rtol=1e-06,atol=1e-09 | high/medium | CELERY |
| `interpolation.nearest_neighbor` | O(n log n + m)（cKDTree k=1 分段常值场） | exact | 8B/cell feat≤200000 cells≤4000000 | — | coarse | rtol=1e-12,atol=0 | low/medium | CELERY |
| `interpolation.rbf` | O(n³) 系统分解 + O(m·n) 求值（RBF_HARD_CAP 类型化拒绝） | exact | 8B/feat feat≤100000 | — | none | rtol=1e-06,atol=1e-09 | high/high | CELERY |
| `interpolation.regression_kriging` | OLS 趋势 + 残差 OK + 协变量 IDW 近似（目标栅格通道） | approximate | 24B/feat pairs≤200000 feat≤500000 | — | chunk_boundary | rtol=1e-06,atol=1e-09 | high/high | CELERY |
| `interpolation.sgs` | O(R·N·k³)（R=实现数、N=格点、k≤24；R×N 预算硬顶 2000 万） | sampling | cells≤20000000 | — | chunk_boundary | rtol=1e-09,atol=0 | high/high | CELERY |
| `interpolation.simple_kriging` | 同 OK：拟合 O(N_fit²) + 预测 O(m·(k+1)³)（协方差形式，无约束行） | exact | 24B/feat pairs≤200000 feat≤500000 | — | chunk_boundary | rtol=1e-06,atol=1e-09 | high/high | CELERY |
| `interpolation.st_kriging` | 逐目标 (k+1)³ 时空系统 + 空间 cKDTree × 时间窗邻域 | exact | 40B/feat feat≤300000 | — | chunk_boundary | rtol=1e-09,atol=0 | high/high | CELERY |
| `interpolation.tin` | Delaunay O(n log n) + 逐格重心定位（>20 万样本类型化拒绝） | exact | 32B/feat feat≤200000 | — | none | rtol=1e-09,atol=0 | medium/medium | CELERY |
| `interpolation.trend_surface` | O(n·d²)（单位盒缩放 OLS，d=多项式项数） | exact | 64B/feat | — | none | rtol=1e-09,atol=1e-12 | low/low | INLINE |
| `interpolation.universal_kriging` | OLS 趋势 O(n·d²) + 残差 OK（同 kriging 窗口） | exact | 24B/feat pairs≤200000 feat≤500000 | — | chunk_boundary | rtol=1e-06,atol=1e-09 | high/high | CELERY |
| `interpolation.variogram_selection` | 6 家族 × 有界拟合 + AICc 排名（样本 ≤2000） | — | pairs≤200000 feat≤2000 | — | chunk_boundary | rtol=1e-09,atol=0 | high/medium | INLINE |
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
| `terrain.aspect` | — | — | 40B/cell | — | none | rtol=1e-06,atol=1e-09 | medium/high | THREAD |
| `terrain.breach` | O(N log N)（priority-flood ×2 + 逐洼地路径切沟） | approximate | 32B/cell cells≤50000000 | — | chunk_boundary | rtol=1e-09,atol=0 | medium/high | — |
| `terrain.contours` | — | — | 16B/cell | — | none | rtol=1e-06,atol=1e-09 | low/low | INLINE |
| `terrain.curvature` | — | — | 48B/cell | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.dinf_flow` | O(N log N) | — | 40B/cell cells≤50000000 | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.flow` | — | — | 48B/cell | — | none | rtol=1e-12,atol=0 | medium/medium | THREAD |
| `terrain.flow_length` | O(N log N) | — | 32B/cell cells≤50000000 | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.geomorphons` | — | — | 24B/cell cells≤50000000 | — | none | rtol=1e-12,atol=0 | medium/medium | THREAD |
| `terrain.hand` | O(N log N)（fill + d8 + accum + 单遍逆拓扑） | exact | 40B/cell cells≤50000000 | — | chunk_boundary | rtol=1e-12,atol=0 | medium/high | — |
| `terrain.hillshade` | — | — | 40B/cell | — | none | rtol=1e-06,atol=1e-09 | medium/high | THREAD |
| `terrain.hillshade_multi` | — | — | 56B/cell cells≤50000000 | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.horizon_angle` | — | — | 24B/cell cells≤50000000 | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.hypsometry` | O(N)（确定性直方） | approximate | 8B/cell | — | coarse | rtol=1e-09,atol=0 | low/low | — |
| `terrain.landform` | — | — | 32B/cell cells≤50000000 | — | none | rtol=1e-12,atol=0 | medium/medium | THREAD |
| `terrain.ls_factor` | — | — | 24B/cell | — | none | rtol=1e-06,atol=1e-09 | low/low | INLINE |
| `terrain.morphometry` | — | — | 40B/cell cells≤50000000 | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.openness` | — | — | 32B/cell cells≤50000000 | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.pfafstetter` | O(S log S)（干流上溯 + 支流归属 BFS，S=河网像元） | exact | 24B/cell cells≤50000000 | — | chunk_boundary | rtol=1e-12,atol=0 | medium/medium | — |
| `terrain.roughness` | — | — | 32B/cell | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.shreve` | O(N log N)（与 Strahler 同拓扑机器） | exact | 24B/cell cells≤50000000 | — | chunk_boundary | rtol=1e-12,atol=0 | medium/medium | — |
| `terrain.sink_fill` | O(N log N) | — | 32B/cell cells≤50000000 | — | none | rtol=1e-06,atol=1e-09 | medium/high | THREAD |
| `terrain.sky_view_factor` | — | — | 24B/cell cells≤50000000 | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.slope` | — | — | 40B/cell | — | none | rtol=1e-06,atol=1e-09 | medium/high | THREAD |
| `terrain.solar_radiation` | O(N)（解析 Ra + gradient 坡面因子） | heuristic | 48B/cell | — | coarse | rtol=0.02,atol=0.5 | low/medium | — |
| `terrain.spi` | — | — | 24B/cell | — | none | rtol=1e-06,atol=1e-09 | low/low | INLINE |
| `terrain.strahler` | O(N log N) | — | 32B/cell cells≤50000000 | — | none | rtol=1e-12,atol=0 | medium/medium | THREAD |
| `terrain.streams` | — | — | 16B/cell | — | none | rtol=1e-06,atol=1e-09 | low/low | INLINE |
| `terrain.tpi` | — | — | 32B/cell | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.tri` | — | — | 32B/cell | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.twi` | — | — | 24B/cell | — | none | rtol=1e-06,atol=1e-09 | low/low | INLINE |
| `terrain.viewshed` | — | approximate | 16B/cell cells≤50000000 | — | none | rtol=1e-06,atol=1e-09 | medium/medium | THREAD |
| `terrain.watershed` | — | — | 32B/cell | — | none | rtol=1e-12,atol=0 | medium/medium | THREAD |
