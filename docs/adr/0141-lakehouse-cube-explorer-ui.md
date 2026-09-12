# ADR-0141: Lakehouse Cube Explorer UI — 信息架构与上图管线选型

- status: Accepted
- date: 2026-09-11
- relates-to: ADR-0135 (Lakehouse V8 Versioned Cubes), ADR-0118 (Spatial Data Lakehouse & Cube V6), ADR-0122 (Spatial Lakehouse V7)
- line: feat/lakehouse-ui-v9（纯前端线，后端零改动）

## Context

Lakehouse V8（ADR-0135）交付了 29 个 REST 端点（对象/cube 17 + dataset
版本层 12），但前端 `grep -rn lakehouse frontend/{lib,components,app}`
为 0 —— 能力只在 agent 工具链可达，分析师无法浏览、查询、对比、发布
cube。README Phase 6「动态栅格图层」亦无着落。本线把 29 个端点变成
一个专业面板。

## Decision

### 1. 信息架构：单 tab + 六子页签

一个「数据湖」rail tab（explore/analyze 模式词表内），内部六子页签
按任务动词组织，而非按后端资源组织：

| 子页签 | 承载端点 | 任务动词 |
|---|---|---|
| 目录 | GET catalog + GET objects/{id} | 浏览/检视 |
| 数据集 | datasets 12 端点中 GET 面 | 版本层浏览 |
| 查询 | cubes/window、cubes/labeled/window、vector/scan、cubes、cubes/revise、cubes/rs | 查询/构建 |
| STAC | GET catalog/stac | 互操作检索 |
| 发布 | publish、revoke（+ 前端本地快照 diff） | 共享/对比 |
| 运维 | verify、scrub、gc/plan、lineage（只读） | 治理检视 |

理由：280–420px 侧栏里按后端资源（objects/cubes/datasets）平铺会让
用户面对模型而不是任务；gc/retention execute 显式不进 UI（归 C 线
闭环与 F 线治理面），运维页是纯只读检视面。

**跨子页签动线**：目录卡片「查询」把 object_id 带给查询页（经
manifest `payload.ref` 解析 cube ref，解析不出诚实提示 —— object id
（64hex manifest 身份）与 `ref:cube/<16hex>` 是不同身份，绝不臆测映射）；
「血缘」带 object_id 给运维页。

### 2. 栅格上图：客户端 canvas 渲染 + 既有 HeatmapRasterSource 通道

README 贡献红线「No Raster Push：后端不生图片，渲染交给前端」。cube
window / labeled window 返回 JSON 嵌套数组（ndarray `.tolist()`），本线
不新增后端瓦片端点，选型为：

- `lib/map-kit/raster-canvas.ts`：统计（nodata 剔除）→ min-max 线性拉伸 →
  定点色带（viridis/inferno/grayscale）→ `Uint8ClampedArray` → canvas
  PNG data URL → 封装为既有 `HeatmapRasterSource {image, bbox}` ——
  mapspec adapter 已有的 raster source 族，零渲染管线改动；
- nodata 掩膜是渲染一级公民（per-variable nodata，V8 cube v3
  `nodata_per_variable` 契约），掩膜像元 alpha=0；
- 矢量结果上图走既有双管线：≤5000 要素 GeoJSON 直挂、>5000 MVT
  （`VECTOR_TILE_THRESHOLD`），本线只挂 GeoJSON（扫描有 max_rows≤200k
  预算，实际上图样本远低于阈值时直挂）。

### 3. 时序播放器：懒加载 LRU + 丢帧策略（P7 / README Phase 6 兑现）

- `lib/map-kit/raster-timeline.ts`：`LruCache`（已渲染帧位图与切片，
  容量 24）+ `createTimelineLoader`（滑动窗口预取 4 帧 + in-flight 去重
  + 越界 typed 拒绝）；
- 播放循环 rAF 驱动，`shouldRenderFrame` 丢帧策略：渲染超预算（16ms）
  2 倍且帧间隔不足时跳帧 —— 卡顿时丢帧保交互，不堆积任务；
- reduced-motion：禁自动播放（初始即暂停），仅手动步进；
- 键盘可达：Space 播放/暂停、←/→ 步进、range slider 双端；
- colorbar 图例与帧渲染共用同一 `COLOR_RAMP` 常量（联动同一真相）；
- 性能门禁（§5「≥48 步无主线程卡顿」）分三层测试：丢帧策略确定性
  单测；加载器请求数/缓存有界确定性单测；绝对 p95<16ms 断言只在
  专用进程（`LAKEHOUSE_PERF_DEDICATED=1`）执行 —— 并发全量跑下
  wall-time 本质抖动，常规跑显式 SKIP 打印实测值（不静默），专用取证
  由 S2 台账承载。

### 4. 类型与测试策略

- `lib/api/lakehouse.ts`：29 端点全量 typed client，响应类型按服务层
  dict 实测（S1 勘察）逐字段对齐 —— 关键诚实形态：catalog `total` 是
  字符串（`">=10000"` 下界）、DurableBlock `published:false` 时其余键
  缺失（全 Optional）、verify `state` 是开放联合、STAC 是
  `{collection, items, skipped}` 包装；
- 本仓无 msw：fixtures 按既有惯例（`vi.stubGlobal('fetch')` + 组件层
  `vi.mock` 模块）建于 `test/lakehouse/fixtures.ts`，正常/空/错误三态
  × 29 端点；
- 错误分支按 HTTP status（typed code 不出后端，只有 `{detail}`）。

## Consequences

- **零后端改动**：`git diff origin/master -- app/ migrations/` 为空；
  端点缺口（catalog bbox/tags 参数未暴露、无快照 diff 端点、GC/retention
  无预估释放字节量字段）全部在前端诚实降级并记 PR 协调点；
- **回滚 = 删一行注册**：tab 注册物理上是 4 处 append 行（LeftTab 词表、
  RAIL_GROUPS、MODE_TABS、PANEL_META + 渲染分支），删除即整体下线；
- 服务器路径类字段（`path`、`content_fingerprints` 键）UI 不展示；
- gc plan 的 `_scan` 内部缓存巨大，前端类型容忍但忽略。
