# G3D01 Baseline — 开源底图 / DEM 资源服务与场景数据底座

- **基线 SHA**：`1115d683a5c8343e44d8a9777e4ce05e073c29e5`（= fetch 后 `origin/master`，2026-10-26）
- **分支**：`zcode/g3d01-open-basemap-dem-v1`（独占）
- **Worktree**：`../webgis-wt-g3d01-open-basemap-dem`
- **开放 PR**：0 个（`gh pr list --state open` 为空 → 无重叠冲突）
- **开放 issue**：#1694（Swarm 批量审计聚合）—— 工作单明确不在授权范围
- **近期合并**：#1763 模板资产生命周期、#1762 任务级验收、#1761 工具能力对账、#1760 科学适用性、#1759 数据集资格、#1758 issue 清偿、#1755/1756 导出一致性/Pi 资格、#1621/1622/1623 恢复/RenderWork/Interaction IR、#1617 语义缩放 —— 均已合入本基线，必须复用。

## 实测现状（file:line 证据）

### 底图（Basemap）
| 能力 | 位置 | 状态 |
|---|---|---|
| 前端 provider 注册表 | `frontend/lib/providers.ts:180` `TILE_PROVIDERS`（12 远程 + local-xyz） | 成熟但无治理 |
| LLM 感知目录 | `app/core/base_layers.py:16` `BASE_LAYER_CATALOG` | 与 providers.ts 手工双真相 |
| 署名 | `map-panel.tsx:163` 对所有 raster 硬编码 `© OpenStreetMap…`，忽略 `provider.attribution`；`attributionControl={false}` L1485 | **破损** |
| styleEpoch | `runtime.ts:215-236 invalidateStyle()`，ACK 携带 `style_epoch`（render-apply-ack.ts:312） | 成熟 |
| 底图自愈 | `map-panel.tsx:827-846` 空画布 watchdog | 部分（无按 profile 降级链） |
| 瓦片错误处理 | `map-panel.tsx:273-296` 非致命 warn 吞掉，无自动降级 | 缺口 |
| 环境诚实缺席 | Tianditu token L44-48 / local-xyz L164-177 / air_gapped 过滤 L200 | 成熟 |

### 地形 / DEM
| 能力 | 位置 | 状态 |
|---|---|---|
| Terrarium 编解码 | `app/lib/cartography/terrain_encoding.py:70/100`（fail-closed 守卫） | 成熟 |
| DEM→tile | `raster_tile_service.py:548 render_terrarium_tile`（单波段/CRS/nodata fail-closed） | 成熟（仅会话 ref） |
| 服务路由 | `layer.py:749-794 /layers/data/{ref_id}/terrain-tiles/{z}/{x}/{y}.png`（会话所有权 SEC-08） | 成熟 |
| Scene 绑定 | `mapspec_schema.py:244 TerrainSceneSpec.source`→raster-dem source；coordinator.py:153-181 双门 | 成熟 |
| **默认地形源** | `renderer.ts:1150` 硬编码 `https://s3.amazonaws.com/elevation-tiles-prod/...`，浏览器直连，游离 egress 治理（network_dependency.py:338-342 自认） | **主要缺口** |
| 开放 DEM provider | 无（planetary_computer_stac.yaml 声明 cop-dem-glo-30/alos-dem 仅作 STAC 数据集，未接 terrain 管线） | 缺口 |

### 瓦片源类型
- **XYZ raster**：成熟（providers.ts + `raster_tile_service.render_raster_tile`）。
- **MVT**：成熟（`tile_service.py:74` PostGIS/fallback 双路径 + 前端 `addVectorTileSource`）。
- **PMTiles**：`pmtiles_adapter.py:284` 完整 v3 reader（`read_tile_bytes` L489）**但无 HTTP tile 路由**；前端无 PMTiles 客户端；compiler 对 `pmtiles` 类型直接 `UNKNOWN_SOURCE_TYPE`（compiler.ts:224）。
- **WMS/WMTS**：adapter 仅元数据（GetCapabilities 解析），无 tile URL 派生。
- **COG**：`cog_adapter.py` 仅探查/描述，栅格经 materialization→会话 ref。
- **TileJSON**：不存在。

### 治理 / 安全 / 缓存
- Data Fabric `source_registry.py`：`SourceDefinition` 含 license/quota/auth(env-key-only)/verified/priority/fallbacks —— **成熟，但底图完全不在此体系内**。
- SSRF：`security.py:99 validate_url` + `SSRFSafeHTTPAdapter`（DNS pin）+ IMDS 黑名单 —— 成熟。
- 配额：`QuotaDecl.requests_per_minute/daily_max` 声明后**无任何消费者**。
- 缓存：MVT `DfTileCache`(256MB)+single-flight；raster/terrain 进程内 LRU + ETag/304；nginx 无 tile 代理缓存。

### 契约锁定机制（改动必须同步的面）
1. TS 投影门：`ts_projection.py` → `types.generated.ts`，`tests/cartography/test_ts_projection_contract.py`。
2. RenderWork corpus 三锁：`tests/cartography/render_work_corpus/{contract_manifest.json,render_work_golden.json}` + Python/TS 两侧 drift 测试。
3. ACK 词表锁：`render_apply_ack.py:38-78` + 双侧测试。
4. MapSpec 版本：`KNOWN_VERSIONS` 1.0–1.6，加法式升级 + identity upgrader 惯例。
5. CI 治理不变量：`tests/test_ci_workflow_governance_contract.py` 等 10 个 `test_ci_*`。

## 本方向将填补的缺口（与工作单 M1–M7 对齐）
1. 底图/高程 provider 的 **typed profile 单一真相**（license/attribution/tos/zoom/encoding/vertical datum/synthetic 标记/缓存策略/就绪状态），机器校验与前端 providers.ts、后端 base_layers.py 一致 —— M2。
2. **OpenFreeMap 开源矢量样式**接入（合法、免 key、可自托管替代）+ 自托管 PMTiles→XYZ 服务路由 —— M3。
3. **默认 3D 地形不再静默 AWS 直连**：默认走受治理 profile；合成 DEM fixture（明示 synthetic、仅显示资格）提供纯本地路径 —— M3/M5。
4. **署名修复**：per-provider attribution 真实生效 —— M2/M4。
5. 瓦片失败 → **typed 降级证据**（scene evidence ring），不白屏、不吞错 —— M4。
6. 负例：SSRF/许可缺失/编码错误/不可用源全部拒绝或显式 unavailable —— M6/M7。

## 环境基线（执行前实测）
- 系统 python 3.14.8（超出 `>=3.12,<3.14`）→ 需 uv + CPython 3.13 venv。
- 前端无 node_modules；pnpm 经 corepack（v10）。
- GDAL 3.13.3 系统库存在；rasterio 走 wheel。
- 测试无需 Postgres/Redis（conftest SQLite + fakeredis + memory broker）。
