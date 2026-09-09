# Cancellation Coverage Certification（自动生成）

> 由 `python scripts/gen_resource_certification.py` 派生，请勿手改。
> 契约：协作式取消原语 `app/lib/cancellation.py`（checkpoint /
> cancellable）；行为认证：tests/quality/
> test_cancellation_resource_certification.py。

- 覆盖：**20/60** 个重计算文件含取消检查点。

| file | cancellation sites | loop sites（粗计） | status |
|---|---|---|---|
| app/lib/geo_analysis/__init__.py | 0 | 0 | no-checkpoints |
| app/lib/geo_analysis/_vector.py | 0 | 6 | no-checkpoints |
| app/lib/geo_analysis/aggregation.py | 2 | 26 | certified |
| app/lib/geo_analysis/cokriging_lmc.py | 1 | 10 | certified |
| app/lib/geo_analysis/cv.py | 0 | 6 | no-checkpoints |
| app/lib/geo_analysis/dasymetric.py | 0 | 11 | no-checkpoints |
| app/lib/geo_analysis/density.py | 2 | 23 | certified |
| app/lib/geo_analysis/evidence.py | 0 | 1 | no-checkpoints |
| app/lib/geo_analysis/geometry_ops.py | 2 | 11 | certified |
| app/lib/geo_analysis/geometry_repair.py | 0 | 3 | no-checkpoints |
| app/lib/geo_analysis/glcm.py | 0 | 14 | no-checkpoints |
| app/lib/geo_analysis/heatmap_grid.py | 1 | 6 | certified |
| app/lib/geo_analysis/interpolation.py | 2 | 23 | certified |
| app/lib/geo_analysis/interpolation_compare.py | 0 | 4 | no-checkpoints |
| app/lib/geo_analysis/kriging.py | 9 | 87 | certified |
| app/lib/geo_analysis/kriging_simulation.py | 2 | 9 | certified |
| app/lib/geo_analysis/kriging_st.py | 1 | 5 | certified |
| app/lib/geo_analysis/network.py | 3 | 30 | certified |
| app/lib/geo_analysis/phenology.py | 0 | 0 | no-checkpoints |
| app/lib/geo_analysis/point_pattern.py | 6 | 76 | certified |
| app/lib/geo_analysis/raster_change.py | 1 | 4 | certified |
| app/lib/geo_analysis/raster_grid.py | 0 | 7 | no-checkpoints |
| app/lib/geo_analysis/raster_guard.py | 0 | 2 | no-checkpoints |
| app/lib/geo_analysis/raster_math.py | 2 | 17 | certified |
| app/lib/geo_analysis/raster_ops.py | 0 | 14 | no-checkpoints |
| app/lib/geo_analysis/raster_pca.py | 0 | 3 | no-checkpoints |
| app/lib/geo_analysis/raster_windowed.py | 2 | 16 | certified |
| app/lib/geo_analysis/rbf_interpolation.py | 1 | 5 | certified |
| app/lib/geo_analysis/regression_kriging.py | 0 | 17 | no-checkpoints |
| app/lib/geo_analysis/rs_v3.py | 2 | 33 | certified |
| app/lib/geo_analysis/sar_calibration.py | 0 | 3 | no-checkpoints |
| app/lib/geo_analysis/sar_filter.py | 0 | 6 | no-checkpoints |
| app/lib/geo_analysis/sar_temporal.py | 0 | 9 | no-checkpoints |
| app/lib/geo_analysis/sar_v3.py | 0 | 2 | no-checkpoints |
| app/lib/geo_analysis/spatial_regression.py | 2 | 70 | certified |
| app/lib/geo_analysis/spatial_weights.py | 0 | 5 | no-checkpoints |
| app/lib/geo_analysis/spatiotemporal_eha.py | 0 | 18 | no-checkpoints |
| app/lib/geo_analysis/spectral.py | 0 | 8 | no-checkpoints |
| app/lib/geo_analysis/statistics.py | 8 | 117 | certified |
| app/lib/geo_analysis/tasseled_cap.py | 0 | 10 | no-checkpoints |
| app/lib/geo_analysis/temporal_cube.py | 0 | 2 | no-checkpoints |
| app/lib/geo_analysis/terrain.py | 12 | 102 | certified |
| app/lib/geo_analysis/tin_interpolation.py | 0 | 21 | no-checkpoints |
| app/lib/geo_analysis/trend_surface.py | 0 | 11 | no-checkpoints |
| app/lib/geo_analysis/uncertainty.py | 0 | 5 | no-checkpoints |
| app/lib/geo_raster/__init__.py | 0 | 1 | no-checkpoints |
| app/lib/geo_raster/chunk.py | 0 | 23 | no-checkpoints |
| app/lib/geo_raster/cog.py | 0 | 5 | no-checkpoints |
| app/lib/geo_raster/env.py | 0 | 3 | no-checkpoints |
| app/lib/geo_raster/fingerprint.py | 0 | 4 | no-checkpoints |
| app/lib/geo_raster/reader.py | 0 | 10 | no-checkpoints |
| app/lib/geo_raster/remote.py | 0 | 3 | no-checkpoints |
| app/lib/geo_raster/source.py | 0 | 2 | no-checkpoints |
| app/lib/geo_raster/windowed.py | 1 | 11 | certified |
| app/lib/geo_raster/zarr.py | 0 | 26 | no-checkpoints |
| app/services/data_ingest/__init__.py | 0 | 0 | no-checkpoints |
| app/services/data_ingest/pipeline.py | 0 | 1 | no-checkpoints |
| app/services/data_ingest/repair_planning.py | 0 | 6 | no-checkpoints |
| app/services/mapspec_layer_pipeline.py | 0 | 0 | no-checkpoints |
| app/services/mapspec_to_svg.py | 0 | 27 | no-checkpoints |

> no-checkpoints 是如实披露的缺口（非失败）：该文件当前没有
> 长循环或尚未接线；认证表随补齐更新。
