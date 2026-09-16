# BASELINE — extreme-scale-runtime-v2（Phase 0 勘察基线）

## 仓库状态

- **worktree**: `C:\Users\wangj.KEVIN\projects\webgis-wt-extreme-scale-v2`
- **分支**: `perf/extreme-scale-webgis-runtime-v2`（跟踪 `origin/master`，未推远端）
- **origin/master SHA**: `faa453a8935101378c23eb6694a42c3616d9c670`（worktree HEAD 与其一致）
- **执行时间**: 2026-09-16T20:19:29Z（UTC）
- **git 状态**: `git status --short` 干净（无未提交修改；仅存在历史 CI 遗留的 `.coverage.root.pid*` 未跟踪垃圾文件，`.gitignore` 已覆盖）
- **最近提交**（origin/master 顶）:
  - `faa453a8` feat(harness): Hot-path Convergence — Mission × SkillPolicy × Evidence (Direction 04) (#1329)
  - `b44c1c9b` feat(harness): Spatial Evidence / Claim / Provenance Graph (Direction 03) (#1328)
  - `14a47cc6` feat(harness): Production GIS Skill Policy & self-evolving procedure runtime (Direction 02) (#1327)

## Open PR 快照（gh pr list，执行时点）

| PR | 分支 | 主题 | 与本方向关系 |
|---|---|---|---|
| #1335 | `fix/harness-claim-mission-failclosed` | evidence/claim/tenant fail-closed（#1330–#1334） | 零文件交集（见 PARALLEL_OWNERSHIP.md） |
| #1336 | `zcode/geoai-promptable-foundation-platform-11` | GeoAI platform、embedding cache、`frontend/components/geoai`（ADR-0198） | **热区勿碰**；占用 ADR 编号 0198 |
| #1351 | `data/spatial-quality-harmonization-v1` | `app/services/data_quality/**`、`app/lib/data/quality.py` | 零交集 |
| #1352 | `eval/gis-agent-benchmark-factory-v2` | `app/evaluation/**`、`scripts/gis_bench_v2.py` | benchmark 命名需避让（gis_bench_v2 已占） |
| #1353 | `frontend/spatial-agent-ops-cockpit-v1` | `frontend/components/cockpit/**`、`frontend/lib/cockpit/**`、workbenchSlice | 零交集 |
| #1354 | `rs/temporal-cube-sar-optical-v1` | remote sensing temporal cube | 可能共享 `raster_tile_service.py` 外围（其分支有 raster 触碰面，落地时需 rebase 核对） |
| #1355 | `harness/event-driven-spatial-ops-v1` | `app/services/spatial_events/**`、portfolio 路由 | 零交集 |
| **#1356** | `cartography/multiscale-scene-intelligence-v1` | scene_*（scene_lod.py 等）、MapSpec schema v1.4、`frontend/lib/map-kit/renderer.ts`、`mapspec-runtime/adapter.ts`+`runtime.ts`、mapspec-compiler、`raster_tile_service.py`、`app/api/routes/layer.py`、`map-panel.tsx`（ADR-0199） | **重叠度最高**。已核实其 diff 文件清单（gh pr diff --name-only）。本方向新模块避免深改这些文件；其 scale-aware LOD 落点 = ADR-0154 的 `label.zoomBands` / `thresholds.maxFeatures`（**样式/标注面**），与本方向**数据面** LOD/渐进加载互补不重叠 |

## Open issue 快照（gh issue list，前 10）

全部为 2026-09-16 audit 批次：#1341–#1350（依赖 CVE、前端、后端安全、工具链、WebGIS 综合审计跟踪）。无与本方向直接冲突的 open issue；#1341 提及 maplibre-gl ^5.23.0 的 CVE-2026-85061（升级决策不在本方向范围，但 benchmark 用新版本时注意）。

## 文档/ADR 基线

- `docs/adr/`：master 上 211 个，最新 = `0197-durable-gis-mission-runtime.md`。
- ADR-0198 已被 #1336 预定（geoai），ADR-0199 已被 #1356 预定（multiscale scene）→ **本方向 ADR 从 0200 起**。
- `CHANGELOG.md` 顶部条目：`[Unreleased] 2026-09-13 adaptive-data-supply/v1 (ADR-0172~0179)` + agent-swarm/02 visual-self-healing（ADR-0186）。
- 并行方向勘察六件套先例：`.agent-work/multiscale-scene-intelligence-v1/`（#1356 分支内），本目录沿用同一六文件命名。

## 相关既有性能基线（本方向的棘轮起点）

- `perf/budgets.json`（schema `quality-e2e-v9/ADR-0146 budget gate`）现有 4 条：`sse_concurrent_50_total_ms` / `mvt_tile_p95_ms` / `cube_window_p95_ms` / `chat_first_token_p95_ms`；门禁 CLI = `scripts/perf/run_budget.py`。
- `tests/benchmarks/baselines.json` + `tests/benchmarks/_baseline_policy.py`：缺 baseline 即 fail（`ALLOW_MISSING_PERF_BASELINE=1` / `PERF_UPDATE_BASELINES=1` 才可记录）。
- `tests/perf/test_mvt_cache_pressure_benchmark.py`：MVT 缓存/内存压力 10 场景确定性基准（1×100k、10×10k、并发同瓦片 ×50、500 瓦片、overwrite/clear 生命周期等）——本方向合成规模 benchmark 的直接模板。
