# Review Findings（R1 + R2 归档）

## Round 1（Subagent-A，架构/正确性）—— 全部已修复（commit 1a94c53c）

- BLOCKER-1 `_svg_to_page_html` 误 html.escape(svg)：矢量 PDF 实为源码文本墙。
  修复=原样嵌入；真闸=提取文本不含 `mapspec-vector-layers`/`&lt;` + 单测未转义 `<svg`。
- MAJOR-2 多帧 @page 压成 auto → named pages（每帧独立尺寸）+ mediabox 互异断言。
- MAJOR-3 report write_pdf 未接锁 → render_pdf_exclusive 共享互斥（忙时 report 阻塞等待、
  导出端点 429，语义区分）。
- MAJOR-4 extend_frame 死码 → publication 逐帧真实消费（子配额+溢出元披露接线）。
- MAJOR-5 ref 源不水合 → 空白出版页 → 未物化 geojson/vector 源 typed 400
  （mapspec_ref_sources_unhydrated）；raster 披露 detail 指名源键。
- MINOR-6 legend 脏输入四类跨语言分叉 → 两侧对齐 + 共享 fixture（dirty_input_alignment）。
- MINOR-7 TS fitLabel 截断无诊断 → onDiagnostic("label_truncated")。
- MINOR-8 maxLabels 死契约 → 双端消费（≤400 封顶）。
- MINOR-9/10/11 title 元数据落地 / sources 非 dict 安全 / 消费方与注释漂移修正。
- NIT：or 掩码断言收敛、_frame_geometry 死参等。

## Round 2（Subagent-B，性能/安全/并发）—— CRITICAL+MAJOR 全部已修复（commit 1ccc4edb）

- CRITICAL-1 graticule 浮点累积永续循环（1e16+0.5 ties-to-even；publication+report
  双链可达、线程永续泄漏）→ 定长索引循环 + 条数上界 + _nice_step 安全步长。
- MAJOR-2 zoom ≤-1075 除零 500 → zoom 夹取 [-2,22] + ZeroDivision/Overflow 捕获。
- MAJOR-3 sessionId 无属主校验（IDOR 面）+ MAJOR-8 工作线程触碰非线程安全 LRU →
  **移除 sessionId 水合面**（调用方内联 ref 源；未内联走既有 typed 400）。
- MAJOR-4 disclosures 无界 → 每类封顶 200 + disclosures_truncated 标志。
- MAJOR-5 内存放大 → maxFeatures 服务端封顶 50000、timeoutMs 封顶 30s、
  累积 SVG 96MB 预算（超限停止加帧 + atlas_page_limit_truncated 披露）。
- MAJOR-6 事件循环内联 dumps/落盘 → 线程化（#592 不变式）。
- MAJOR-7 与 M5 一并落地（服务端 timeoutMs 封顶）；专用有界 executor 登记 follow-up。
- MINOR-11 font_size<=0 过滤 + cell clamp（双孪生）；MINOR-12 spec-frames span 有限性复检。
- MINOR-10（report 忙时无超时阻塞抢锁）、MINOR-9（双重 deepcopy）、NIT-14（字体探测预热）
  → 登记 follow-up（行为安全，仅打磨）。

## 登记为 Follow-up（非阻塞）

1. 矢量 PDF dataUrl 形式的 raster 层嵌入（当前诚实省略+披露）。
2. WeasyPrint 多进程部署的外部协调（当前单进程互斥）。
3. publication 专用有界 executor（当前共享默认 executor + 锁）。
4. export sidecar / report 元数据携带 schema disclosures（当前 vector-pdf 响应消费）。
5. curved textPath 标签、terrain/hillshade 导出、cartogram（诚实 planned，词表披露不变）。
