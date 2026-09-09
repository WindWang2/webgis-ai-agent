# Lakehouse V6 — 03 Progress / 04 Test Matrix（截至 W14）

## Implementation waves（14/14 完成，14 commits）

| Wave | Commit | 状态 |
|---|---|---|
| docs: baseline+architecture+plan | d34960c | ✅ |
| W1 DataObject identity + content-hash-default-on | 729a65b | ✅ |
| W2 S3 BlobStore（同接口） | 03546e7 | ✅ |
| W3 悬空 ref:fabric-parquet 修复 | 09f3e90 | ✅ |
| W4 GeoParquet row-group 剪枝扫描 | e06104d | ✅ |
| W5 原子 save_png + COG DataObject | 07263d3 | ✅ |
| W6 Zarr V6 cube store + CoW revision | d675cd8 | ✅ |
| W7 cube 生产路径 + ref:cube | 9dc40e9 / 63e0b75 | ✅ |
| W8 dedup/cache identity | 并入 W1/W5/W6/W7 | ✅ |
| W9 workspace durability | b17e12b / 243b882 | ✅ |
| W10 environment fingerprint + lineage | 6f2b6a3 | ✅ |
| W11 DR + BlobStore 根缓存隔离 | 856686a 损坏→11e1b4d 重提 | ✅ |
| W12 安全回归 + SSRF 门序修复 | 8e3da92 | ✅ |
| W13 REST 面 + snapshot regen | a85efc2 | ✅ |
| W14 perf harness + docs + 质量产物 | 43cdbc3 | ✅ |

注：本机曾出现 git 对象库并发损坏（共享 bare 对象库上 9 个并行 Epic 的
git maintenance 竞争，产生零字节对象）。处理：分支 ref 重置到完好父提交，
index 重建后重放；push 备份先行；`git rev-list` 全量验证 14 commits 可读。

## 验收指标对照

- ✅ session 结束/进程重启后 durable refs 可恢复：W9 e2e（save→unlink→
  restore(register)→字节一致复活；cube manifest lane 从 BlobStore 物化）
- ✅ content hash 默认参与 identity：DataObject 恒内容寻址；session
  RefDescriptor 默认 ON（显式 off 回退）
- ✅ Zarr 真实 round-trip：W6/W7（group cube 写/读/CoW fork/consolidated）
- ✅ 大栅格 window/chunk 不隐式全量：W5 counting-read 结构性证明；
  W6 counting store chunk 计数；W4 row-group 剪枝计数
- ✅ GC 不删 pinned/in-flight/reachable：W11 live-ref 保护 e2e + 既有
  revision pin 测试语义未动
- ✅ dangling fabric ref 修复：W3（resolver+台账+GC+identity）
- ✅ vector/raster/cube deterministic identity：dedup 测试（同内容同 id）
- ✅ 跨项目/owner 隔离：W1/W12（身份分离 + 越权物化/读 404，不泄漏存在性）

## 测试矩阵（本地实测）

| 车道 | 命令 | 结果 |
|---|---|---|
| V6 全套 | tests/data + api（78+ tests） | ✅ 全绿 |
| zarr foundation 回归 | test_geo_raster_zarr_v6 | ✅ |
| geo lanes 回归 | test_geoparquet_fast_lane_v5 / arrow_ops_v5 | ✅ |
| workspace 回归 | test_workspace_snapshot | ✅ |
| quality gates | quality/ + api snapshot + drift + manifest | ✅（regen 后） |
| env parity | env_hygiene + env_template_parity | ✅ |
| perf contract | test_ci_perf_coverage_contract + ci_local_gate | ✅ |
| V6 perf harness | `-m perf` nightly lane | ✅ 4 passed |
| ruff | app/ + tests/ | ✅ |
| **全量后端** | cov-fail-under=75 主车道 | 见 Phase D 记录 |

### 预存失败（与本 Epic 无关，stash 基线复现确认）
- `tests/data/test_wave1_promotion_gc.py`（5 failed）：真实 Postgres fixture
  依赖，本机无 PG。
- `tests/data/test_quota_retention_v5.py::test_retention_workspace_tier_and_
  lineage_root_protected`：SQLite `no such table: users`（测试基建）。
