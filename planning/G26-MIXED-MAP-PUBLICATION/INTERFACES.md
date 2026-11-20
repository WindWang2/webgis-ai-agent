# G26 INTERFACES — 模块边界与签名契约

## 新模块

### 1. `app/services/raster_authoring/publication_window.py`（P2 核心）

```python
@dataclass(frozen=True)
class WindowRenderResult:
    png_bytes: bytes            # RGBA PNG（含 alpha=nodata 透明；opacity 已乘入）
    bounds_wsen: tuple[float, float, float, float]  # 该 PNG 覆盖的 EPSG:4326 地理范围
    width: int; height: int     # 像素网格
    all_nodata: bool
    constant: bool
    truncated: bool             # 受 2048 边或解码预算约束降采样
    grid: str                   # "mercator-window"
    overlap: str                # "full" | "partial" | "none"
    notes: list[str]            # 近似性/CRS/旋转披露

def render_publication_window(
    path: str,                       # owner 已校验的 scoped 安全路径
    stat_key: tuple[int, int],       # (mtime_ns, size) 冻结身份；不符 → typed 拒绝
    spec_payload: dict,              # RasterExpressionSpec.model_dump(mode="json")
    view_bounds_wsen: Sequence[float],   # 页面地图范围（EPSG:4326）
    out_w: int, out_h: int,          # 目标像素网格（画布地图区）
    max_side: int = 2048,
    decode_budget_px: int = 22_000_000,  # 源窗解码像元上限（bomb 防线）
) -> WindowRenderResult
```

- 语义对齐 `render_raster_tile`：源 CRS 窗口读、冻结全局 stretch（spec 内）、
  全通道有效才绘制、masked→alpha、opacity 单点、分类强制 nearest（spec 已保证）。
- 目标网格 = EPSG:3857 仿射（与 SVG 编译器 proj_x/proj_y 同构 —— 线性 lon +
  mercator-y ⇒ 对 3857 为仿射）；源 CRS≠4326/3857 时 rasterio.warp 重投影；
  无 CRS 源 → 与瓦片服务同语义（按源网格放置 + 明确披露，不猜投影）。
- 不相交 → `overlap="none"`，透明 1×1 + notes（**不读边缘像元**）。
- 进程级 LRU：key=(path, stat_key, style_id, data_revision, bounds6, out_w, out_h)。

### 2. `app/services/raster_authoring/publication_bindings.py`（P1 冻结）

```python
@dataclass(frozen=True)
class RasterLayerBinding:
    layer_id: str
    source_id: str
    ref_id: str
    path: str                    # scoped 安全路径
    stat_key: tuple[int, int]
    spec_payload: dict           # 已冻结 RasterExpressionSpec（model_dump json）
    style_id: str
    data_revision: str
    crs: str | None
    source_bounds_wsen: list[float] | None   # 源 EPSG:4326 范围证据

async def freeze_raster_bindings(
    session_id: str, document: dict, *, expected_revision: int | None,
    owner_token: str | None = None, trusted_dispatch: bool = False,
) -> dict   # {"revision", "bindings": {layer_id: binding}, "omitted": [...], "disclosures": [...]}
```

- 复用 `resolve_committed_expression`（mapspec 一次读入）+ `resolve_source`
  （owner 三重校验）+ STALE_BINDING 复核 + stat 冻结。JSON 可序列化
  （dataclass → dict 过程在边界处显式）。

### 3. `app/services/mixed_map_export.py`（P5 服务编排）

```python
async def export_mixed_map(
    session_id: str, *, fmt: Literal["png","svg","pdf"],
    expected_revision: int | None = None, owner_token=None,
    trusted_dispatch: bool = False, target_dpi: int = 300,
    partial_policy: Literal["blocking","allow_partial"] = "blocking",
    page_profile: str | None = None, atlas: dict | None = None,
) -> dict  # {"bytes","media_type","extension","manifest",...}（persist 由 delivery 层）
```

### 4. 工具：`webgis_map_export`（app/tools/mixed_map_export_tools.py，P5）

## 修改的既有接缝（加性，最小侵入）

### A. `app/services/mapspec_to_svg.py`

`compile_mapspec_to_svg_detailed(..., raster_bindings: Mapping[str, dict] | None = None)`
（`SvgCompilation` 增加 `raster_placements: list[dict]`：`{layer_id, style_id,
data_revision, x, y, w, h, bounds_wsen, z_index, notes}` —— PDF HTML 面与
manifest 消费）。raster 分支：binding 在场 → 窗口渲染 + 按图层顺序发射
`<image href="data:image/png;base64,...">`（放置矩形 = bounds 经 proj_x/proj_y）；
binding 缺席 → 既有披露语义（不静默）。

### B. `app/services/publication_export.py`

- `_deny_url_fetcher`：放行 `data:image/png;base64,`（≤24MB/张，仅本 pipeline
  生成的 URI；返回 `URLFetcherResponse`）；**修复字体分支**同样返回
  `URLFetcherResponse`（dict 在 WeasyPrint 70 被 assert 吞掉）。
- `compile_publication_page(..., raster_bindings=None)` 透传；
  `PageCompileOutcome` 增加 `raster_placements`。
- PDF 页组装：页含 raster_placements 时按 z 序切分 SVG 片段 → HTML
  `<img>`/`<svg>` 交替层叠（E0.2 验证的形态）；无 raster 时字节不变。
- `render_publication_pdf(payload, ..., raster_bindings=None)`（worker options
  透传，JSON 可序列化）。
- 披露词表：`numeric_raster` 加入 raster 源披露循环（仅在无 binding 且无法
  嵌入时才报 `raster_layer_unavailable_vector_pdf`）。

### C. `app/lib/cartography/render_scene.py`

`derive_legend_items`：`type=="raster_rgb"` 分支（通道条目 + unit_status，
不造色标）；continuous 分支透传 `notes`/`unit`（G20 legend 已有 unit 字段，
确认透传）。

### D. HTTP：`app/api/routes/map.py`（或独立 router 挂既有前缀）

`POST /api/v1/export/mixed`（session_id + fmt + expected_revision + partial_policy
+ target_dpi + atlas/page_profile）→ 冻结 → 编译 → persist（复用
`_persist_export_file`/`_publish_export_owner`/`record_export_lineage`）→
下载走既有 `/api/v1/export/download/{filename}`。

### E. Pi：`app/tools/mixed_map_export_tools.py` 注册 `webgis_map_export`
（tier/side_effect/预算/取消按既有工具纪律；refs-only 回执 + omitted_layers +
manifest 摘要）。raster-only 工具 `webgis_raster_expression_export` 不动。

## 不改的东西

- PublicationIR 模型（页面级不加字段；raster 属于文档编译层）。
- G15/G16/G18/G20 既有 authoring/state。
- 前端 mapspec-compiler（live 视图）。
- `_namespace_slot_svg`/产品页组合（复用）。
