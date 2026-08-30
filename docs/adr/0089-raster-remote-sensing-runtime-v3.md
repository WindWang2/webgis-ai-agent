# ADR-0089: Raster & Remote Sensing Runtime V3

## Status

Proposed（随 feat/raster-remote-sensing-runtime-v3 落地）

## Context

master 上的栅格/遥感能力由三条互不相通的执行路径构成，各自持有网格/
对齐/写入的私有实现：

1. **文件路径径**（`app/lib/geo_analysis/raster_math.py`）：
   `raster_calculator` 对齐输入已窗口化，但**不对齐的 B 走整幅重投影**
   （`np.full(src_a.shape)` + 全图 `reproject` + 整幅 footprint 掩膜 ——
   内存 O(A×B)）；`resample_raster` 整幅 band-to-band warp；
2. **本地 TIFF NDVI 径**（`NatureResourceAnalyzer.calculate_ndvi`）：
   `src.read(band)` 全图 ×2 + 自持一份与
   `app/services/rs/band_math.INDEX_FORMULAS` 重复的 NDVI 数学；
3. **在线 STAC 径**（`spectral_engine` + `spatial_tasks`）：内存数组的
   窗口代数 + 自制网格对齐（`_grids_pixel_aligned`），产物为统计 GeoJSON。

此外：
- `TemporalRasterEngine._validate_alignment` 是仓库里**第三份**网格判定；
- `RasterArtifactDescriptor` 缺 `transform/resolution`，网格身份不完整；
- `RasterProfile`（DatasetProfile 子结构）是死结构 —— 栅格契约验证不存在；
- `raster.algebra` 算法错挂 `raster_source`（“数据获取”）capability；
- `change_detection` capability 声明 raster 输入但唯一算法是矢量时序变化；
- 复用键只含 `raster_path` 字符串 —— 同路径 in-place 重写会错误命中。

## Decision

栅格分析从「file → tool → whole-array → output」演进为：

```
Raster Artifact → Grid Contract → Alignment Decision → Windowed Execution
→ Algorithm → Validated Artifact（descriptor + evidence + content identity）
→ Tile / MapSpec / Downstream
```

### 1. Why windowed execution（为什么窗口化）

大栅格的全图 `src.read()` 是 O(全图×工作数组) 的内存峰值，是 OOM 的
第一来源。V3 把所有文件栅格算法统一到「open → iterate windows →
read → compute → write → close」的共享底座（`raster_windowed.py`）：

- 窗口边长由 `RASTER_PROCESSING_MEMORY_MB`（默认 256MB）按
  64B/像元工作集推导（`window_side_from_budget`），不是拍脑袋的
  512×512；护栏 [64, 2048]；
- 优先源文件自然 block（`block_windows`，块 ≤ 预算时），否则固定网格；
- 窗口循环**串行**（§正确性优先），`rasterio_env()` 内
  `GDAL_NUM_THREADS=1`、`GDAL_CACHEMAX` 受预算钉住 —— 无界并行窗口
  只会放大峰值内存；
- `RasterResourceGuard` 的字节记账从“全网格工作集”改为**输出磁盘足迹**
  （float32 单波段）：窗口化后内存约束由窗口预算保证，guard 继续约束
  像素总数/维度/放大比。

### 2. Why alignment precedes pixel math（为什么对齐先于像元运算）

仅 `width==width && height==height` 绝不构成“可逐像元运算”——分辨率/
原点/CRS 不同的两个同形状栅格逐像元相减，比较的是**错位采样**，产物
看起来合法、实际是垃圾。`raster_grid.py` 收编三份自制判定为一个纯
metadata 契约：

- `RasterGridProfile`（头信息投影，零像元 IO）+ `grids_align()`：
  CRS + 仿射六参数 + 宽高全部一致才 aligned；
- `RasterAlignmentDecision`：`aligned / needs_resample /
  needs_reproject / incompatible` + 目标网格 + 重采样方法 + 理由——
  决策对象，不是数据状态；
- 足迹无交集（bounds 先换算到同一 CRS 再比较）→ `RasterAlignmentError`
  结构化拒绝，绝不静默产空垃圾栅格。

### 3. Why A is the reference grid（为什么 A 是基准网格）

`raster_calculator(a, b)` / `detect_raster_change(a, b)` 的第一个栅格是
基准（既有工具契约即如此——输出 suffix 挂在 A 上）。B 经
`aligned_reader()`（`WarpedVRT`）**虚拟对齐**：窗口读即对齐读，不落地
临时重投影栅格、不整幅进内存。对齐事实（resampled/reprojected/cropped）
进 quality evidence，绝不静默 padding。

策略：连续量默认 bilinear、分类量 nearest（分类图禁 bilinear——混合出
不存在的类别）。源未声明 nodata 时，VRT 足迹外填充哨兵 = NaN（float）/
dtype 最小值（整型）——旧实现填 0 且依赖整幅 footprint 掩膜；哨兵与
真实数据的碰撞概率远低于 0（#931 语义保持：B 足迹外 = 无效）。

已知精度取舍：未对齐 B 的重采样从 nearest（旧）改为 bilinear（新，
连续量默认）——亚像元精度更好，但与旧整幅实现逐位不同
（`ARTIFACT_VERSION_NS` 升 v2，旧缓存不命中）。

### 4. Why change_detection needs raster-specific semantics

「变化检测」此前一个词盖两种分析：
- `detect_vegetation_change`（STAC 在线，统计 GeoJSON，无栅格产物）；
- `temporal.change`（矢量时序，`change_detection` capability）。

栅格图像变化（两期栅格工件 → 对齐 → 差值 → 阈值分类 → 变化栅格）没有
实现，而 `change_detection` capability 却声明了 raster 输入 —— 注册表
在撒谎。V3 分家：

- 新 capability `raster_change_detection`（domain=raster，输入
  `raster_surface`，输出 `raster_surface`）+ 新算法
  `remote.change.raster`（native，工具 `detect_raster_change`）；
- `change_detection` capability 输入词表收窄为矢量要素集（如实）；
- `detect_raster_change` V1 是确定性基线：difference /
  absolute_difference / normalized_difference（零分母 → nodata，同 NDVI
  golden 语义）+ 可选阈值二分类（uint8 0/1/255），窗口化执行，产物带
  descriptor/evidence/指纹。不做深度学习变化检测。

变化产物复用 `raster_surface` artifact 类型 + metadata
`semantic_type=raster_change_surface`（§ 不为每个算法新增类型）。

### 5. Why ArtifactRegistry stays metadata-only

`WindowedRasterWriter` 边写边累计：统计（valid/nodata/min/max/mean）、
**内容摘要**（sha256：网格身份种子 + 窗口字节流）、descriptor（写者已知
—— 宽高/CRS/transform/dtype/nodata/band 统计，**零重开**）。这些都是
有界 metadata，进工具结果 / artifact metadata / UploadRecord 旁路；栅格
字节本体只落 GeoTIFF 文件。ArtifactRegistry 仍只记
identity/lineage/status。

内容身份两处、两种成本档：
- **写者摘要**（输出）：零额外 IO，精确到窗口字节；
- **`raster_content_fingerprint(path)`**（外部输入）：grid 身份 + ≤1024
  边降采样样本哈希，成本与一次 inspect 同级——绝不为 hash 整幅重读
  几十 GB。mtime 不是内容（§32）：指纹不含 path/mtime。

复用集成：`analysis_reuse` 生产时快照 args 中的栅格路径指纹
（`input_raster_fps`，≤4 条）进 artifact metadata；复核时重算比对——
不同 band/threshold/expression 本就换 analysis_key，**同路径重写内容**
现在也正确 miss（§34）。

### 6. Why content identity does not create a second store

指纹是**派生投影**：不落第二份文件、不建内容寻址存储、不改
`storage_ref` 语义。写者摘要只进结果 dict；路径指纹只进 artifact
metadata 的一个有界键。失效语义仍由 `analysis_key`（算法+参数）+
`ARTIFACT_VERSION_NS`（算法版本）+ 指纹（内容）三层兜底。

### 7. Why style change never recomputes raster analysis

C5（ADR-0075）已在瓦片数据面落实：`raster_tile_service` 按
（数据, 样式）二元组缓存瓦片——`RasterStyleSpec`（colormap/bands/
stretch）只换缓存键。STAC 在线径的 `emit_raster_layer →
render_array_to_png` 是**对已计算数组的纯渲染**（不重拉 STAC、不重算
指数），样式→渲染而非样式→分析。V3 的窗口化产物（tiled+LZW+有界
overview 金字塔）使低 zoom 瓦片的降采样读走 overview 而非整幅解码
（#595 的自然延伸）。本轮不改 legend/inset/chart/layout 框架。

## Registry honesty 修正（§49）

| 修正 | 之前 | 之后 |
|---|---|---|
| `raster.algebra` | capabilities=`raster_source`（获取） | `band_math`（新 capability） |
| `change_detection` cap | 输入含 raster_surface（谎报） | 矢量输入；栅格走 `raster_change_detection` |
| `raster_reclassify`/`raster_resample` | 无 capability（孤儿工具） | 各自 capability + native 算法 |
| `RasterProfile` | 死结构 | `from_raster_descriptor` 生产方 + 契约验证消费 |
| 栅格契约验证 | 不存在 | 声明 raster 族 → 网格证据缺席/不完整 finding |

## Consequences

- `raster_calculator` 任何路径（aligned/unaligned/constant）内存均
  O(window)；未对齐 B 不再整幅重投影；
- `calculate_ndvi` → `calculate_index`（API 兼容：默认 ndvi，新增
  index_type/band 角色参数），窗口化 + tiled LZW + overview 输出；
- 输出统一 `build_output_profile`（GTiff/tiled 256/LZW/nodata），
  算法不得再 copy profile 漏字段；
- 取消/磁盘失败 → `atomic_output` 清理临时文件，绝不留下“看似有效的
  半个栅格”；artifact 登记只发生在成功 finalize 之后；
- whole-read 回归守卫（`_ReadSpy`）+ 大栅格 bounded-memory 测试
  （~7200²，按本机资源自动收缩）锁住性能语义，不依赖 wall clock。

## Deferred

- STAC 在线径（`_pixel_change_classification`）的内存数组对齐保持自治
  （输入是内存数组+bounds，非文件栅格；VRT 不适用）；
- `resample_raster` 的 band-to-band warp 仍走 rasterio 内部窗口（受
  guard 约束），未迁移到 `WindowedRasterWriter`（产物语义不同：无统计/
  指纹需求）；
- 深度学习变化检测、更多光谱指数（原则：先把底座做对，不为数量发明）；
- COG 严格合规（`copy_src_overviews` 等）：当前 tiled+overview 已满足
  瓦片/检视消费，不引入额外工具链。
