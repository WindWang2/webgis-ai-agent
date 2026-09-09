# Lakehouse V7 — Progress

| wave | 状态 | commit | 备注 |
|---|---|---|---|
| 1 audit/docs | done | (worktree init) | 00-baseline/01-architecture/02-plan |
| 2 n-d cube schema | done | 0ad93bb1 | cube_schema.py 纯校验层 |
| 3 xarray adapter | done | 0ad93bb1 | probe-gated；V6 合成投影 |
| 4 labeled store v2 | done | 0ad93bb1 | v3 dimension_names；V6 版本闸 |
| R0 架构挑战 | done | 0ad93bb1 | 28 findings 全处置（05-review-findings.md） |
| 5 labeled selection | in progress | | labeled_selection.py（标签→bounded 索引计划） |
| 6 chunk planner | pending | | |
| 7 rs cube | pending | | |
| 8-9 s3 multipart/etag | pending | | |
| 10 virtual objects | pending | | |
| 11 catalog+migration | pending | | |
| 12 STAC | pending | | |
| 13 publishing | pending | | |
| 14 lineage | pending | | |
| 15-16 GC/retention | pending | | |
| 17 DR scrub | pending | | |
| 18 REST | pending | | |
| 19 security | pending | | |
| 20 perf | pending | | |
| 21 completion proof | pending | | |

基线：tests/data 465 passed / 6 skipped（zarr 3.3.0 + xarray 2026.7.0 本机已装）。
