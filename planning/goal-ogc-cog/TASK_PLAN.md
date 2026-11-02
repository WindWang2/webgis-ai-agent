# TASK_PLAN —— GOAL-02 OGC/COG/远程数据服务

分支 `zcode/goal-ogc-cog-data-services-v1`；基线 `6c5e71c2c`。每阶段：失败测试先行 → 最小实现 → 聚焦测试 → 回归 → commit → 刷账本。

## 实施阶段（与 goal 里程碑映射）

### P1 = M1+M7(fixture) 能力发现
- `tests/data/ogc_service_fixtures.py`：FakeSourceServer 增加 add_ogc_tiles / add_ogc_coverages / add_stac_cog（canned 最小真响应）。
- `app/services/data_fabric/adapters/ogc_tiles_adapter.py`：read-only discovery（conformance→collections→tilesets→TMS descriptor），三态 capability，版本化 tileset descriptor（metadata.vocab 版本字段）。
- `app/services/data_fabric/adapters/ogc_coverages_adapter.py`：domainset/rangetype/格式声明发现，三态。
- 接线：registry.py AdapterSpec ×2、source_registry.KNOWN_PROTOCOLS、query/capabilities.py 默认矩阵、fabric/probing.py。
- 测试：fixture 真响应往返（probe/list/describe）、CRS 缺失→None 不伪造、未声明 conformance→unknown、恶意响应（4xx/5xx/超大）typed 拒绝。

### P2 = M2 Tiles 消费
- `app/services/data_fabric/ogc_tiles_proxy.py`：瓦片代理（治理 adapter→远端瓦片 bounded_get→DfTileCache 前缀 `ogc-tiles:`→ETag 内容寻址→typed reason code）。
- route：`GET /data-fabric/catalog/{item_id}/ogc-tiles/{tileset_id}/{z}/{x}/{y}`（routes/data_fabric.py 追加；鉴权先于缓存）。
- MapSpec 桥：`mapspec_source.store_data` 增加 `type:"vector"` passthrough 分支（最小兼容扩展，schema/前端已有）；raster tileset 用 data_fabric url 模板（今日已有路径）。
- 资格门：仅 WebMercatorQuad；范围外/未声明媒体类型/401/403/429/5xx→reason code；agent 可解释。
- 测试：代理服务单测（缓存 hit/miss、ETag、304、404→204、字节帽、SSRF 同源）、MapSpec vector source 落库+schema 校验、前端 runtime vector 分支 vitest、E2E fixture→MapSpec 断言。

### P3 = M3 Coverage 受控读取
- `app/services/data_fabric/coverage_read.py`：subset 参数（bbox/subset/波段/size 预算）→ 服务声明格式（COVJSON 解析 / GeoTIFF 字节）→ bounded（max cells/bytes）→ COG 写出 → publish_cog_data_object + session ref。
- 工具 `read_coverage_subset`（含 reason code：oversize/unsupported_format/compressed_bomb/slow/no_permission）。
- 测试：COVJSON fixture 真值对照（像元/nodata/transform）、GeoTIFF 输出路径、预算拒绝、格式未声明拒绝。

### P4 = M4 STAC/COG 物化
- `app/services/data_fabric/cog_materialization.py`：discover（stac search→assets）→ select（asset key/bands/AOI/time）→ preview/validate（validate_remote_href + rasterio 头 + cog_structure_ok）→ bounded materialize（RemoteReadSession + 取消）→ publish（CAS 幂等 + fingerprint + provenance + 签名 URL 脱敏）。
- 工具 `materialize_remote_raster_asset`。
- 测试：本地 HTTP COG 服务器（127.0.0.1 线程 http.server + allow_private=True 部署语义）真实 /vsicurl 物化、幂等重跑、AOI 窗口只读预算、取消、404/429 fail-closed、secret 脱敏断言。

### P5 = M5 工具与资格
- 工具 `inspect_data_service`（URL→只读发现→三态能力报告 + descriptor 版本）。
- categories.py 归类 + descriptor 纪律（tier/side_effect/produced_refs/network）。
- G01 桥：tile-only 源 → display-only 资格（不可 numeric raster）；coverage/COG ref → numeric raster eligible。`#1868` prepare 接受本方向 ref descriptor（验证 inspect 收据路径兼容，不重写工作台）。
- 工具名查重与 capability registry 登记。

### P6 = M6 UI 最小
- add-source-form SOURCE_TYPES += ogc_api_tiles/ogc_api_coverages；数据源列表展示三态能力 badge；i18n zh/en。不改工作台。

### P7 = M8 性能与科学正确性
- 物化回执带 requests/bytes/cache_hit 账本；窗口读采样与 fixture 真值对照（nodata/transform/值域/时间窗）；E2E 断言 ref 不灌 LLM。

### P8 = M9 闭环 + review + PR
- 3 场景 E2E：①Tiles 行政底图叠加本地小学 ②STAC COG 区域指标计算并出图 ③Coverage 无能力声明诚实拒绝。
- 独立 review（subagent C）→ fix → re-review；关键测试双跑；PR。

## 验收 Oracle（可测量）
见 goal §3；落地为 tests/ 下聚焦测试 + EVIDENCE.md 记录（命令、输出、数字）。
