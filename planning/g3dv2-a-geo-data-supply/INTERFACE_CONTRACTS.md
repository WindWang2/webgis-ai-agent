# INTERFACE_CONTRACTS — g3dv2-a-geo-data-supply

发布给 B/C 的接口只有类型/schema/端点契约 + 契约测试，不要求对方先合并。

## 1. `app/contracts/terrain_sources.py`（新，跨方 schema 发布点）

```python
class TerrainSourceQualificationEnum(str, Enum):
    analysis_grade = "analysis_grade"      # 全部资格条件满足（对齐 basemap_profiles 词表）
    display_only = "display_only"          # 可渲染，不可作科学分析输入
    unavailable = "unavailable"            # 不可作为 terrain 源（诚实拒绝）

class QualificationReason(BaseModel):      # 每条不合格因素可编程消费
    code: str                              # TERRAIN_INGEST_NO_CRS / UNKNOWN_VERTICAL_DATUM /
                                           # TERRAIN_REQUIRES_SINGLE_BAND / ROTATED_GRID_UNSUPPORTED /
                                           # SYNTHETIC_DEM_DISPLAY_ONLY / UNIT_ASSUMED_M /
                                           # ELEVATION_OUTLIERS / NON_FINITE_PIXELS …
    detail: str

class TerrainSourceReceipt(BaseModel):     # 机器可读处理回执（摄入期一锤定音，渲染不再变义）
    qualification: TerrainSourceQualificationEnum
    reasons: list[QualificationReason]
    crs: str | None; bbox_epsg4326: tuple[float,float,float,float] | None
    width: int; height: int; ground_sample_distance_m: float | None
    vertical_unit: Literal["m"]; vertical_unit_source: Literal["declared","geotiff_tag","assumed"]
    vertical_datum: str | None; vertical_datum_source: Literal["declared","geotiff_tag","unavailable"]
    unit_conversion: UnitConversionEvidence | None   # {from_unit, factor, input_sha256, output_sha256}
    nodata: float | None; nodata_hole_fraction: float
    non_finite_pixels: int; outlier_pixels: int; outlier_threshold_m: tuple[float,float]
    elevation_min_m: float | None; elevation_max_m: float | None
    synthetic: bool; content_sha256: str
```

## 2. HTTP 端点（layer.py / upload.py，全部会话鉴权先于任何缓存）

| 端点 | 方法 | 语义 |
|---|---|---|
| `/api/v1/layers/data/{ref}/terrain-tiles.json` | GET | TileJSON 3.0.0 核心 + `terrain` 扩展块（encoding/vertical_unit/vertical_datum/qualification/synthetic/receipt 摘要）；auth=session_id query + (X-Session-Token 或 ?token=) |
| `/api/v1/layers/terrain-sources` | GET | typed 可用性摘要：static 高程画像（含 deployment 可用性投影）+ 本会话 DEM ref（qualification/reasons 透传）；`map_resources` 总摘要含 3D terrain 可用性解释 |
| `/api/v1/upload`（扩展） | POST | 新 Form：`vertical_datum`、`vertical_unit`（可选声明）；raster+register_ref → `session_ref` + `terrain`（receipt）进响应 |
| `/api/v1/layers/data/{ref}/terrain-tiles/{z}/{x}/{y}.png`（扩展） | GET | 加法式 `?token=` 匿名支持（头仍在，优先头） |

## 3. 429 限流契约（三瓦片路由）

`Retry-After: <seconds>` + `{"code": "TILE_RATE_LIMITED", "scope": ..., "limit_rpm": N}`；键：catalog 瓦片=(org/user, item_id)，会话 terrain=(session_id, ref)。env：`DATA_FABRIC_TILE_RPM`（默认 600）、`SESSION_TERRAIN_TILE_RPM`（默认 1200）；0=禁用。

## 4. 对 C（get_3d_capabilities）的对接面（仅 schema，不改其代码）

摘要 API 的 `map_resources.elevation[].source_id` 可直接映射 C 的 `dem_source_ids` 候选；`qualification != "unavailable"` 的会话源即"可绑定 spec 的 raster-dem 候选"。契约测试锁定 response 形状（tests/unit/test_terrain_sources_summary.py）。

## 5. 对 B（前端 scene3d）的对接面

TileJSON 即 MapLibre `raster-dem` source 的机器可读入口；`tiles[0]` 可直接填 `RasterDemMapSpecSource.url` 模板。encoding 恒为 "terrarium"。
