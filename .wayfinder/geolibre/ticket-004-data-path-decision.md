# Ticket: v1 数据进入浏览器的路径决策

**Label**: `wayfinder:grilling` | **Type**: HITL | **Status**: closed
**Blocked by**: ticket-002-wasm-io-formats.md

## Question

浏览器端 WASM 算子的输入数据走哪条（或哪几条）路径作为 v1 范围：(a) 服务端 ref_id 取数 → GeoJSON 下发；(b) 云原生格式 HTTP range 直取（GeoParquet/COG/PMTiles，data_fabric 已有适配器）；(c) 用户上传件不经服务端直传浏览器？决策需兼顾格式兼容（ticket-002 事实）、体量上限、与 Fetch-on-Demand/ref_id 模式的关系。

## Resolution

**v1 = 服务端中介字节直传（选项 a）**。矢量走 ref_id GeoJSON（零转换最短路径，依据 [geolibre-wasm-io-formats.md](../../docs/research/geolibre-wasm-io-formats.md)）；栅格/点云走上传件单文件原样字节（GeoTIFF/COG/GPKG/FGB/LAS 直喂虚拟 FS）；需转换的格式（CSV/SHP/PMTiles）由服务端先转 GeoJSON 再下发。体量护栏（矢量 ~10⁵ 要素、栅格 ~4000×4000）之上的请求回落服务端 Celery 路径——护栏的具体执行位置归「算子子集暴露与 ToolRegistry 分类策略」票。

处置其余选项：(b) COG/GeoParquet 字节范围窗口提取是真实优化但不在关键路径，落雾待 v2；(c) 上传直通浏览器与 session 所有权/ref_id/权限链冲突，出局（已记 Out of scope）。
