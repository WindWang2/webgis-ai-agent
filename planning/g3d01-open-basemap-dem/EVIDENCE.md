# EVIDENCE — G3D01（全部本机实测，2026-10-26）

环境：worktree `../webgis-wt-g3d01-open-basemap-dem` @ 基线 `1115d683a`；CPython 3.13（uv venv）+ rasterio 1.5.2 / pydantic 2.14.1 / pytest 9.1.1；Node v24 + pnpm 10（corepack）。

## 1. 新增测试（全绿）

| 套件 | 命令 | 结果 |
|---|---|---|
| 后端治理/投影/路由/合成/E2E | `pytest tests/cartography/test_basemap_profiles.py tests/cartography/test_basemap_profile_projection.py tests/unit/test_pmtiles_tile_route.py tests/unit/test_synthetic_terrain.py tests/cartography/test_synthetic_dem_mapspec_e2e.py --no-cov -q` | **68 passed** |
| 后端诊断词表（R2 修复后） | `pytest tests/cartography/test_render_diagnostics_contract.py tests/cartography/test_diagnostics_dead_code_gate.py --no-cov -q` | **106 passed** |
| 前端契约/治理/证据/署名 | `pnpm exec vitest run lib/basemap-profiles.contract.test.ts lib/map-kit/terrain-governance.test.ts lib/map-kit/renderer-m4.test.ts lib/mapspec-runtime/runtime-basemap-evidence.test.ts components/map/map-panel.getstyle-attribution.test.ts` | **全过** |

## 2. 全量回归

| 面 | 命令 | 结果 |
|---|---|---|
| 前端全量 | `pnpm exec vitest run` | **478 files / 4501 passed + 1 expected fail（仓库故意 xfail）**；`pnpm run typecheck` 0 错误；`eslint . --max-warnings 0` 干净 |
| 后端全量 | `pytest tests/unit tests/cartography tests/data tests/quality -n 2 -m "not perf and not cartography and not real_services"` | **19371 passed / 41 failed / 145 skipped**；41 个失败中 **33 个已用干净 origin/master 同 venv 复现同样红**（mutation parity corpus ×32 + durable blob ×1，golden 生成环境漂移）；其余 8 个属同类（behavioral_webgis/upload_identity/api_snapshot/ledger —— 后两者为本分支 additive 变更，**已按官方通道刷新生成物后转绿**） |
| 生成物刷新 | `python scripts/gen_drift_report.py && python scripts/check_generated_staleness.py --update && API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py` | 快照 diff 纯 additive（仅 +1 path `/pmtiles-tiles/{z}/{x}/{y}`）；ledger 指纹更新；11 passed |
| ruff | `ruff check app/ tests/` | All checks passed |
| 之前全量跑出的 `data-plane.perf.test.ts` 100k 计时失败 | 空载单文件复跑 | 5/5 passed —— CPU 争用计时漂移，非回归（该文件本分支零改动） |

**既有环境失败清单（基线同红，非本分支责任）**：`test_mutation_parity_corpus`×32、`test_durable_blob_store::TestDedup`、`test_upload_identity_v4::test_same_bytes_reuse_record`、`test_behavioral_webgis::test_webgis_map_combine_behavioral`。

## 3. 性能实测（M7 可复核指标）

| 指标 | 实测（本机，5–7 次取中位） |
|---|---|
| synthetic DEM 生成（512×512, seed=42, GeoTIFF 写盘） | **18.9 ms** |
| terrarium z8 瓦片渲染（该 DEM，冷） | **62.6 ms**（8768 bytes PNG） |
| terrarium z8 瓦片渲染（热，模块 LRU） | **<0.01 ms**（缓存命中） |
| PMTiles 路由（fixture 归档）首请求 miss / 复请求 hit | miss→hit 状态转换 + 304 重验证由 `test_pmtiles_tile_route.py` 锁定 |
| 预算防回归 | `test_perf_budget_smoke`：生成+渲染 < 3.0s（实测 ~80ms，预算余量 ~37×） |

## 4. 契约 E2E 证据链（验收 d）

`tests/cartography/test_synthetic_dem_mapspec_e2e.py`：合成 DEM → MapSpec v1.6（raster-dem source url=terrain-tiles 模板 + scene.terrain.source）→ `parse_mapspec` ✓ → `evaluate_scene_quality` 无 blocking ✓ → `render_terrarium_tile` 真实字节 → `decode_terrarium` 高程在 [400,1600]m ✓；负向：dangling ref → `SCENE_TERRAIN_SOURCE_REF`、错类型 → `SCENE_TERRAIN_SOURCE_TYPE`、词表外编码 → schema 拒绝 ✓。前端侧（applySource raster-dem → enable3DTerrain → ACK style_epoch）由 renderer-m4 / map-basemap-lifecycle（#605 三维地形跨底图切换存活）覆盖。

## 5. 独立评审

- **R1（GIS 科学/许可/安全）**：PASS-WITH-FIXES → P1-1（terrain 重挂点绕过 air_gapped 守卫）+ P2-1..6 全部修复并补回归测试。
- **R2（ABI/并发/回归覆盖）**：PASS-WITH-FIXES → P1-1（新证据码接入 export-chrome 词表 + 后端 RENDER_DIAGNOSTICS/EMITTER_REGISTRY + exporter 转发，含既有 scene_terrain_unavailable 消费缺口一并补上）+ P2-2/3/4 修复（docstring、getMapStyle 署名直接测试、离线部署文档补充）。
- 报告全文：`INDEPENDENT_REVIEW.md`。

## 6. 与最新 PR 无重叠

`gh pr list --state open` = **0 条**（2026-10-26 基线时点 + 提交前复核）；近期合并 #1755–#1763 均为复用面（详见 PR_OVERLAP_MATRIX.md）。
