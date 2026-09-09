# Cartography V5 — Architecture (Phase B)

审计证据见 `00-baseline.md` 与两份 subagent 审计（后端/前端）。本文件冻结架构决策。

## 1. Current-state dependency graph（渲染/导出链）

```
后端 desired-state：intent → lifecycle_engine.apply_mutation（锁+COW+CAS）
  → review_and_repair_cartography（semantic_checks + quality_loop AUTO_SAFE 修复，可改写 candidate！）
  → blocking diff 校验 → store.save_mapspec（Redis+磁盘双写）
后端导出孪生：mapspec_to_svg.compile_mapspec_to_svg ← report_service（WeasyPrint 报告内嵌）
  ← 唯一调用点；不查 layer visibility；不接 label_engine；不消费 thresholds
PDF 壳：api/routes/map.py /export/pdf ← 前端 canvas PNG → pdf_renderer（matplotlib A4 栅格壳，dpi=150）
前端 live：committed MapSpec(session-cursor) + HUD → composeLiveMapSpec（合并 pending*）
  → MapSpecRuntime diff/patch → MapLibre；chrome = DOM 覆盖层（MapSpecChrome registry）
前端导出：export_map 命令 → exporter.runExport → map.getCanvas()
  → buildExportChrome({committed spec only}) → composeLayout 画 chrome → PNG / SVG(PNG包装) / PDF(jsPDF raster+CJK乱码)
孤岛真矢量资产：mapspec-to-svg.ts（TS 孪生，parity 锁定）+ svg-marginalia.ts —— 零生产调用方
```

## 2. Root causes（对应 Epic known gaps）

| Epic gap | 根因 |
|---|---|
| legend visible=False 声明成功但未生效 | `_check_map_legend_completeness` 将显式关闭判为 error/auto_safe，`quality_loop` 在 commit 前翻回 True；SetLayoutIntent 无 user-wins 守卫；且整值替换丢 position |
| long label 静默溢出 | label_engine 无截断/换行/overflow 诊断且生产零接线；SVG 导出文本原样嵌入；前端靠 MapLibre 碰撞无披露 |
| SVG 导出伪矢量 | 前端 exporter 走 buildSvgWrapper（PNG 包装）；真矢量编译器是孤儿代码 |
| live/PNG/PDF/SVG 真相 | buildExportChrome 只读 committed spec 不合并 pending*；SVG 后端孪生不查 visibility；PDF 三套版式 |
| small_multiple/cartogram/before_after/atlas | 多画幅运行时整体缺失（planned 诚实登记）；swipe 导出静默丢第二视图 |
| 降级披露 | 后端无 render 诊断契约；前端 4 码词表 2 码死词；无持久化锚点 |
| thresholds 契约装饰化 | maxFeatures/timeoutMs 声明但零消费 |
| apply_presentation_batch | `popitem(next(iter(...)))` TypeError（dict.popitem 不接受参数） |

## 3. Target-state component graph

```
                    ┌─ Render Diagnostics Contract V5（唯一词表，后端权威）
                    │   app/lib/cartography/render_diagnostics.py
                    │   → export_component_catalog 导出 renderDiagnostics 段
                    │   → 前端 registry-parity 测试锁定 ExportDegradation ⊆ 词表
                    │
MapSpec ──┬─ live：composeLiveMapSpec（现网不动）
          ├─ backend twin SVG：+visibility 过滤 +label 截断 +thresholds 执行 → diagnostics[]
          ├─ frontend vector SVG（新接线）：mapspec-to-svg.ts + svg-marginalia.ts → 真矢量 .svg
          ├─ PNG：composeLayout（现网不动，+诊断发射）
          ├─ PDF：单 title 源 + CJK 文本栅格化回退 + 诚实披露
          ├─ swipe 导出：显式双画布组合（clip 组合）或诚实降级码
          └─ atlas / small_multiple：export_product 多帧运行时（确定性分页）
                 帧定义 = MapSpec layout.components 新类型？✗ —— 走 ExportRequest 参数
                 + composite/frame spec（避免改 MapSpec 契约核心）
```

### 3.1 数据/状态所有权（不变式）
- MapSpec 仍是唯一 desired-state 真相；本 Epic **不新增第二 spec/store/registry**。
- render_diagnostics 是**导出产物证据**（ephemeral evidence），不是状态；持久化锚点 = export 上传 sidecar（`{filename}.diagnostics.json`）。
- 降级词表唯一权威在后端 `render_diagnostics.py`；前端经 generated catalog 消费（与 component catalog 同通道）。

### 3.2 新 service 为何不能复用现有实现
- `render_diagnostics.py`：现有 `semantic_checks` 是 desired-state 语义检查（提交前），前端 `ExportChromeModel.degradations` 是前端私有 4 码词表；二者语义层不同（spec 质量 vs 渲染产物披露）。新建小词表模块 + catalog 导出，复用 `export_component_catalog.py` 通道，不另造通道。
- label 截断：`label_engine.solve_labels` 是布局求解器（碰撞/位点），截断/换行是文本度量问题 —— 在 label_engine 内新增纯函数 `fit_label_text`，不另建模块。
- atlas/small_multiple：`CompositeMapSpecBuilder` 是 5-slot 组装器（单画幅），不承载多帧；多帧组合属渲染产物层，放前端 exporter 引擎内新增 `frame-composer.ts`，复用 waitForMapIdle/canvas 工具。

### 3.3 API/typed contract 变化
- `ExportRequest` 增：`frames?: ExportFrame[]`（atlas/small_multiple 帧序列）、`frameLayout?: 'grid'|'pages'`。
- `POST /api/v1/export`：payload 可选 `render_diagnostics: [{code,severity,message,detail}]`，服务端校验词表 ∈ 权威词表，存 sidecar。
- 后端孪生 `compile_mapspec_to_svg(...)` 返回值扩展为 `SvgCompilation`（svg + diagnostics + feature_count），旧 str 返回保留兼容 wrapper。
- `webgis_layout_set`：legend 字段级 merge（partial intent 不再丢 position）；controls 类型修正 Dict→List。
- MapSpec 契约核心（version/layers/layout schema）不变；不引入后端全量 pydantic MapSpec（超大改动，冲突面宽，列为 follow-up）。

### 3.4 Backward compatibility
- `compile_mapspec_to_svg` 旧签名调用点（report_service）经 wrapper 兼容。
- buildSvgWrapper 保留但导出菜单 'svg' 语义升级为真矢量；`svg_wrap` 旧产物仍可读。
- PDF：ASCII-only 文本仍走矢量 doc.text；含 CJK 时栅格化 + `pdf_text_rasterized_cjk` 诊断。不引入字体文件（仓库无 CJK 字体资产，下载超资源约束）。
- legacy 固定槽 composeLayout 路径不动。

### 3.5 Cancellation/timeout/retry
- 后端孪生编译消费 `thresholds.maxFeatures`（超出 → 截断 + `features_truncated` 诊断）；report 链 SVG 编译包 `asyncio.wait_for(timeoutMs)`。
- 前端多帧导出复用 EXPORT_RENDER_TIMEOUT_MS 每帧预算 + 总页数上限（atlas ≤ 50 页）+ 失败页记 `atlas_page_skipped` 继续而非中断。

### 3.6 Observability/evidence
- 诊断三通道：工具结果（现有 cartography_findings 通道不动）、导出后系统消息（现有，升级词表）、export sidecar（新，持久化锚点）。

### 3.7 Security
- SVG 真矢量路径沿用 python 孪生的属性转义 + 前端 compiler 转义；新接线处复用相同 escape；上传侧 sanitize 不变；sidecar 路径由服务端文件名生成（不可注入）。

### 3.8 Performance budgets（结构化，不依赖整机 wall-clock 绝对值）
- 后端孪生：20k 点 synthetic 编译 ≤ 2.5s 且 feature cap 生效（differential: cap=2000 时元素数 ≤ cap 相关上界）。
- 前端 atlas：50 页 × 逐帧 idle 等待复用既有 30s/帧预算；frame-composer 不复制大 GeoJSON（按 ref 复用 source）。

### 3.9 Test oracle
- 语义 parity corpus：`describeRenderScene`（前端纯函数：components/legend entries/label 截断标记/visibility 投影）对 live chrome 模型 vs export chrome 模型 vs 孪生 SVG 结构做 golden 断言。
- 后端孪生 golden semantic corpus（SVG 结构断言，不做像素断言）。
- 两个 P0 回归测试：legend visibility user-wins；200+ 字符 label 截断+诊断。

### 3.10 Rollout/fallback
- 全部变化带诊断披露；真矢量 SVG 失败时回退 PNG 包装 + `vector_svg_fallback_raster` 诊断。
- 不动 DB/migrations；无 Alembic revision。

### 3.11 与并行 Epic 的边界
- Workbench V5（副视图/协作）：不动 comparison live UX，只加导出组合。
- Harness/Science：不动 intent 词表与算法。
- 共享文件最小化：CHANGELOG 一条、无 workflow 改动、generated catalog 只增 renderDiagnostics 段 + registry 再生成。

## 4. Waves（12 个）

| Wave | 内容 | 拥有者 |
|---|---|---|
| W1 | render_diagnostics 词表 + catalog 导出 + 前端类型对齐测试 | 主 |
| W2 | layout intent 正确性：user-wins 守卫 + 字段级 merge + popitem 修复 + controls 类型 | 主 |
| W3 | label engine：fit_label_text（截断/换行）+ overflow 诊断 + 确定性 | A |
| W4 | 后端孪生 SVG：visibility + label 接线 + thresholds 执行 + SvgCompilation + golden corpus | A |
| W5 | 前端真矢量 SVG 导出接线（mapspec-to-svg + svg-marginalia 复活）| B |
| W6 | PDF 纠正：单 title 源 + CJK 栅格化回退 + dpi 透传 + 诚实文案 | B |
| W7 | export 真相对齐：pending 合并 + legend 内容 parity + 死词表发射器 + nodata 图例 + 诊断 sidecar | B（前端）+ A（sidecar 端点） |
| W8 | swipe 导出组合/诚实降级 | B |
| W9 | atlas / small_multiple 多帧导出运行时 + cartogram 诚实降级 | B |
| W10 | 语义 parity corpus（describeRenderScene）+ visual regression golden | 主 |
| W11 | 导出性能预算 benchmark（孪生 20k 特征 + cap 生效）+ report 链超时 | A |
| W12 | ADR-0118 + parity matrix/matrix 文档再生成 + CHANGELOG + 已知限制收敛 | 主 |

Review R1（架构/正确性）、R2（性能/安全/UX）由主 + 两 subagent 交叉执行。
