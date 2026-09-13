# ADR-0155: 自适应符号律与运行时表达力（adaptive-cartography/06-symbol-law-runtime）

日期：2026-09-13 · 状态：Accepted · 线：adaptive-cartography/06-symbol-law-runtime

## 背景

前端渲染存在三类结构性缺口：

1. **符号律几乎不存在**：`renderer.ts` 中 `fill-opacity` 硬编码 0.8、
   `circle-radius` 硬编码 6；`resolveHeatmapRadiusPx` 产出**常量 px**
   —— 与要素数量、几何类型、zoom 全都无关（「放大后点还是那么大」
   「1 万个点挤成一团」）。
2. **属性级更新缺失**：paint/layout 任一变化即整层 recompile
   （remove + add，ADR-0036 Q3 的粗粒度策略），改色整层闪烁；
   ADR-0091 只给 filter 开了 setFilter 快路径。
3. **表达力白名单缺口**：headless 编译器的 paint if/else 链没有
   symbol 分支（静默空 paint）、interpolate 仅 `["linear"]`、
   无 dash/blur/translate、无 background/hillshade；未知 source 类型
   静默降级为空 FeatureCollection；diff 深比较递归整个 inline
   GeoJSON（10k 要素 6–10ms/次）；`syncLayerZOrder` 逐层 moveLayer
   （#692 记账的 O(n·m)）。

同时 03 线（ADR-0152）冻结了 legend_spec v2 additive schema，
前端需要 paint 侧投影消费方。

## 决策

### D1 — 符号律单一事实源 `symbol-law.ts`（P1）

新增 `frontend/lib/map-kit/symbol-law.ts`：点径/线宽/热力半径/描边宽/
不透明度写成 **f(zoom, featureCount)** 的纯函数，产出 MapLibre
zoom-interpolate 表达式；zoom 维度留在表达式内（相机平滑插值），
密度维度在编译期烘焙进停靠点。出厂默认值表 `DEFAULT_SYMBOL_LAW`
可逐项覆盖；**显式用户意图（spec/命令显式给值）永远优先于符号律**。
密度信号 `densitySignal`（别名 `density_signal`）是全仓唯一一份，
05 线（labels）按 §8 协调点 import 消费；符号绝对基准值归本线，
05 线只给 size_ratio。

双路径接线：headless 编译器（compileMapSpec law 兜底）与 live 渲染
（renderer.addThematicLayer / addNativeHeatmap / paint-bridge 兜底）
消费同一符号律 —— 双路径方言不再漂移。
`resolveHeatmapRadiusPx` 保持为**契约归一函数**（显式 radius_px 优先、
legacy 4–60 窗口、缺省 30），其产物作为符号律表达式的 zoom=8 锚点。

### D2 — 密度自适应表达切换（P2）

`resolveDensityPresentation`：点层 ≥ 5000（VIEWPORT_RENDER_BUDGET /
MVT 阈值基准线）自动聚合（MapLibre cluster 范式，逐点交互在非簇点
保留），≥ 20000 编译为 heatmap 表达；线层 ≥ 5000 宽度降档 +
MVT 简化通道 evidence。显式 cluster 配置优先；阈值可配；全部切换写
runtime evidence（有界环 + 计数）。面密度语义仍归分类/抽稀通道。

### D3 — 属性级增量更新，recompile 降为安全网（P3）

diffSpecs 新增 `paint` / `layout` patch kinds：键级分解
（`diffLayerKeys`）单次走查同时完成「是否变化」判定与 patch 分类；
paint-only / layout-only 走 `setPaintProperty` / `setLayoutProperty`
（零 remove/add，z-order 不重跑）；结构性字段（type/source/cluster/
label/…）保持 recompile。运行时 patch 失败（MapLibre 拒绝表达式 /
层不在图上）**回落 recompile**（§0.5 契约），降级计数 +
`incremental-fallback` evidence，收敛性不劣于旧路径。ADR-0036 Q3
的粗粒度策略就此收窄：recompile 不再是 paint 变化的唯一出路。

### D4 — 表达力补齐走 schema 单一真相（P4）

类型扩展**不改手写 TS**：`app/lib/cartography/mapspec_schema.py`
（LayerType += background/hillshade；新增 RasterDemMapSpecSource）
+ 生成器 `ts_projection.py`（interpolate 增 `interpolation`
exponential/cubic-bezier 变体；paint 已知键面 += dashArray/blur/
translate/translateAnchor/outlineWidth），`python -m
app.lib.cartography.ts_projection` 再生成，幂等由契约测试锁定。
编译器/桥补 symbol（color/opacity→text-*）、background（无源层，
source="" 哨兵省略）、hillshade（raster-dem 源；canonical opacity →
exaggeration）分支与 dash/blur/translate 键；`field:"zoom"` 编译为
相机插值。**未知 source 类型 → UNKNOWN_SOURCE_TYPE 编译错误 +
evidence**（静默空 FeatureCollection 废除）；MapLibre 契约不存在的
`fill-outline-width` 与 heatmap 的对象颜色等无法映射的表达 → evidence
而非静默丢弃。

### D5 — diff 与 z-order 性能（P5）

源 diff 双短路：`content_revision` 相等（同数据版本）→ 跳过
inlineData 比较；WeakMap 身份缓存的对象指纹（FNV-1a）快败 ——
**指纹只作「不等」的证明，绝不作「相等」的证明**（相等仍走全量比较，
正确性优先）。10k 要素实测：最坏全量走查 6.23ms→0.002ms，
等价重建 9.68ms→0.005ms。`syncLayerZOrder` 改最小移动集
（LDS 保留集 + 锚定插入）：moveLayer 次数 = |期望| − |LDS|，
顺序不变时为 0；最终栈序与旧实现逐位等价。

### D6 — legend_spec v2 投影（P6）

`thematic-paint.ts` 消费 03 线 v2 additive 字段：`out_of_range`
（clip_p99 裁剪尾）→ case 包装显式映射 `out_of_range.color`
（nodata guard 保持最外层）；`unit`/`k`/`method`/`palette_id`/`why`/
`nodata_label`/`out_of_range_label`/`context` 为图例/裁决元数据，
记 `legend-v2-metadata` evidence（不静默丢弃），07 线图例渲染消费
同一 spec；`clip_policy: log` 由后端分类前变换承载，paint 侧不再变换。
03 线未合入期间按其冻结 schema 的本地快照
（`docs/dev/ac-06-legend-spec-v2.schema.json`）fixture 驱动测试。

## 后果

- 改色/样式微调零层闪烁（事件计数断言锁定）；10k 要素 diff 热点消除。
- 符号随 zoom/密度连续变化；密集点层自动聚合/热力化，可由 evidence 审计。
- background/hillshade/raster-dem 打开地形与底色表达；未知源显式报错，
  上游可诊断。
- 兼容性：v1 legend_spec 产出逐字节不变；显式 paint 值全部优先保留；
  recompile 路径完整保留为回落；compile_10k 耗时持平（±10% 内）。
- `fill-outline-width` 无 MapLibre 契约 —— 上游应改用 strokeColor/
  描边层；evidence 已披露该键的所有出现。
