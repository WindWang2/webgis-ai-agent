# PROTOTYPE — GeoLibre WASM 工具箱物理 PoC（丢弃式原型）

wayfinder 票据「最小 PoC——浏览器手动驱动跑通 1–2 个算子」的实证产物。**非生产代码**。
运行方式：`cd poc && npm install && node build.mjs && python3 -m http.server 8899` → 浏览器开
`http://localhost:8899/index.html`，依次点 ①②③④。样本数据由 `gen_samples.py` 生成（用主仓 .venv 的 rasterio/numpy/pyproj）。

## 实测数据（2026-08-28，本机 headless Chromium）

| 步骤 | 测量 |
|---|---|
| wasm 加载+编译 | `geolibre-cli.wasm` 23,828,023 B（localhost 无压缩；CDN gzip ≈7.53 MiB，见 docs/research/geolibre-wasm-toolbox-consumption.md）· 编译 **371–380 ms** · 懒加载（worker 动态 import）实证可行 |
| 工具清单 | **1063** 个工具（与调研一致）· `listManifests()` 离线返回参数 schema（input/stations/height/output + defaults），manifest 即参数权威源 |
| dissolve（矢量） | 400 面 / 200 KiB → 1 面 / 26.6 KiB · **19.2–19.3 s**（冷启动含一次性初始化；GeoLibre 自家案例 290 面 ~60 s 同量级）· 回传体积 ≈26.6 KiB |
| viewshed（地形） | DEM 300×300 / 352 KiB → COG 2.0 KiB · **251 ms** · 835/90000 可见像元 · overlay 画布实画（557×566 px 屏幕矩形）· 回传体积 ≈2.0 KiB |

## 结论（ADR 证据）

1. **物理路线成立**：浏览器懒加载单体 wasm + 数据 bytes 进 `/work` + 算子执行 + 结果回传体积小（26.6 KiB / 2.0 KiB）——「算在浏览器、状态在服务端」的回流路径体量无障碍。
2. **栅格/地形算子快**（viewshed 251 ms@300²），**矢量重拓扑算子慢**（dissolve 19 s@400 面）：类目白名单应以地形/水文/LiDAR/栅格为主；矢量拓扑类不适合进客户端白名单（与暴露策略票的类目选择一致，并为其增加注脚）。
3. **manifest 驱动注册可行**：参数 schema 结构化完整（required/io_role/schema/defaults/examples），且经典 Whitebox 文档的参数名不可靠（如 viewshed 用 `--input` 而非 `--dem`、`stations` 是矢量文件非内联坐标）——动态生成注册必须以 manifest 为权威源。
4. `upsert_layer` 薄暴露为**静态核实**（`UpsertLayerIntent` 已存在于 lifecycle_engine，mutations API 只差一个 action 分支）；动态端到端（upload→ref→挂层→raster-tiles）依赖的是既有生产端点，留待实现 effort。
5. 已知瑕疵：viewshed 可见像元数（835/90000）未与参考实现（QGIS/服务端）对照——按地图雾项约定，仅当 ADR 有争议时才补对照。

截图：`final-screenshot.png`。
