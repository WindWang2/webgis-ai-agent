# ADR-0157: 出版级导出 V10 — 真高分渲染 · 矢量优先 PDF · 所见即所得版面

- 状态: Accepted（本 PR 落地）
- 日期: 2026-09-13
- 关联: ADR-0126（Cartographic Rendering V6 出版引擎）；ADR-0118（V5 导出 parity）
- 线: `adaptive-cartography/08-publish-export`

## Context

导出链在 V6 后仍有四类出版级缺口（P0 勘察 `docs/dev/ac-08-export-recon.md` 实证）：

1. **栅格瓦片无高分增益**：`setPixelRatio(dpi/96)` 真重渲染只增益矢量/符号层；
   tilezoom-dpr-probe 实证 MapLibre 取图 zoom 与 DPR 无关（DPR 1 与 3.125 均取
   同一 zoom）→ 栅格底图在 300 DPI 下被拉伸模糊。DPI 线宽探针实测：放大插值
   路径 0.5 CSS px 线对比度 0.253（有效线宽 2.24px，膨胀 ~4.5×），真重渲染
   0.948（0.32px）。
2. **PDF 文本层结构性缺 CJK**：jsPDF 标准 14 字体仅 WinAnsi；CJK 标题只能
   栅格化（`pdf_text_rasterized_cjk`），不可选取不可检索；仓内零字体文件。
3. **出图范围无契约**：A4/A3 只对视口做 1.414 中心裁切，遮罩（用户所见）与
   出图件范围无任何保证，边缘内容静默丢失。
4. **双链漂移**：canvas 与 SVG 两条导出链各自决策（图例只取 `legends[0]`、
   署名缺席、标题回退链不一致、比例尺口径分叉）；后端报告附图是第三套版面。
   另：`POST /map/export/vector-pdf` 为零前端调用方的死接口（#1213）。

## Decision

### 1. 版面描述中间层（Publication Layout IR）—— 单一决策记录

`frontend/lib/export/layout-description.ts`：纯同步装配器，把分散在消费端的
版面决策收拢为一份确定性、JSON 可序列化的记录（page/mapFrame/texts/scaleBar/
extent/degradations + canvas 专用 chromeModel）。消费契约：

- canvas 链（composeLayout）：消费 chromeModel（既有绘制面与金样测试不变）；
- SVG 链（vector-svg-export）：消费全量字段 —— 漂移逐项封口；
- 后端（P6）：`app/lib/cartography/layout_description.py` 忠实镜像，
  golden corpus 双端对拍（`tests/cartography/golden_corpus/layout_description/`，
  浮点 1e-9；`chromeModel` 为 canvas 运行时面不入 JSON 契约）。

**与 07 线协调**：07 未合入（分支无独有提交）→ 本线定义该中间层；07 合入后
可整体迁往其版面模块，本文件即交接面。

### 2. 高 DPI 渲染策略（lib/export/highdpi.ts 单源）

- 真重渲染（setPixelRatio + 有界 idle）保持；**超时从失败改为 §0.5 降级**：
  恢复原比率 → 3s 短界等重绘 → 以当前画布导出 + `highdpi_rerender_timeout_degraded`
  诊断；降级重绘再超时才类型化失败（无画面不伪造产物）。
- 栅格细节增益由**矢量引擎孪生 oversample**（`computeOversampleBoost ≤2` 级，
  已有 parity 测试）承担；canvas 路径发 `raster_tile_detail_limited_highdpi`
  info 披露。否决离屏重建地图实例方案：style/图标闭包保真风险高且 06 线
  runtime 边界受限。
- 引擎级单飞锁：并发第二调用类型化失败（资源纪律：高 DPI/PDF 重任务禁并发）。

### 3. PDF 字形与文本层（lib/export/pdf-font.ts）

- 仓内 vendored **Noto Sans SC 子集**（OFL；wght=400 静态实例 + GB2312 全表
  6763 汉字 + ASCII + 常用标点；2.3MB glyf TrueType；frontend/public/fonts 与
  app/lib/cartography/fonts 同一构建产物双面部署）。
- 字体加载（TTF 魔数校验）+ jsPDF VFS 注册成功 → 标题/副标题/页标题/页脚
  走 **doc.text 真文本层**（可选取/可检索/可复制）；`pdf_text_rasterized_cjk`
  降为字体不可用时的**最后兜底**。嵌入披露 `pdf_cjk_font_embedded`。
- 后端 matplotlib 同字体优先注册（font_manager.addfont）。

### 4. 所见即所得（lib/export/extent.ts + exporter 接线）

- `exportBoundsForFrame`：遮罩范围 → 图框纵横比导出范围（Mercator 归一量纲、
  中心不动点扩张，**⊇ 遮罩零内容裁切**；度/归一化混用单位 bug 由测试捕获修复）。
- 纸张档出图前 fit 相机（duration 0 + 有界 idle）→ 视口中心裁切恰好等于
  导出范围；导出后相机恢复。screen 档不干预。
- 数据范围超界 → `extent_overflow_data` 提示（超界留白，不静默裁切）；
  fit 超时回退旧裁切语义 + `extent_fit_timeout_degraded` 披露。

### 5. 出版档（color_mode = srgb | cmyk）

- cmyk（print 档）：PDF 页面外扩 3mm 出血 + trim 四角裁切线；SVG 裁切标记；
  栅格件色彩转换仅近似（`cmyk_approximate_raster` 披露 —— 真分色需出版引擎，
  禁止假装精确）。srgb 档 bleed 恒 0。

## Consequences

- 正面：PDF 中文文本层可检索（pypdf 硬门禁）；300 DPI 细线对比度/有效线宽
  全面优于放大插值基线（探针取证入档）；遮罩=出图范围有解析与接线双重测试；
  双链/前后端版面决策同源（golden 对拍 + 漂移杀死测试）；高 DPI 超时不再
  丢失整次导出。
- 代价：仓内 +2.3MB×2 字体资产（OFL 允许再分发）；jsPDF 嵌字体 PDF ~+2.3MB；
  版面决策变更须双端同步（golden corpus 红灯护栏）。
- 限制：容器级 WeasyPrint/pango 与系统 CJK 字体安装属部署面（Dockerfile 本线
  禁改），以仓内字体 + file URL 白名单为本地/自托管路径，登记 follow-up；
  canvas 栅格底图高分细节仍受 MapLibre 取图语义限制（已披露）。

## 门禁取证（§5 全项）

见 PR 描述「本地门禁证据」段与 `docs/dev/ac-08-samples/dpi-baseline/`、
`docs/dev/ac-08-samples/sample-export-cjk.pdf`。
