# ads-v1 首轮基线（DS0.5 / ADR-0170 · provisional）

> 测量脚本：`scripts/ads_baseline.py`（离线可复跑，60 样本/项）。
> DS8 用同一协议复测并转定稿；本轮数值仅作 ratchet 锚点。

## 取数时延（adapter.query，OGC API fixture，200 要素/次）

| 指标 | 值 |
|---|---|
| 样本数 | 60 |
| 每次 rows | 200 |
| P50 | 7.98 ms |
| P95 | 10.09 ms |

## 内联缓存（ref_payload_cache）

| 指标 | 值 |
|---|---|
| 命中率 | 1.0 |
| put P50 | 0.001 ms |
| get(hit) P50 | 0.001 ms |
| get(hit) P95 | 0.002 ms |

## 外部源可用率（真实 probe，不伪造）

| 端点 | 可用 | probe 耗时 |
|---|---|---|
| overpass-api.de | yes | 14.0 ms |
| services.arcgis.com | yes | 15.9 ms |
| planetarycomputer.microsoft.com | yes | 14.9 ms |

> sandbox default = unavailable (not faked)
