# DECISIONS — G3D01

| # | 决策 | 理由 / 备选否决 |
|---|---|---|
| D1 | typed profile 目录放 `app/lib/cartography/basemap_profiles.py`（pydantic，纯静态+纯函数） | 契约域（与 terrain_encoding/mapspec_schema 同层）；data fabric `SourceDefinition` 是数据集源域，混入会污染其注册表语义。字段惯例对齐（license/auth env-key-only/缓存策略） |
| D2 | TS 镜像 = **值投影** `basemap-profiles.generated.ts`（python -m 生成）+ pytest 幂等锁 + vitest TILE_PROVIDERS 一致锁 | 复用 ts_projection（类型投影）与 render_work corpus（三锁）双先例；否决"前端直读后端 JSON"（构建期无后端）与"继续手工双真相"（基线已证漂移风险：attribution 硬编码 bug） |
| D3 | OpenFreeMap 3 样式进双目录；许可事实 2026-10-26 逐页核验（openfreemap.org + openfreemap-styles repo） | MIT/ODbL、免 key、无请求上限、商用允许、署名串固定；公共实例离线下载限额（3000 tiles/50MB/4 并发）写入 cache_policy.offline_note —— 不核验就写 = 伪造许可 |
| D4 | PMTiles→XYZ：服务层 `pmtiles_tile_service.py` + 路由复用 MVT 纪律（鉴权先于缓存 API-01、fingerprint 键、ETag、typed 4xx/5xx） | 否决"前端 pmtiles-js 直接读归档"（归档地址将暴露给浏览器且绕过租户/缓存治理）；键前缀 `pmtiles:` 与 MVT 表示隔离，manager 同步失效双前缀 |
| D5 | synthetic DEM = 服务函数（GeoTIFF 自描述 tag SYNTHETIC_DEM=1）+ profile synthetic=True 强制 display_only | 文件级自描述使误用面最小；否决"运行时内存生成"（无法过 ref_lifecycle/缓存身份纪律） |
| D6 | AWS 地形兜底**保留为默认**（cloud）但收编进治理画像；air_gapped → None → map-panel 显式禁用+证据 | ADR-0199 D10 承诺"永久兜底"，彻底移除 = 契约破坏；收编后 URL/署名/资格单一来源，行为不变+可披露 |
| D7 | attribution 经 MapStyleOption 透传（constants.ts 破损映射修复） | 最小改动面；否决"map-panel 直接 import providers"（保持 @/lib/constants 兼容层） |
| D8 | basemap 瓦片失败证据 = runtime error handler 扩展 `scene_basemap_unavailable`（raster-tiles-\d+ 模式） | 与 scene_terrain_unavailable 同通道同纪律；否决"新错误总线"（平行真相，违规） |
| D9 | base_layers.py 派生自 profiles（API 不变） | LLM 感知端从此无手工清单；_SEED_PROVIDER_ALIASES 保留（历史模板种子兼容） |
| D10 | 不动 MapSpec schema 版本（留在 1.6） | 本方向全部增量在资源治理层，无需 spec 字段变更；RasterDemMapSpecSource/scene.terrain 既有契约已足够表达（E2E 测试证实） |
| D11 | Quota 声明（requests_per_minute）不新增消费者 | M6 配额执行是独立工程（涉及出站限流器）；本方向先把 cache_policy（许可驱动的缓存/下载限制）落成机器可读，配额消费留给后续 PR —— 不做半吊子假执行 |

## 许可事实核验记录（数据诚信）

- OpenFreeMap（2026-10-26 官网+GitHub）：项目 MIT；样式 repo MIT；数据 OSM ODbL；免 key/无请求上限/商用允许；署名 "OpenFreeMap © OpenMapTiles Data from OpenStreetMap"（OpenFreeMap 部分推荐但非必须）；公共实例离线下载 ≤3000 tiles/50MB/4 并发。
- AWS Terrain Tiles / joerd attribution.md：署名=多源清单（ArcticDEM/Australia SRTM 1"…/Austria/Canada/EU-DEM/ETOPO1/Mexico/NZ/Norway/UK/US 3DEP+GMTED2010+SRTM），全文存 `attribution_detail`；垂直基准混合（Skadi=EGM96）→ qualification=display_only。
- 既有 provider（OSM/Carto/ESRI/OpenTopoMap/高德/天地图）许可按各自 ToS 页补全字段（license_url/tos_url）。
