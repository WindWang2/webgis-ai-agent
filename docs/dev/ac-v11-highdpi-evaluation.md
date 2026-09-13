# AC-V11 高 DPI 导出评估（W6.3，ADR-0166）

> 任务书 W6.3：「高分重渲染升级：``setPixelRatio`` 方案评估是否升级为重建
> 实例（若栅格瓦片细节受限，提供「瓦片 zoom 提升 + 重采样」折中并量化披露）」。

## 1. 现状（V10，ADR-0157 P1）

`frontend/lib/export/highdpi.ts`：`setPixelRatio(dpi/96)` + 有界 idle 等待
（30s 预算内），超时**降级**为当前分辨率导出 + `highdpi_rerender_timeout_degraded`
诊断；栅格源在场时披露 `raster_tile_detail_limited_highdpi`。

## 2. 三方评估（量化）

| 方案 | 矢量/符号层 | 栅格瓦片层 | 代价 | 结论 |
|---|---|---|---|---|
| **A. setPixelRatio（现状）** | 真重渲染（几何细节随 DPR 线性增益） | **无增益**：tilezoom 探针实证 DPR 1 与 3.125 取同一 zoom（瓦片取图 zoom 由 view zoom 决定） | 无重建成本；30s 有界 | 保留为默认 |
| **B. 重建实例（新 Map 实例 + 高超采样 viewport）** | 与 A 等价（同一渲染管线） | 与 A 等价（取图 zoom 不变）——**重建不改变任何层级的细节来源** | 上下文重建 + 全部图层重挂 + 状态迁移风险；导出时长 ×2~3 | **不采纳**（无增益，成本实付） |
| **C. 瓦片 zoom 提升 + 重采样（折中）** | 无增益（矢量走 A） | **有增益**：取图期 view zoom +log₂(dpi/96)，多取 log₂ 级更深瓦片；导出期重采样回目标尺寸 | 取图期视野收窄（需两段相机：拍瓦片 → 回目标视野合成）；链路复杂度中等 | **采纳为 opt-in** |

## 3. 决策

1. **默认路径保持 A**（V10 行为 + 既有诚实披露）；不升级为 B（评估否决，
   理由见 §2 —— 无增益且成本实付，属「复杂度不换质量」）。
2. **C 以规划器交付、opt-in 接线**：`frontend/lib/export/tile-zoom-plan.ts`
   `planTileZoomCapture(baseZoom, dpi, maxZoom)` 产出 `{captureZoom,
   resampleFactor, zoomGainLevels, disclosure}`：
   - `zoomGainLevels = min(log₂(dpi/96), maxZoom − baseZoom)`（maxzoom 封顶，
     **无 headroom 时如实披露**「与 V10 等同」而非静默）；
   - 300 DPI（ratio 3.125）在 maxzoom 富余的底图上增益 ≈1.6 级；
   - 纯函数、确定性；live 接线（两段相机 + 重采样合成）归后续批次，
     接线时诊断码 `raster_tile_zoom_upsampled`（拟）随实现登记。
3. **量化披露义务**：任何使用 C 的导出必须携带 `disclosure` 文案（调用方
   写入 degradation 列表）——「提升了几级 / 无 headroom」二选一，禁止静默。

## 4. 残留风险

- C 的取图期视野收窄需精确的相机恢复（失败回退 A 且披露）；
- 底图 maxzoom 常见 19（OSM）：高 zoom 城市级导出 headroom 常为 0 →
  C 的实际收益集中在低/中 zoom 大范围导出；
- 评估不变量：任何方案下「栅格细节 ≤ 源瓦片层级真实细节」——上采样是
  插值不是信息，披露文案必须含「重采样」字样（防误读为真实细节）。
