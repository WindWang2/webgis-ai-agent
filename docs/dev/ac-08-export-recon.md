# ac-08 出版级导出 · P0 勘察报告（export recon）

- 线：`adaptive-cartography/08-publish-export`（ADR-0157）
- 基线：origin/master @ `1fd4b035`；worktree：`webgis-wt-ac-08`
- 日期：2026-09-13
- 方法：S1 Explore 只读勘察 + 主 agent 逐点亲验（exporter.ts / exportCommands.ts / oversample.ts / publication_export.py / report_service.py / ADR-0126）

---

## 0. 复核纪要（防重复复核，§0.2）

### 0.1 检索取证

1. **PR 检索**（`gh pr list --state all --search "export OR DPI OR PDF OR SVG OR 导出 OR 出版 OR print"`，200 条）：
   - 制图主线：#1151（V4 导出 parity）→ #1170（V5 render contract/export parity）→ #1181（V6 Typed MapSpec + Publication Engine，ADR-0126）→ #1195（V7 composition）。
   - 早期导出：#79（标准地图导出含指北针/比例尺/图例/PDF，2026-04）；#91（security audit + professional map export）；#506（GeoJSON chunked export）。
   - 结论：**没有任何已合并/进行中 PR 做「真高分重渲染 + 矢量优先 PDF + CJK 文本层 + 所见即所得 + 前后端版面对拍」的整线交付**。V6 的 publication engine 是本线最直接前作（后端矢量 PDF 已存在但前端未接线）。
2. **Issue 检索**（300 条）：
   - `#527` 导出 DPI 路径 `map.once('idle')` 无超时（已修复：`waitForMapIdle` + `MapIdleTimeoutError`，exporter.ts:1262-1284，亲验 ✓）。
   - `#802` 导出整饰 DPR/DPI-blind（已修复：`pixelsPerLogicalPx`/`canvasDpr` 真实设备像素比，exporter.test.ts 有 pin 测试）。
   - `#805` `export_layout` 组件死配置（已修复：runExport 读取 spec layout.export_layout，exporter.ts:1533-1544）。
   - `#614` export_map 参数契约（已修复：dark_mode 优先级 exporter.ts:1572-1575、A3 白名单）。
   - `#1213`（P2，audit3）：`POST /api/v1/map/export/vector-pdf` **无任何前端调用方，整链死接口** —— 本线 P2/P6 的核心协调点：前端矢量优先接线即是对该 issue 的收口。
   - Wayfinder 老票 #257/#258/#260/#261/#262/#263/#264（HD 矢量导出与 Print Layout）：分别由 mapspec-to-svg 双孪生（#844 后 V5 重引入）与 WeasyPrint 报告注入（#263）承接。
3. **分支检查**：`git branch -a | grep -iE "export|pdf|svg|print|publish"` → 仅本线新分支 `adaptive-cartography/08-publish-export`，无平行实现。
4. **代码指纹**（`runExport|prepareExportCanvas|exportToPDF|dpi/96|pdf_text_rasterized_cjk|toDataURL`）：前端命中 exporter.ts / export-chrome.ts / frame-composer.ts / raster-canvas.ts 及其测试；后端命中 `component_renderers.py` / `render_diagnostics.py` / `mapspec_to_svg.py`。与 S1 勘察一致，无未登记的第三条导出链。

### 0.2 编号与边界（§0.3）

- **ADR-0157 可用**：`docs/adr/` 最高编号 0147，0148–0157 全部未占用（亲验 `ls docs/adr | grep ^015` 为空）。
- **Alembic**：本线无 schema 变更，不新建迁移。
- **环境偏差（记录）**：任务书 §0.1 的 `pip install -e .` 在本仓不可行 —— pyproject.toml 无 `[build-system]`/`[tool.setuptools]`，setuptools flat-layout 自动发现撞多顶层目录（app/perf/agent/…）拒建；兄弟 worktree（ac-01…ac-09）同样未装 editable 包。仓库实际依赖 `pytest.ini` 的 `pythonpath = .`。故本线改用 `pip install -r requirements.txt -r requirements-dev.txt`，语义等价。

---

## 1. 现状链路全图（亲验后修正版）

### 1.1 Canvas 链（PNG / 栅格 PDF）

```
UI 命令 export_map（components/command/command-palette-root.tsx）
 └─ lib/map-commands/exportCommands.ts:30
     map.once('render') + triggerRepaint；队列级 30s 看门狗 EXPORT_RENDER_TIMEOUT_MS(:24)
     动态 import MapExporterEngine（重模块不进首屏 bundle）
 └─ lib/map-kit/exporter.ts:1505 runExport(deps, req)
     1) fmt==='svg' → hydrateMvtLayers(:1511-1516)
     2) 参数优先级 request > spec layout.export_layout > 默认
        paperSize screen/A4/A3、orientation、dpi 默认 96（:1530-1562）
     3) spec 级 frames → spec-frames.ts（:1545-1550）
     4) frames? → runFrameExport(:1376)（frame-composer，MAX_FRAMES=50）
     5) 高 DPI 真重渲染：map.setPixelRatio(dpi/96)（:1577-1586）
        → waitForMapIdle(30s 有界，MapIdleTimeoutError :1262-1284)
     6) baseCanvas = map.getCanvas()；canvasDpr = width/clientWidth（#802 :1593-1598）
     7) prepareExportCanvas(baseCanvas, {dpi: 96})（:1599-1603）
        —— 注意：实际调用恒传 dpi=96，插值放大分支（:150-162）在生产路径已死
     8) swipe 对比合成（:1610-1631）
     9) buildExportChrome ← spec layout.components（:1745-1802）
    10) composeLayout（Canvas2D :182-504，scalePx = v·dpi/96，DPR 感知）
    11) 格式分支：
        png → toDataURL → uploadExport('export.png')（:1950-1961）
        pdf → exportToPDF(:897-1034)：jsPDF unit:mm a4/a3；
              addImage(toDataURL('image/png')) 恒栅格（:938-953）；
              仅 ASCII(WinAnsi) 标题走 doc.text 真文本（:961-973）；
              CJK → 标题栅格化 + pdf_text_rasterized_cjk（:1911-1921、atlas :990-1023）
        svg → buildVectorSvgExport（真矢量）或位图包装（:1859-1909）
    12) uploadExport → POST /api/v1/export + render_diagnostics sidecar
    13) finally: map.setPixelRatio(origPixelRatio)（:1985-1989）
```

超时/失败语义（亲验）：idle 超时 → 类型化错误 → **导出失败**（如实文案 + 恢复 pixelRatio），**不是降级导出**。与 §0.5「超时降级为当前画布导出 + degraded 标记」存在差距 → P1 增量 1。

并发语义：命令队列保证 `export_map` 串行（queue head 至 settle，exportCommands.ts 注释 F5）；但 `MapExporterEngine.export` 直接调用方（story/narrative-export.ts 等）无单飞保护 → P1 增量 2：引擎级 single-flight。

栅格底图高分细节：`setPixelRatio` 只提升矢量/符号渲染分辨率；栅格瓦片源仍按当前 zoom 取图，高分下被拉伸 → 模糊。`oversample.ts` 已有 `computeOversampleBoost = clamp(log2(dpi/96), 0, 2)` 与显式瓦片网格（`buildTileGrid`），但 canvas 导出路径未消费 → P1 增量 3：高分导出时对栅格底图做 oversampled zoom 瓦片交换（RasterTileSource.setTiles 显式 URL 列表），有界瓦片数 + 超时降级。

### 1.2 SVG 链（真矢量）

```
runExport svg 分支（:1859-1909）
 └ vector-svg-export.ts:133 buildVectorSvgExport
    A4/A3 = 视口长边 ÷1.414（:64-79）；compileMapSpecToSvg(targetDpi:72)（:139-144）
    标签 >60 字符截断（:94-111）
    合成：白底 + 编译数据层 + 帧框 + 真 <text> 标题 + 指北针 + 比例尺
          （仅当 metersPerPixel 给定）+ 单图例（legends[0]）（:156-204）
    诊断：basemap_omitted_vector_svg 恒发；异常 → fallbackRaster + vector_svg_fallback_raster
 └ mapspec-compiler/mapspec-to-svg.ts:229 双孪生编译器（与 Python 字节 parity）
    raster→外部 <image href>；circle/line/fill/extrusion(压平+标记)/symbol→矢量；
    heatmap→近似半透明圆（非核密度）；标签确定性碰撞（budget≤400）
```

### 1.3 后端链

```
POST /api/v1/map/export/vector-pdf（map.py:300）【死接口，#1213】
 └ publication_export.render_publication_pdf（:333）
    weasyprint 缺席→503；schema 校验；ref 源未水合→400；
    raster/wms/wmts/pmtiles→诊断 raster_layer_unavailable_vector_pdf（诚实省略）；
    CJK 字体探针失败→pdf_font_fallback（:94-142）；
    逐帧 compile_mapspec_to_svg_detailed(target_dpi∈[72,600] 默认300, include_chrome=True,
      标签≤400, 要素≤50000, 超时≤30s)；
    HTML @page=帧尺寸 → render_pdf_exclusive（非阻塞锁，忙→429）→ WeasyPrint
POST /api/v1/map/export/pdf（map.py:389）【无前端调用方】
 └ pdf_renderer.generate_map_pdf（matplotlib，139 行，位图塞 A4 横版，
   无指北针/比例尺/图例；CJK 走 matplotlib fontManager 关键字扫描）
报告链 report_service.py
 └ _compile_vector_svg_for_report(:507) → compile_mapspec_to_svg_detailed(300, include_chrome=True)
    超时 → data-export-degraded 注入（:528-535）；_html_to_pdf(:629-647)
    → render_pdf_exclusive（忙时阻塞排队 —— 与端点 429 语义有意不对称）
```

### 1.4 多帧合成（frame-composer.ts）

`MAX_FRAMES=50`（超限 atlas_page_limit_truncated）· 每帧 setFilter+fitBounds+有界 idle+**快照拷贝**（getCanvas 单例别名 BLOCKER 回归，:164-175）· PDF=多页图集（首页封面）· PNG/SVG=网格拼图（`GRID_MAX_DIM_PX=16384` 诚实抛出）· cartogram 不支持如实标记。

---

## 2. 任务书 §1 痛点 vs 现状（差异修正表）

| # | 任务书痛点 | origin/master 现状（亲验） | 本线真实增量 |
|---|---|---|---|
| 1 | DPI 假高分（drawImage 插值） | **部分过时**：setPixelRatio 真重渲染已在（#527 修复）；但 (a) 超时=失败非降级 (b) 无引擎级单飞 (c) 栅格底图高分下无细节增益（无 oversample 接线） | P1：超时降级+degraded 标记、单飞锁、栅格底图 oversample 交换 |
| 2 | PDF 实为栅格、CJK 不可选 | **仍然成立**：地图体恒 addImage(PNG)；仅 ASCII 标题是真文本；CJK→pdf_text_rasterized_cjk | P3：CJK 子集字体嵌入 jsPDF + 真实文本层；栅格化降为最后兜底 |
| 3 | A4/A3 只裁画布、范围≠遮罩 | **成立**：视口中心裁切 1.414；jsPDF 版框 (pageW-20)×(pageH-40)≈1.63 与裁切比不一致→letterbox（#803 有意） | P4：遮罩范围=导出范围 ≤1px + 范围预览/超界提示 |
| 4 | 双渲染链漂移 | **成立**（详 §3 差异表） | P2：统一「版面描述中间层」 |
| 5 | 无出版档 | **成立**：无 CMYK/出血/裁切标记 | P5：print 档出血 3mm+裁切标记；CMYK 显式参数化 |
| 6 | 后端 139 行弱渲染、报告链另套 | **成立**：pdf_renderer 无整饰；报告链经 compile_mapspec_to_svg(include_chrome) 与前端导出仍非同源 | P6：报告附图与前端消费同一版面描述；后端 PDF 补整饰 |

---

## 3. SVG 与 Canvas 双链内容差异清单（同 MapSpec 对比）

| 维度 | Canvas 链（PNG/PDF） | SVG 链 | 证据 |
|---|---|---|---|
| 数据层 | live MapLibre WebGL 栅格化（瓦片+矢量+标签，live 符号律） | 双孪生编译：circle/line/fill/extrusion(压平)/symbol | exporter.ts:1588-1603 vs mapspec-to-svg.ts:379-486 |
| 底图/栅格 | live 瓦片含入 | 底图诚实省略（basemap_omitted_vector_svg 恒发）；栅格数据层发外部 URL 引用（不自包含） | vector-svg-export.ts:146-148 vs mapspec-to-svg.ts:342-367 |
| 标签 | glyph SDF + style 碰撞 | 真 <text>；确定性碰撞仅 deterministic 模式；≤400；>60 字符截断 | mapspec-to-svg.ts:590-699 |
| 字体 | 浏览器 fillText | font-family 声明（默认 sans-serif），不嵌入，查看端依赖 | mapspec-to-svg.ts:527-528 |
| 整饰 | 全套：header 渐变/标题/比例尺/罗盘(方位角)/经纬网/多图例+色条/inset/统计图表/水印/元数据行 | 仅：帧框/标题/指北针(固定)/比例尺(条件)/单图例(legends[0], 矩形项) | exporter.ts:224-371 vs vector-svg-export.ts:156-204 |
| 图例模型 | deriveLegendModel 多实例 + colorbars | 仅 legends[0]，双变量→null | vector-svg-export.ts:117-127 |
| 出图范围 | 视口中心裁切 1.414 | 数据自动 bbox（padding 0），**非视口裁切** —— 范围语义分叉 | exporter.ts:135-148 vs vector-svg-export.ts:64-79 |
| 对比(swipe) | 副图合成+分界线 | 如实跳过（comparison_second_view_not_exported） | exporter.ts:1610-1631 |
| 多帧 | 图集页/网格 PNG | 无（网格 SVG=位图包装 + vector_svg_fallback_raster） | exporter.ts:1473-1489 |
| heatmap/3D | live 真渲染 | heatmap≈半透明圆（近似）；extrusion 压平+degraded 标记 | mapspec-to-svg.ts:465-486, :450 |

后端 publication 链是**第三套版面实现**（`_render_chrome_groups`，学术版式；`render_scene.py` 语义 parity 金样corona），与前端两条链都非像素同源。

---

## 4. 字体资源盘点

- **仓内零字体文件**（无 .ttf/.otf/.ttc/.woff2，node_modules 外）；`deploy/` 无字体安装。
- **前端**：jspdf ^4.2.1 标准 14 字体 = WinAnsi only；无 opentype.js/fontkit/字体嵌入库。CJK 进 PDF 文本层在现状下结构性不可能 → 需要**随仓供应 OFL 授权 CJK 子集字体**（jsPDF addFileToVFS/addFont）。
- **后端**：CSS_FONT_STACK "Noto Sans CJK SC"…（publication_export.py:61-64）；_probe_cjk_font 探测系统路径/fc-list/matplotlib；**Dockerfile runner 未装任何字体与 pango 系库** → 容器内 WeasyPrint 可能不可用 + CJK 探针必假（pdf_font_fallback）。本线不改 Dockerfile（§8 边界），以仓内 vendored 字体 + WeasyPrint @font-face file URL（report 链 safe_url_fetcher 可达）缓释；容器级修复登记 follow-up。
- 策略（§0.5 契约）：子集嵌入优先 → 转曲次之 → 栅格化最后兜底（保留 pdf_text_rasterized_cjk 词表码）。

---

## 5. 测试基建现状

- vitest/jsdom：**无量测像素能力**（2D context 为录制型 no-op，toDataURL 返回固定 2×2 PNG，test/setup.ts:34-68）→ 只能断言调用序列/几何/诊断。
- pngjs（devDep）：仓内 PNG 解码器；`lib/mapspec-compiler/probes.ts` / `canvas-analysis.ts` 已有纯函数像素分析（dominantColorInWindow/analyseCanvas）。
- Playwright ^1.63 + `e2e/journeys/`：真浏览器通道（j1 导出落盘、j3 pngjs 像素断言已存在）→ 本线 DPI 像素探针复用该通道（独立 HTML + maplibre + pngjs，不起 Next 服务，符合资源纪律）。
- 后端：pypdf 在 requirements-dev（文本抽取断言）；golden corpus `tests/cartography/golden_corpus/`。

---

## 6. DPI 质量基线（实测）

方法：独立 Playwright 页 + maplibre（无网络依赖，inline GeoJSON），同一 MapSpec 渲染递减线宽（0.25/0.5/0.75/1/1.5/2/3 CSS px）行组：
- A. 真重渲染路径：setPixelRatio(dpi/96) + idle 后读 canvas（现状生产路径）
- B. 放大插值路径：DPR=1 渲染后 drawImage 放大至同尺寸（旧路径/降级面）

度量（pngjs 解码）：每名义线宽的有效对比度（线带 vs 背景亮度差）与有效线宽（强度剖面一阶矩）。「可分辨」= 对比度 ≥ 阈值。

结果（实测于本机 2026-09-13，Playwright chromium + SwiftShader；完整数据 `docs/dev/ac-08-samples/dpi-baseline/dpi-baseline.md`）：

| 名义线宽 (CSS px) | 96DPI 基准对比度 | 300DPI 放大插值对比度 / 有效线宽 | 300DPI 真重渲染对比度 / 有效线宽 |
|---|---|---|---|
| 0.25 | 0.119 | 0.127 / 1.92 px | **0.360 / 0.64 px** |
| 0.5  | 0.238 | 0.253 / 2.24 px | **0.948 / 0.32 px** |
| 0.75 | 0.358 | 0.376 / 1.92 px | **0.948 / 0.64 px** |
| 1.0  | 0.473 | 0.507 / 2.24 px | **0.948 / 0.96 px** |
| 1.5  | 0.712 | 0.753 / 1.92 px | **0.948 / 1.28 px** |
| 2.0  | 0.950 | 0.950 / 2.24 px | 0.948 / 2.24 px（持平） |
| 3.0  | 0.950 | 0.950 / 3.20 px | 0.948 / 3.20 px（持平） |

结论：**≤1.5 CSS px 的细线，放大插值全面劣化**（对比度腰斩、有效线宽膨胀 ~1.5–4.5×：0.5px 线被糊成 2.24px 灰带）；真重渲染路径有效线宽贴合名义值（0.32–1.28px）且对比度满格。≥2px 粗线两路径持平 —— 证明现状「放大不增益」在细线/文字笔画等出版关键要素上成立。P7 闸以此表为基线：新链路 300 DPI 各档对比度 ≥ 放大插值路径，且细线（≤1.5px）有效线宽 ≤ 名义 × 1.5。

### 6.1 探针脚本与运行方式

- 脚本：`frontend/scripts/ac08/dpi-line-probe.mjs`（Playwright chromium，单次运行单页渲染，产物 <1MB）
- 运行：`node scripts/ac08/dpi-line-probe.mjs --dpi 96,300 --out ../../docs/dev/ac-08-samples/dpi-baseline`
- 资源：单页、小尺寸（720×480 CSS px）、无并发；符合 §0.4 限额。

---

## 7. 地雷清单（实现必读）

1. exporter.ts:1036-1043 注释已过时（称 SVG 只会位图包装）——勿信。
2. 双看门狗栈：内层 EXPORT_IDLE_TIMEOUT_MS(30s) + 队列级 EXPORT_RENDER_TIMEOUT_MS(30s) + upload 90s + 后端 120s/编译 30s，改时序需全链对齐。
3. `map.getCanvas()` 单例别名：帧路径必须快照拷贝（frame-composer BLOCKER 回归）。
4. `data-export-degraded` 标记有两生产者（extrusion 压平、报告超时注入）；新增降级沿用该约定。
5. render_pdf_exclusive 不变式：**所有 write_pdf 调用必须过锁**（忙时端点 429 / 报告阻塞排队，有意不对称，保持）。
6. #803 letterbox 契约有测试锁定；P4 改「填满版面」须同步改测试并披露。
7. 新增诊断码必须同步：export-chrome.ts:131-150 前端词表 ⊆ app/lib/cartography/render_diagnostics.py 权威词表（EMITTER_REGISTRY 契约测试）。
8. svg-marginalia 的 TS/Python 双孪生 + mapspec-to-svg 字节 parity 金样：动版面中间层必须双端同步，跑 parity 测试。
