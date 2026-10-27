# TEST MATRIX — G3D01

## 新增测试（本分支）

| 文件 | 覆盖 | 关键负例 |
|---|---|---|
| `tests/cartography/test_basemap_profiles.py`（26） | 目录完整性（唯一 id/name、license/attribution 全场、https）、OpenFreeMap×3 治理字段、兜底源 display_only、可用性评估（token/env/deployment） | 空 license/空署名/http URL/内联 secret 形值/requires_credentials 缺 env/air_gapped_only 缺 gate/synthetic≠display_only/远端源不可默认 analysis_grade/远端缺 url/非 https |
| `tests/cartography/test_basemap_profile_projection.py`（7） | 再生成 byte 幂等、生成物含全部 name/attribution、base_layers 派生一致性、OpenFreeMap 可解析 | —（漂移即红） |
| `tests/cartography/test_synthetic_dem_mapspec_e2e.py`（7） | MapSpec v1.6 + scene.terrain 过 schema+质量门；数据面字节解码；合成资格进 spec 溯源；性能预算冒烟（<3s） | dangling terrain ref→SCENE_TERRAIN_SOURCE_REF、错类型→SCENE_TERRAIN_SOURCE_TYPE、词表外编码→schema 拒绝 |
| `tests/unit/test_synthetic_terrain.py`（13） | 确定性（同 seed 逐位一致）、GeoTIFF 自描述（SYNTHETIC_DEM tag/CRS/单波段/nodata）、高程范围、terrarium 多 zoom 渲染、ref 注册/失效 | 覆盖窗口外=全透明（无数据≠0 高程） |
| `tests/unit/test_pmtiles_tile_route.py`（12） | 归档 fixture 直读、路由 200+content-type+ETag、缓存 hit/304、204 空瓦片 | 404 未知 item、跨租户缓存命中后仍 404（API-01）、非 pmtiles 源 422、非法坐标 400、fingerprint 变更不复用旧瓦片 |
| `frontend/lib/basemap-profiles.contract.test.ts`（8） | TILE_PROVIDERS ≡ 生成画像（id/name/attribution/type/keywords/url 逐条）、env 门、免凭据 | tianditu 缺 token 诚实缺席、remote 高程 https、synthetic 强制 display_only |
| `frontend/lib/mapspec-runtime/runtime-basemap-evidence.test.ts`（4） | raster-tiles-* 错误→scene_basemap_unavailable 证据 | 非底图源不误报、无 sourceId 安全忽略、terrain 分支优先不串道 |

## 修改的既有测试

- `frontend/lib/map-kit/renderer-m4.test.ts`：不变，仍锚 terrarium 默认（D6 收编后 URL 来自画像，行为不变 —— 测试即证明）。

## 回归面（全量本地验证）

- 后端：`pytest tests/unit tests/cartography tests/data tests/quality -n 2`（进行中，结果入 EVIDENCE.md）
- 前端：`pnpm run typecheck` + `eslint . --max-warnings 0` + 全量 `vitest run`（进行中）
- ruff：全部新/改文件 clean（新增 per-file-ignore：basemap_profile_projection T20，与 ts_projection 生成器先例一致）

## 验收标准对照（工作单 §D）

| 标准 | 证据 |
|---|---|
| ≥2 类合法开源 2D 底图 + 1 条可控 DEM 3D + 纯本地 fixture | OpenFreeMap×3（vector）+ Carto/OSM（raster，治理化）+ 会话 ref terrain-tiles 路径 + synthetic-demo-dem（E2E 测试跑通全链） |
| 热切换保图层/ACK/相机 | 既有 map-basemap-lifecycle 测试回归（3D 地形跨底图切换 #605 已有）+ styleEpoch 语义未动 |
| 地形源变化可追踪 | profile qualification/synthetic 随 ref 流转（E2E 溯源测试）+ scene_terrain_unavailable/scene_basemap_unavailable 证据通道 |
| 非法源/缺署名/SSRF/无权限/错编码拒绝 | profiles 负例（license/attribution/https/secret）+ PMTiles 路由租户 404 + schema 编码词表 + 既有 security.py SSRF 面（未动，复用） |
| 性能指标 + 部署说明 | perf 冒烟（<3s 预算）+ EVIDENCE.md 实测表 + air_gapped 语义测试 |
