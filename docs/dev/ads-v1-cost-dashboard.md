# ads-v1 成本看板（DS8 · provisional）

> 展示层与 V11 W8 共享渲染语义，**表隔离**：本看板读 ads_* 域事实，
> 不读 carto_* 表（任务书 §8.1.7）。预算为 provisional（DS9 收口复核）。

矩阵规模：864 组（通过 864，失败 0）

## 分源 × 请求型 预算

| 源 | 请求型 | max_rows | max_bytes | max_ms | 告警 |
|---|---|---|---|---|---|
| beijing_gov | bbox_query | 50000 | 8000000 | 5000.0 | — |
| beijing_gov | time_query | 50000 | 8000000 | 5000.0 | — |
| beijing_gov | projection_query | 50000 | 8000000 | 5000.0 | — |
| beijing_gov | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| beijing_gov | sampled_query | 50000 | 8000000 | 5000.0 | — |
| beijing_gov | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| beijing_gov | paginated_query | 50000 | 8000000 | 5000.0 | — |
| beijing_gov | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| beijing_gov | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| beijing_gov | clarification_query | 50000 | 8000000 | 5000.0 | — |
| beijing_gov | local_first_query | 50000 | 8000000 | 5000.0 | — |
| beijing_gov | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | bbox_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | time_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | projection_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | sampled_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | paginated_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | clarification_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | local_first_query | 50000 | 8000000 | 5000.0 | — |
| shanghai_gov | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | bbox_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | time_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | projection_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | sampled_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | paginated_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | clarification_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | local_first_query | 50000 | 8000000 | 5000.0 | — |
| guangdong_gov | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | bbox_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | time_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | projection_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | sampled_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | paginated_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | clarification_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | local_first_query | 50000 | 8000000 | 5000.0 | — |
| local_osm | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | bbox_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | time_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | projection_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | sampled_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | paginated_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | clarification_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | local_first_query | 50000 | 8000000 | 5000.0 | — |
| local_poi | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | bbox_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | time_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | projection_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | sampled_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | paginated_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | clarification_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | local_first_query | 50000 | 8000000 | 5000.0 | — |
| local_yearbook | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | bbox_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | time_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | projection_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | sampled_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | paginated_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | clarification_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | local_first_query | 50000 | 8000000 | 5000.0 | — |
| planetary_computer | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | bbox_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | time_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | projection_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | sampled_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | paginated_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | clarification_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | local_first_query | 50000 | 8000000 | 5000.0 | — |
| copernicus_dataspace | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | bbox_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | time_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | projection_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | sampled_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | paginated_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | clarification_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | local_first_query | 50000 | 8000000 | 5000.0 | — |
| nasa_cmr_lpcloud | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | bbox_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | time_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | projection_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | sampled_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | paginated_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | clarification_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | local_first_query | 50000 | 8000000 | 5000.0 | — |
| worldbank_api | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | bbox_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | time_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | projection_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | sampled_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | paginated_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | clarification_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | local_first_query | 50000 | 8000000 | 5000.0 | — |
| gbif_api | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | bbox_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | time_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | projection_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | aggregate_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | sampled_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | fallback_chain_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | paginated_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | version_pinned_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | budget_downgrade_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | clarification_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | local_first_query | 50000 | 8000000 | 5000.0 | — |
| overpass_api | drift_annotated_query | 50000 | 8000000 | 5000.0 | — |

预算行数：144（12 源 × 12 请求型）

## 说明

- 告警语义：facts.check_budget 对 rows/bytes/latency_ms/quota 任一超限产生
  BudgetAlert；矩阵组为规划层确定性执行（无真实网络流量），latency 以
  代价估算行数为代理指标，真实流量告警随 DS9 安全复核后的运行面接入。
- 本看板由 `scripts/ads_cost_dashboard.py` 再生成；数值段为派生数据。
