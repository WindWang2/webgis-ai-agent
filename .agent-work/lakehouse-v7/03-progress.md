# Lakehouse V7 — Progress

| wave | 状态 | commit | 备注 |
|---|---|---|---|
| 1 audit/docs | done | (worktree init) | 00-baseline/01-architecture/02-plan |
| 2 n-d cube schema | done | 0ad93bb1 | cube_schema.py 纯校验层 |
| 3 xarray adapter | done | 0ad93bb1 | probe-gated；V6 合成投影 |
| 4 labeled store v2 | done | 0ad93bb1 | v3 dimension_names；V6 版本闸 |
| R0 架构挑战 | done | 0ad93bb1 | 28 findings 全处置（05-review-findings.md） |
| 5-6 selection/planner | done | f5199c6f | 确定性计划层 |
| 6 chunk planner | pending | | |
| 7 rs cube | done | 35351ec8 | 光学/SAR/mask 对齐组装 |
| 8-9 s3 multipart/etag | done | 981fa59a | multipart/流式/ETag/清扫 |
| 10 virtual objects | done | 40a5ae38 | 零字节组合 + 分片 |
| 11 catalog+migration | done | cb58b627 | 0034 additive 单 head |
| 12 STAC | done | cb58b627 | 1.0.0 纯投影 |
| 13 publishing | done | 8f77b09c | 零字节发布+幂等 |
| 14 lineage | done | b103a9db | manifest 祖先视图 |
| 15-16 GC/retention | done | 8f77b09c | union 保护+watermark |
| 17 DR scrub | done | 8f77b09c | 采样/ETag/深度 |
| 18 REST | done | b103a9db | 8 端点+门禁 |
| 19 security | done | 2baef0a5 | V7 面回归 |
| 20 perf | done | 2baef0a5 | NIGHTLY_ONLY 注册 |
| 21 completion proof | done | 2baef0a5 | §17 全链路 |

基线：tests/data 465 passed / 6 skipped（zarr 3.3.0 + xarray 2026.7.0 本机已装）。
