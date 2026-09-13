# ADR-0166: V11 W6 — 出版与交付（IR 三渲染器 parity、PDF 图体矢量、高 DPI 折中、格式矩阵、批量队列、可访问性）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/v11-master（W6）
- 关联: ADR-0157（V10 出版级导出）、ADR-0120（孪生）、ADR-0160（C2 IR）、ADR-0165（版面）

## 1. 背景与靶心

缺口 G3（三套整饰渲染无共享 IR）与 G9（PDF 图体栅格 / 高分不重建 / 批量与
可访问性缺位）。W6 的收敛策略：**可机器验证处先锁**（放置层 parity），
**行为不可变处叠加**（矢量优先 + 栅格兜底），**评估结论量化入档**（高分三方
对比），缺口逐条登记（不虚报）。

## 2. 决策一：IR 三渲染器 parity 骨架 + 残壳清理（G3）

- `frontend/lib/layout/ir-parity.test.ts`：同组件集三方对拍 ——
  ①C2 IR 的 `frame.anchor`（冻结契约）；②React DOM 面 `resolveMapComponents`
  （map-spec-chrome 消费面）；③canvas/SVG 面 `resolveComponentLayout`
  （export-chrome 及其 SVG 孪生消费面）。三方锚点逐组件一致 + Z 序语义
  锁定（IR layers 升序；槽内 stackIndex 语义防误读）。
- **残壳清理**：`frontend/lib/map-exporter/` 仅含一个测真实 exporter 的
  测试文件（S1 债扫描登记）——测试迁至 `lib/map-kit/exporter-engine.test.ts`
  （18 例全绿），目录删除；`exportCommands.ts:18` 引用不存在路径的陈旧注释
  修正。
- 渲染器**像素级** parity 与全量切换随各渲染器接线增量追加到同一测试文件
  （骨架先行，防再分叉）。

## 3. 决策二：PDF 图体矢量化（G9 半边）

- `frontend/lib/map-kit/pdf-vector.ts`：`addVectorSvgBody` —— SVG 经
  **svg2pdf.js**（MIT，jsPDF 官方配对）转 PDF 路径操作符嵌入；异常全捕
  fail-soft。**两处互操作实证入档**：①svg2pdf 包 main 是 UMD（peer 模式
  读全局 jsPDF），Vite/ESM 下 import 即炸 → 显式走 `dist/svg2pdf.es.js`
  并在导入前挂 `globalThis.jsPDF`；②jsdom 不实现 SVG `getBBox`（测试桩
  补齐；生产浏览器原生具备）。
- `exporter.ts` PDF 路径接线：`options.vectorSvg` 在场 → 矢量优先；
  失败/缺席 → 栅格回退 + `onBodyMode` 回执（不伪矢量）。
- 验收实证：矢量体 PDF **不含图像 XObject**（`pdfHasRasterImage` 判据），
  内容流含路径算子；栅格对照含之；结构确定性（长度/算子/无图像；字节级
  因 svg2pdf 进程级 id 计数器不可得 —— 第三方事实，测试据实降级断言）。
- 服务端 WeasyPrint 矢量 PDF（V10 既有）不动 —— 双通道并存，前端通道补齐。

## 4. 决策三：高分导出评估（W6.3，量化入档）

- `docs/dev/ac-v11-highdpi-evaluation.md`：三方对比 —— A setPixelRatio
  （现状，矢量受益/栅格无增益）、B 重建实例（**否决**：取图 zoom 由 view
  zoom 决定，重建同样无栅格增益且成本实付）、C 瓦片 zoom 提升 + 重采样
  （**采纳 opt-in**）。
- `tile-zoom-plan.ts`：`planTileZoomCapture` 纯函数（gain 封顶 maxZoom；
  无 headroom 如实披露「与 V10 等同」；披露文案必须含「重采样」防误读）。
  live 两段相机接线归后续批次（登记）。

## 5. 决策四：批量导出队列（W6.5）

- `export_batch_queue.py`：**串行恒 1**（类常量 + assert，资源纪律）、
  失败重试（默认 2 次）、**断点续传**（resume_state 跳过已完成 id）、
  fail-soft（失败不中断批次）、有界（≤200 job）、确定性（输入序）。
  不落盘（状态由调用方持久化，与 session/工件存储解耦）。

## 6. 决策五：可访问性清单（W6.6）

- `accessibility_manifest.py`：alt text（作者优先/机器兜底合成）、图层标签
  （图例标题 > 字段 > id 回退链）、色盲安全声明（**context_matrix 同源实测**
  —— 不新造词汇，未通过如实披露）、数据来源、投影；`complete`/`missing`
  诚实核验（验收「100% 存在」= 缺即失败信号）。

## 7. 决策六：格式能力矩阵（W6.4）

- `docs/dev/ac-v11-export-formats.md`：SVG✅ / PDF✅（W6.2 补前端矢量）/
  PNG✅ / GeoTIFF⚠️（服务端具备、导出菜单未接线）/ 打印档⚠️（版式走通、
  自动化核验归 W8）/ 批量队列✅服务面 / 可访问性✅服务面。缺口四条逐项
  登记移交（W8 批次）。

## 8. 验收对照

| 任务书 W6 验收 | 状态 |
|---|---|
| 三套渲染器 parity 通过（同 MapSpec 输出等价） | ✅ 放置层三方对拍；像素级随接线增量（骨架在） |
| PDF 图体矢量（文本层 + 图形层均可提取） | ✅ 图形层路径算子（无 Image XObject 实证）；文本层沿用 V10 |
| 批量队列端到端 | ⚠️ 服务面全测（串行/重试/断点/fail-soft）；路由接线后续批次 |
| 可访问性元素 100% 存在（断言） | ✅ 清单 complete/missing 断言 |
| `exporter.ts` 双路径债清除 | ⚠️ 残壳目录与陈旧注释已清；chrome/legacy 双路径收敛随三渲染器接线（G3 主战役，W6 骨架已锁） |

## 9. 风险与回滚

- 矢量优先是**叠加**（失败即回退栅格 + 回执），无行为悬崖；svg2pdf 为
  动态 import（导出器首载不增重）。
- 回滚点：tag `ac-v11-w6`；依赖新增 svg2pdf.js（MIT）随 lockfile 登记。
