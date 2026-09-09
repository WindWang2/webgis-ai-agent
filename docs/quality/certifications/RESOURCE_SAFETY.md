# Resource Safety Certification（自动生成）

> 由 `python scripts/gen_resource_certification.py` 派生，请勿手改。
> 原则：estimate-before-allocate；超限必须 typed reject，
> 而不是 OOM。行为认证：tests/quality/
> test_cancellation_resource_certification.py。

| resource kind | limit | unit | source | typed reject |
|---|---|---|---|---|
| raster_total_pixels | 250000000 | pixels | app/lib/geo_analysis/raster_guard.py | RasterResourceExceededError |
| raster_max_width | 100000 | pixels | app/lib/geo_analysis/raster_guard.py | RasterResourceExceededError |
| raster_max_height | 100000 | pixels | app/lib/geo_analysis/raster_guard.py | RasterResourceExceededError |
| raster_output_bytes | 1073741824 | bytes | app/lib/geo_analysis/raster_guard.py | RasterResourceExceededError |
| json_node_budget | 20000 | nodes | app/lib/json_size.py | args oversized 门（Registry） |
| tool_args_bytes | 262144 | bytes | app/lib/json_size.py | args oversized 门（Registry） |
| algorithm_min_features_declarations | 20/208 | algorithms | app/lib/gis/algorithm_registry.py | 数据资格四态（qualify_data） |
| algorithm_max_features_hint_declarations | 4/208 | algorithms | app/lib/gis/algorithm_registry.py | 数据资格四态（qualify_data） |
| algorithm_backend_variants | 29/208 | algorithms | app/lib/gis/algorithm_registry.py | ScaleProfile 窗口（dispatch） |
