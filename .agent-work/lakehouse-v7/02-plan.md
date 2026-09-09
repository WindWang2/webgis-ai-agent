# Lakehouse V7 — Implementation Plan (waves)

每 wave：实现 → targeted tests → ruff → 03-progress 更新 → 独立 commit。

| # | wave | 主要产物 | 测试 |
|---|---|---|---|
| 1 | audit/docs | 00/01/02 文档 | — |
| 2 | n-d cube schema | `app/services/lakehouse/cube_schema.py` | 正/负/边界（dims 白名单、坐标单调、CRS、网格恒等） |
| 3 | xarray adapter | `app/services/lakehouse/xarray_adapter.py` | round-trip、V6 兼容读、typed 缺席 |
| 4 | labeled cube store v2 | `cube_store.py` 扩展 `write_labeled_cube/open_labeled_cube` | labeled selection→chunk 触达、dims 组合 |
| 5 | labeled selection | `labeled_selection.py`（时间/带/极化/垂直/窗口→bounded plan） | 触达块∝窗口、越界 typed |
| 6 | chunk planner | `chunk_planner.py` | 确定性、rechunk、预算闸 |
| 7 | rs cube | `rs_cube.py`（optical/SAR/mask 组装、对齐校验） | 对齐失败拒绝、mask lineage |
| 8 | s3 multipart/stream | `s3_blob_store.py` 扩展 | fake-client：分段/重试/abort 清理/ETag |
| 9 | etag/retry/orphan | 同上 + `sweep` | 条件写竞态、超龄清扫 |
| 10 | virtual objects | `virtual_object.py` | 缺失子/深度闸/零复制证明/owner |
| 11 | catalog + migration | `app/models/lakehouse_catalog.py` + `0034` + `catalog_service.py` | upsert 幂等、分页、bbox/time/tags、单 head、up/down/up |
| 12 | STAC | `catalog_stac.py` | 必填字段、分页 links |
| 13 | project publishing | `project_publish.py` + REST | owner 链、幂等、撤销、跨租户拒绝 |
| 14 | lineage 集成 | virtual/rs-cube→lineage_query 投影 | 血缘边可查询 |
| 15 | GC | `lakehouse_gc.py` | protected roots、plan token、中断续跑、确定性证据 |
| 16 | retention + multipart 孤儿 | gc 扩展 | 超龄/宽限 |
| 17 | DR scrub/restore | `dr.py` 扩展 | 采样确定性、etag mismatch、损坏检测 |
| 18 | REST 面 | routes + schemas 扩展 | owner 门禁/错误脱敏/预算 |
| 19 | security pass | SSRF/路径/边界/脱敏回归 | — |
| 20 | structural perf | `tests/benchmarks/test_lakehouse_v7.py`（注册 NIGHTLY_ONLY） | 结构性断言 |
| 21 | completion proof | e2e optical+SAR 全链路 | §17 全步 |
| 22 | real-services（可选） | 自跳过 smoke | — |
| 23 | ADR/CHANGELOG/PR | ADR-0119 + 最小追加 | — |

基线验证命令（venv 共享）：
`/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest tests/data -q --no-cov`
