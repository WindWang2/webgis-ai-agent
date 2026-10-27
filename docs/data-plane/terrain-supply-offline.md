# 地形/DEM 数据面供给运行手册（G3D-V2-A）

> 目标读者：部署运维与集成方。本文覆盖**用户 DEM 摄入 → 校验/资格 →
> Terrarium XYZ + TileJSON → 限额服务 → 可用性发现**这条数据面的三种
> 部署契约与可运行 fixture。系统级 air_gapped profile（egress 守卫、
> 本地 LLM、资产预置）见 `docs/DEPLOYMENT-offline.md`（ADR-0202），本文
> 不重复。

## 1. 数据生命周期（一段话版）

用户上传 GeoTIFF（≤200MB，魔数+GTiff 白名单）→ `POST /upload` 携带
`register_ref=true` 与可选的 `vertical_datum`/`vertical_unit` 声明 →
摄入车道做结构/单位/基准/质量校验，产出 typed 资格 receipt
（`analysis_grade | display_only | unavailable`，逐条 reason code）→
合格或仅显示的源注册为会话 raster ref（`ref:terrain-*`）→
`/layers/data/{ref}/terrain-tiles/{z}/{x}/{y}.png`（Terrarium XYZ）与
`/layers/data/{ref}/terrain-tiles.json`（TileJSON 3.0.0 + terrain 扩展块）
即可消费 → `GET /layers/terrain-sources` 给出本会话可用性摘要（Agent/
前端发现面）。

不合格绝不静默：无 CRS/多波段/旋转网格/单位不可换算/损坏 → `unavailable`
（不注册）；未知垂直基准/单位假定米/合成标记/尖峰污染 → `display_only`
（可渲染，永不进科学分析门）。非米单位（ft / US survey ft）生成换算副本
（`.dem-m.tif`）并在 receipt 留双 sha256 证据；源文件不动。

## 2. 三种部署契约

### 2.1 云联网（`DEPLOYMENT_PROFILE=cloud`，缺省）

| 能力 | 状态 |
|---|---|
| 会话 DEM（上传→瓦片→TileJSON） | offline-readable（本地渲染，无出网） |
| 远端兜底（aws-terrarium-public 等） | requires-network；`terrain_3d_status=available_with_limits`（兜底 display_only/混合基准，如实披露） |
| OpenFreeMap 矢量底图 | requires-network；许可允许浏览器缓存，**不允许**服务端镜像离线分发（公共实例限流，见画像 cache_policy） |
| 目录 PMTiles→XYZ | offline-readable（归档在服务端） |

### 2.2 普通内网（有内网出口，无公网）

- 同 2.1，但远端公共源按 `NETWORK_EGRESS_ALLOW` 白名单决定可达性；
  不可达时摘要 API 如实标 `deployment_not_permitted`/不可用。
- 自托管 PMTiles（内网 MinIO/本地目录）+ 会话 DEM 是主路径。

### 2.3 自托管离线（`DEPLOYMENT_PROFILE=air_gapped`）

- 远端高程/底图一律 `deployment_not_permitted`（前端
  terrain-governance 同步禁用 3D 兜底并记 `scene_terrain_no_source_air_gapped`
  证据）。
- **3D 地形的唯一合法路径 = 预置/上传的本地 DEM**：摘要 API 返回
  `terrain_3d_status=unavailable + NO_TERRAIN_SOURCE` 并附解释（上传
  DEM 后重试）；有会话 DEM 时 `available`。
- 合成演示 DEM（`synthetic-demo-dem`）永久 display_only，仅演示。

## 3. 配额（quota 执行层）

| env | 缺省 | 作用域 |
|---|---|---|
| `DATA_FABRIC_TILE_RPM` | 600 | 目录 MVT/PMTiles 瓦片：键=(org‖user, item) |
| `SESSION_TERRAIN_TILE_RPM` | 1200 | 会话 terrain 瓦片：键=(session, ref) |

超限 → `429` + `Retry-After` + `{"code":"TILE_RATE_LIMITED",...}`。
≤0 禁用。多 worker 部署为每 worker 独立窗口（总预算 = N × rpm，保守
方向）；出站 adapter 抓取限流 seam 已就绪
（`make_safe_session(rate_limit=(source_id, rpm))`），QuotaDecl → 各
adapter 逐源接线为后续工作（见 planning 账本 R3/R6）。

## 4. 可运行 fixture（零网络）

### 4.1 合成 DEM（演示/回归）

```bash
# 仓库内确定性合成 DEM（自描述 SYNTHETIC_DEM tag，display_only）
python -m pytest tests/unit/test_synthetic_terrain.py -q
# 端到端（MapSpec 数据面字节级解码回高程）
python -m pytest tests/cartography/test_synthetic_dem_mapspec_e2e.py -q
```

生成一份合成 DEM 文件（可直接经上传车道摄入，验证 display_only 降级链）：

```python
from app.services.synthetic_terrain import write_synthetic_dem
write_synthetic_dem("/tmp/demo-dem.tif")
```

### 4.2 真实链四段契约（upload→register→TileJSON→tile）

```bash
python -m pytest tests/unit/test_upload_raster_lane.py \
  tests/unit/test_terrain_ingest.py \
  tests/unit/test_terrain_tilejson.py \
  tests/unit/test_terrain_sources_summary.py \
  tests/unit/test_tile_rate_limiter.py -q --no-cov
```

覆盖 fixture 家族：有效 DEM（米/声明基准）、负高程（死海量级合法）、
无 datum、英尺（声明/tag 双路径换算证据）、nodata 洞、无 CRS、多波段、
非有限像素、异常尖峰、旋转网格、synthetic 强制 display_only、幂等重注册、
匿名 `?token=`、跨会话 404/越权、退役 ref、429 限流、出站 seam。

### 4.3 手工烟测（启动后端后）

```bash
# 1) 上传并注册（EGM96 声明 → analysis_grade）
curl -F "files=@my-dem.tif" -F "session_id=<sid>" \
     -F "register_ref=true" -F "vertical_datum=EGM96" -F "vertical_unit=m" \
     http://localhost:8000/api/v1/upload
#    → 响应 terrain.qualification / session_ref / tiles_url_template

# 2) TileJSON + 瓦片（认证会话；匿名会话用 &token=<owner_token>）
curl "http://localhost:8000/api/v1/layers/data/<ref>/terrain-tiles.json?session_id=<sid>"
curl "http://localhost:8000/api/v1/layers/data/<ref>/terrain-tiles/12/3373/1552.png?session_id=<sid>"

# 3) 可用性摘要（Agent/前端发现面）
curl "http://localhost:8000/api/v1/layers/terrain-sources?session_id=<sid>"
```

## 5. 对 B/C 方向的接口

- **B（渲染/前端）**：TileJSON `tiles[0]` 可直接填
  `RasterDemMapSpecSource.url`；`terrain.encoding` 恒 `terrarium`。
- **C（分析门/编排）**：`GET /layers/terrain-sources` 的
  `session_terrain_sources[].receipt.qualification` 即资格真值
  （schema：`app/contracts/terrain_sources.py`）；`analysis_grade`
  才可进量测输入。本 track 不改 `scene_orchestration_tools.py`。
