# Ticket: WASM 算子输入输出格式与数据体量约束调研

**Label**: `wayfinder:research` | **Type**: AFK | **Status**: closed
**Blocked by**: —

## Question

Whitebox WASM 算子接受哪些输入/输出格式（GeoJSON/SHP/GPKG/GeoTIFF/COG/GeoParquet…）？数据如何物理进入 WASM 运行时（虚拟 FS？buffer？）？代表性算子（viewshed/DEM 水文/矢量 buffer）的典型输入输出体量是多少？GeoLibre 自己如何在浏览器内把用户数据喂给工具？对照本项目数据源（服务端上传件、ref_id 取数、data_fabric 的 GeoParquet/PMTiles/S3），哪些传输路径看起来可行？

## Resolution

调研完成，完整结论与来源见 [geolibre-wasm-io-formats.md](../../docs/research/geolibre-wasm-io-formats.md)。核心结论：工具层（npm `geolibre-wasm/tools`，WASI，实测 1063 个工具）按扩展名读写——矢量 GeoJSON/Shapefile/FlatGeobuf/GeoPackage/GeoParquet/KML 等、栅格 GeoTIFF/BigTIFF/COG（输出一律 COG）、LiDAR LAS/LAZ，Whitebox 传统 `.dep`/`.tas`/`.taudem` 在 next-gen 重写后已不存在。数据进入方式是纯内存虚拟文件系统：JS 把 `Record<文件名, Uint8Array>` 写进 `browser_wasi_shim` 的 `PreopenDirectory("/work")`，工具经 `std::fs` 读写 /work，运行在专用 Web Worker（运行器 ~23 MB，首次懒加载），每次 run 全新 /work；不用 OPFS，Service Worker 仅缓存底图瓦片，受 WASM ~4 GiB 内存硬约束。体量上中小数据可行（本机实测：2048² DEM 地形/水文工具各 1-4 s、viewshed 26 s、4096² slope 15 s；瓶颈在矢量 union 与稠密切瓦片——10k dissolve >9 min、稠密面 vector_to_pmtiles 直接 WASM OOM），且 `extract_cog_subset`/`pmtiles_extract` 支持 HTTP 字节范围只取窗口。对本项目：**ref_id GeoJSON 是零转换的最短路径**，S3 COG 经字节范围窗口提取最优、服务端 GeoTIFF 可原样直喂，GPKG/FGB/GeoParquet 单文件直喂，SHP 需解包多文件、CSV 与 MVT-PMTiles 必须先转 GeoJSON；栅格结果（COG 字节）需经 `write_pmtiles`/`raster_to_tiles` 浏览器内转瓦片或回传服务端 raster_tile_service 才能回显。
