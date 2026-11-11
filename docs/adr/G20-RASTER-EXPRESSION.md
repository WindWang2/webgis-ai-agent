# G20-RASTER-EXPRESSION — 持久化数值栅格表达与真实像元检视

状态：Accepted（本任务 PR 范围内）。文件名故意**不含四位数字编号**：本批
G12–G21 并行，各任务不得抢占同一个 ADR 数字号；交叉引用按文件名解析。

## 背景

基线存在两条互不相通的栅格路径：

1. 会话 ref → `render_raster_tile` XYZ PNG（仅 `cmap` / `bands` 两个 query 参数；
   stretch 来自内部近似全局统计、重投影固定 bilinear、opacity 完全不进数据面）；
2. 数值阵列 → `raster_cartography_converter` 烘焙 PNG → `ref:raster/<id>` →
   MapSpec `type:"raster"` 的 MapLibre `image` 源（换色 = 重跑转换 = 新 PNG）。

`RasterStyleSpec` 只有测试构造点，没有生产消费链；没有 metadata / 直方图 /
像元检视路由；刷新后无法恢复用户表达；导出不携带同一套样式与图例。

## 决定

1. **MapSpec 仍是唯一权威**。不新增 mutation intent、Body codec、第二 style
   store / ID 系统 / 撤销栈 / evidence store。提交为**单笔**
   `UpsertLayerIntent(layer, source_data=<numeric_raster 源条目>)`，经
   `apply_gis_mutation` 的 session 锁、CAS `expected_revision`、checkpoint
   与 undo/redo；`mapspec_source.store_data` 的 `numeric_raster` typed
   passthrough 保证 source 与 layer 落在同一事务（独立评审 P1-3 后由最初
   的 2–3 笔改为恒 1 笔，撤销栈不再被污染、失败不留半状态）。全新会话由
   引擎 auto-init 骨架补 `version`。样式与派生图例原子一致。
2. **新增 raster 子模型 + 独立子契约版本**，不与 frame / label / flow 字段分区
   相交：
   - `NumericRasterMapSpecSource(type="numeric_raster")` 只承载数据身份
     (`refId/bounds/crs/dataRevision/dataFingerprint`)；
   - `MapSpecLayer.raster_style: RasterExpressionSpec`
     (`raster_expression.v1`)。
   - MapSpec 文档版本保持 1.9：纯 additive optional + `extra="allow"` +
     canonical 保序深拷贝使旧文档 `parse_mapspec` 结果逐位不变，无需迁移。
     子契约版本独立演进，避免与本批其它 PR 抢 1.10。生成物由
     `ts_projection.py` 重生成。
3. **样式在 layer、数据在 source**。同一 ref 的两个图层可携带不同
   `style_id`；compiler/runtime 为每个层派生 `sourceId::layerId` 的 MapLibre
   raster 源，缓存与恢复互不串色。
4. **严格表达契约**：`mode = single|rgb`（1 / 3 个 1-based 波段，允许重复波段
   做灰度复合）；两波段数据集默认单波段，显式二元 RGB 返回
   `BAND_COUNT_UNSUPPORTED`；越界 / NaN / 反向范围 / 未知色带 / 不支持的
   重采样 / 分类+插值 / RGB+色带 / 越界 opacity 都返回稳定 code + field +
   correction_hint，UI 与 Pi 同码表。分类语义只由真实元数据 tag 或用户显式
   声明提供，整数 dtype 不单独证明类别；分类栅格强制 `nearest`。
5. **统计口径随样式冻结**。自动拉伸基于 ≤2048 边的有界降采样，
   `source="bounded_sample"` + `method` + `sample_count` + `approximate=True`
   写入 `RasterStretchBounds`；`style_id` 是规范样式 JSON + 数据修订的
   sha256。缩放与导出不重新估计。`data_revision` 与工件内容指纹分开，
   不合并成「万能指纹」；样式改动不重算遥感数据。
6. **数据面消费完整样式**。`render_raster_tile(..., style=RasterRenderStyle)`
   复用既有窗口读 / overview / 跨瓦片全局归一 / 缓存 / 失效 hook；样式参数与
   数据修订进入缓存身份；`style=None` 时旧路径逐字节回归。渲染失败抛
   `RasterTileError` 并由路由返回可观测 502，不用透明 PNG 伪装空白地图。
   opacity 只在服务端瓦片应用一次。
7. **owner / 会话边界**。URL 只带 `session_id` / `layer_id` / `style_id`；
   owner token 由既有 `buildTileTransformRequest` 注入 header，绝不写进
   MapSpec / 日志 / 导出。后端 `require_owned_session` +
   `get_ref_data(owner_token=…)` + `resolve_scoped_raster_path` 三重校验，
   并以**当前 MapSpec** 比对 `style_id`，不匹配即 409。Pi 工具由 dispatcher
   注入的受信 `session_id` 走 session store 内部读（`trusted_dispatch`），
   仍受 per-session 隔离与 scope 路径校验。
8. **像元检视只读源栅格**（窗口/像元读取 + CRS 变换），不读显示 PNG、不插值、
   不推进 MapSpec 修订。范围外 / mask 洞 / 无 CRS / 数据过期是不同状态；
   RGB 的所有通道都有效才绘制与报告，避免黑色填充值冒充实测低值。
9. **图例 / 说明同源**：色带只有一份解析表（cartography hex stops ↔
   matplotlib 名称显式别名 + `_r` 反向），瓦片像素、图例色样、导出色标共用
   同一 LUT。RGB 不伪造连续物理量色标；常值 / 全 nodata 明确说明。
10. **导出冻结一个修订**：服务端生成真实可读 PNG（位图 + 图例 + 表达说明），
    SVG 以 base64 data URI 嵌入（离线不依赖 session URL），PDF 显式拒绝而非
    静默丢层；其它未渲染图层在 sidecar 的 `omitted_layers` 披露。位图 ≤2048
    边，明确声明「提高 DPI 不增加真实遥感细节」。

## 兼容

- 旧 `RasterStyleSpec` 类逐字保留；旧 XYZ tile route 零修改；旧 PNG
  `type:"raster"` image 源 / 外部底图 / 旧 `raster-opacity` 行为不变。
- 未挂载数值源的 PNG 图层不出现伪波段 / 伪像元控件；工作台只提供
  「选择已登记栅格并绑定」入口。
- 生成的 `types.generated.ts` 与 `parse_mapspec` 冷路径按仓库既有漂移闸同步。

## 未做（明确边界）

不修改 Pi pin / RPC / extension / dispatcher（G12）、资格政策（G13）、要素编辑
（G14）、画幅编排（G15）、标注避障（G16）、上传协议（G17）、数据成果交付
（G18）、字段计算（G19）、OD 流图（G21）、全局 UI 主题/布局与自动数据刷新。
不新增遥感/地形/分类算法，不重建 `terrain_profile` 或参数化批分析。
