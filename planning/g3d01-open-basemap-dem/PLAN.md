# G3D01 PLAN — 开源底图 / DEM 资源服务与场景数据底座

基线 `1115d683a` · 分支 `zcode/g3d01-open-basemap-dem-v1` · worktree `../webgis-wt-g3d01-open-basemap-dem`

## 使命（唯一任务边界）

GIS 资源与数据服务基础层：让既有 MapLibre 与场景契约以**可验证、可授权、可脱网降级**的方式获取矢量/影像/地形底图与高程数据。不做 3D Tiles renderer、新 viewshed 算法、Agent 语义规划。

## 架构决策（详见 DECISIONS.md）

- **D1 单一真相 typed profile**：新增 `app/lib/cartography/basemap_profiles.py`（pydantic，纯静态目录 + 纯函数可用性评估），字段对齐 data fabric `SourceDefinition` 惯例（license/auth env-key-only/verified）但独立域（渲染资源 ≠ 数据集源）。
- **D2 生成式 TS 镜像 + 双侧契约锁**：`basemap_profile_projection.py` 生成 `frontend/lib/basemap-profiles.generated.ts`（值投影，非类型投影）；pytest 锁再生成幂等 + 与 `providers.ts`/`base_layers.py` 一致；vitest 锁 `TILE_PROVIDERS` ≡ 生成 profiles（杀"手工双真相"）。
- **D3 OpenFreeMap 接入**（MIT 样式 / OSM 数据 / 免 key / 无请求上限 / 商用允许 / 署名 "OpenFreeMap © OpenMapTiles Data from OpenStreetMap"；公共实例离线下载 3000 tiles/50MB/4 并发 → 记入 cache_policy 限制）：libre/positron/bright 三个 vector style 进双目录。
- **D4 PMTiles→XYZ 服务路由**：复用 `PMTilesAdapter.read_tile_bytes` + `catalog tiles` 路由的鉴权/ETag/typed-error 模式，使"本地 PMTiles 派生 XYZ 服务"（providers.ts 注释里承诺过的）成为真实自托管路径。
- **D5 synthetic DEM fixture**：`app/services/synthetic_terrain.py` 确定性合成 DEM → GeoTIFF → `register_raster_ref` → 既有 `render_terrarium_tile` 管线；profile `synthetic=true`、`qualification="display_only"` —— 数据诚信：绝不冒充分析级。
- **D6 AWS 地形静默回退收编**：`renderer.ts` 硬编码 URL 移入生成 profile（`aws-terrarium-public`，Mapzen/AWS Open Data 许可+署名），默认行为不变但受治理、可披露；air_gapped 下禁用。
- **D7 署名修复**：`getMapStyle` 使用 per-provider attribution（bug：硬编码 OSM）。
- **D8 底图瓦片失败 → typed 证据**：沿用 runtime `terrainErrorHandler` 模式，basemap 源瓦片错误登记 `scene_basemap_unavailable`（不白屏、不吞错，披露给 observation/export）。

## 模块切分（新增为主，既有文件最小改）

```
app/lib/cartography/basemap_profiles.py          # NEW D1 模型+目录+评估
app/lib/cartography/basemap_profile_projection.py # NEW D2 TS 生成器（python -m 可运行）
app/core/base_layers.py                          # MOD D2/D3 目录派生自 profiles（API 不变）
app/services/synthetic_terrain.py                # NEW D5
app/api/routes/data_fabric.py                    # MOD D4 PMTiles tile 路由（+服务函数放 tile_service? 独立 pmtiles_tile_service）
app/services/data_fabric/pmtiles_tile_service.py # NEW D4 服务层（缓存/ETag）
frontend/lib/basemap-profiles.generated.ts       # NEW D2 生成物
frontend/lib/providers.ts                        # MOD D3 +OpenFreeMap×3
frontend/lib/map-kit/renderer.ts                 # MOD D6 地形回退 import 生成 profile
frontend/components/map/map-panel.tsx            # MOD D7 署名；D6 terrain options
frontend/lib/mapspec-runtime/runtime.ts          # MOD D8 basemap 证据 handler
tests/cartography/test_basemap_profiles.py       # NEW 模型+负例
tests/cartography/test_basemap_profile_projection.py # NEW 幂等+一致性
tests/unit/test_synthetic_terrain.py             # NEW 全链路+资格
tests/data/test_pmtiles_tile_route.py            # NEW 路由（鉴权/404/caps）
frontend/lib/basemap-profiles.contract.test.ts   # NEW vitest 契约锁
（另：受影响的既有测试更新：renderer-m4、base_layers 相关、map-basemap-lifecycle）
```

## Waves

- **W0** 勘察/环境 ✅（BASELINE.md / PR_OVERLAP_MATRIX.md / venv+pnpm）
- **W1** D1+D2 后端 profiles + 投影 + 测试
- **W2** D3 前后端目录接入 + D7 署名 + vitest 锁
- **W3** D4 PMTiles 路由；D5 synthetic DEM；D6 地形收编；D8 证据
- **W4** M7 验收：契约级 E2E（synthetic DEM→MapSpec raster-dem→compiler→runtime apply→ACK）、负例矩阵、性能表、全量本地验证（pytest 分域 + vitest + typecheck + eslint + ruff）
- **W5** 独立 review ×2 → 修复 → 复验
- **W6** 原子 commits → push → PR（不合并）

## 停机条件（DoD，对应验收标准）

1. ≥2 类合法开源底图路径（OpenFreeMap vector + Carto/OSM raster 治理化）+ 1 条可控 DEM 3D 路径（会话 ref）+ 纯本地 synthetic fixture ✅须真实测试
2. 热切换保留图层/ACK/相机（既有测试回归通过）+ 地形源变化可追踪（证据/资格）
3. 非法源/缺署名/内网 SSRF/错编码 → 拒绝或显式 unavailable（负例测试）
4. 性能指标 + 部署说明（air_gapped/自托管）
5. 独立 review 两轮通过；全部本地验证绿
