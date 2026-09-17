# TEST_MATRIX — 极限规模渲染运行时 V2 测试计划

> 目录约定（现状核实）：后端 `tests/unit/`（单测）、`tests/perf/`（性能契约/压力基准, pytest marker `perf`）、`tests/benchmarks/`（确定性基准 + baseline 棘轮, marker `perf`）；前端 vitest 同目录 `*.test.ts`（`pnpm --dir frontend test` / `vitest run`）。pytest `timeout=60`（pytest.ini）；perf 基线 gate = `tests/benchmarks/_baseline_policy.py`（missing baseline = fail；`PERF_UPDATE_BASELINES=1` 记录 / `ALLOW_MISSING_PERF_BASELINE=1` 放行）。

## A. 后端单测（tests/unit/）

| # | 测试文件（新增） | 覆盖 | 关键断言 |
|---|---|---|---|
| A1 | `test_tile_pipeline_lod.py` | LOD 金字塔构建/选取 | lod(k) 要素数单调不增；同输入同输出（确定性）；GeometryCollection/非法几何剔除与 mvt.build_spatial_index_entry（mvt.py:1302）同口径；estimated_bytes 记账正确 |
| A2 | `test_tile_pipeline_view_query.py` | 视口查询端点语义 | bbox 相交 + max_features 截断（截断时带 has_more/续读游标）；antimeridian 跨越 bbox 查询（mvt 有切分先例 mvt.py:259-470）；空/越界 bbox → 400 |
| A3 | `test_tile_pipeline_auth_budget.py` | 租户与预算 | 无/错 X-Session-Token → 401/403（对齐 test_sec08_session_owner_token.py 口径）；`_layer_data_budget`（layer.py:52）超限 429；Redis 缺席 fail-open |
| A4 | `test_tile_pipeline_cache_epoch.py` | LOD/patch 缓存并发正确性（复用 mvt epoch 测试族风格） | overwrite/rollback/evict 后旧 LOD 绝不服务（get_epoch/put_if_current, mvt.py:1621/1632）；构建中失效 → RefDataUnavailableError → 路由重拉重试（layer.py:326-329 同款）；singleflight 去重（并发同 key 只算一次, mvt.py:1703 语义）；leader 超时/崩溃诚实降级 |
| A5 | `test_tile_pipeline_patch.py` | 服务端 patch 计算 | since=revision 的 diff 正确（add/remove/update/unchanged）；unchanged → 空 patch；revision 缺失 → 降级整包；patch 字节有界 |
| A6 | `test_ref_descriptor_lod_fields.py` | descriptor additive | 旧 descriptor（无新字段）读取缺省安全；store/overwrite 重算填新字段（session_data.py:390/446 路径）；is_mvt_capable（ref_descriptor.py:263）行为不变 |

## B. 后端性能/压力（tests/perf/ + tests/benchmarks/，marker `perf`）

| # | 测试文件 | 覆盖 | 预算/棘轮 |
|---|---|---|---|
| B1 | `tests/benchmarks/test_extreme_scale_benchmark.py` | 合成规模：1×1M / 10×100k / 100×10k 要素 ref | 首帧（概览档）构建/查询/编码时延；内存峰值（BenchmarkInstrumenter 风格, 模板 = test_mvt_cache_pressure_benchmark.py）；baselines.json 棘轮 |
| B2 | `tests/perf/test_extreme_scale_concurrency.py` | 并发风暴：同视口 50 并发 / 500 不同视口 / 取消在飞 | singleflight 生产者计数；无重复构建；请求数 ≤ 预算 |
| B3 | `tests/perf/test_extreme_scale_stale.py` | stale/生命周期：构建中 overwrite ×N / 回滚 / 逐出 | 零幽灵服务（epoch 拦截计数 > 0）；终态缓存与权威 payload 一致 |
| B4 | `scripts/perf/extreme_scale_measurements.py` + budgets.json 新条目 | CI 预算门 | 新增 `extreme_scale_view_p95_ms` / `extreme_scale_patch_bytes_p95`；沿用 run_budget.py 门禁（perf/budgets.json schema 不变）；`--self-test` 超线可红 |

## C. 前端单测（frontend/lib/map-kit/progressive/*.test.ts + 相关）

| # | 测试文件 | 覆盖 | 关键断言 |
|---|---|---|---|
| C1 | `engine.test.ts` | 渐进编排 | 首帧 plan 选取（descriptor 缺失 → legacy 路径逐字节等价）；快速连续 moveend 只结算最后视口（generation token, 镜像 renderer.ts:44）；会话切换取消在飞请求（AbortController） |
| C2 | `lod.test.ts` | LOD 选取 | zoom 阈值换档有滞回（防抖动）；与 data-tiers 常量 parity（frontend/lib/data-tiers.ts:10-17, 同 tests/cartography/test_data_tiers_and_matrix.py 的源码扫描手法） |
| C3 | `patch.test.ts` | 增量应用 | feature id 合并语义（add/remove/update）；patch 应用后与整包重拉结果深等价；>帽值 patch → 降级整包 setData |
| C4 | `scheduler.worker.test.ts` | worker 调度 | 主线程回退透明（worker 不可用）；30s 超时诚实降级（worker-bridge.ts:43 同值同语义）；帧预算不超（RenderDebouncer 口径, render-debouncer.ts:23） |
| C5 | `memory-budget.test.ts` | 内存预算 | 预算水位 = Σ(descriptor 估算)；超限驱逐最久未用层并触发降档（C6 联动）；raw/filtered 双份不重复计费错误 |
| C6 | `degrade.test.ts` | 退化链 | 触发顺序固定（D6 阶梯）；逐级恢复不跳级；每次降级产证据事件（useHudStore 记录断言） |
| C7 | `network-budget.test.ts` | 网络预算 | 并发帽（默认 4）；优先级（概览 > 细节）；304 命中不重传（ETag 协作） |
| C8 | `renderer.progressive.integration.test.ts`（放 map-kit/） | 与 addGeoJsonSource/refresh 协作 | F31 引用跳过（renderer.ts:164）与 viewport 缓存（renderer.ts:100）不被破坏；MVT source 不被 double-crop（renderer.ts:74-94 守卫仍生效） |

## D. 集成 / 回归（现有目录与既有测试的对齐义务）

| # | 位置 | 内容 |
|---|---|---|
| D1 | `tests/test_layer_api.py`、`test_layer_descriptor_api.py`、`test_layer_feature_endpoint.py`、`tests/perf/test_mvt_cache_pressure_benchmark.py` | 全量回归必须绿——本方向不改既有端点行为（flag 关闭时逐字节等价） |
| D2 | `tests/integration/`（如适用） | SSE → addLayer → progressive 装载的端到端合成会话（无 LLM/网络，镜像 cartography marker 纪律） |
| D3 | `pytest -m cartography` | 发布门回归（#1356 先例：1125 passed 基线），确认零侵入 |
| D4 | 前端 `vitest run lib/map-kit lib/mapspec-runtime lib/mapspec-compiler components/map` | 既有 1000+ 用例回归（#1356 先例 1186 passed），确认 renderer/runtime 未被破坏 |
| D5 | `ruff check app tests` + `tsc --noEmit && tsc -p tsconfig.test.json --noEmit` | 静态门（仓库惯例） |

## E. 环境披露与执行纪律

- perf 测试标注 `@pytest.mark.perf`，假定隔离执行（pytest.ini #664 契约）；新 baseline 首次提交走 `PERF_UPDATE_BASELINES=1` 并在 PR 附环境披露（CPU/内存/平台）——沿用 tests/benchmarks 既有惯例。
- 前端 perf 断言只用 fake timers + 操作计数（不测真实墙钟），对齐 map-kit 既有 `*.perf.test.ts` 的做法（如 raster-timeline.perf.test.ts）。
- 每个并发/stale 测试必须包含「故意制造竞态 → 断言拦截」的负例（A4/B2/B3），镜像 mvt epoch 测试族（CONC MINOR 系列注释, mvt.py:1394-1397）。
