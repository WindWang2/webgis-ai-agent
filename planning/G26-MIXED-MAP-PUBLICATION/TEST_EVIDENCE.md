# G26 TEST_EVIDENCE

环境：worktree `webgis-wt-g26-mixed-map-pub-20261112T000000Z` @ b8f2a6ecc；
Python 3.13.17（主仓 .venv 只读环境层）；weasyprint 70.0；rasterio/PIL/numpy/pypdf 可用；
宿主 `rsvg-convert`（librsvg 2.63.4）可用（Docker slim 镜像不可用 → 代码必须能力探测 + typed 降级）。

## E0 — 关键技术探针（2026-11-12，全部本地实跑）

### E0.1 WeasyPrint 70 内嵌 SVG `<image>` 不可用（PDF 面架构依据）

构造 32×32 RGBA PNG（左红右蓝）→ base64 data URI → 三个变体（href / xlink:href /
JPEG）嵌入整页 SVG → `assemble_pages_pdf`（生产同款 deny fetcher + 进程锁）：

- 结果：三变体全部 **0 图像对象、文本不提取、PDF 830B（SVG 未渲染）**。
- 结论：WeasyPrint 70 的内嵌 SVG 渲染器遇 `<image>` 丢弃整棵 SVG 子树
  （与 G15 产品页 docstring 的限制观察一致）。**PDF 嵌入必须走 HTML 面。**

### E0.2 HTML `<img data:>` 分层 + 正确 fetcher → 真实 PDF 图像对象 ✅

`<div position:relative>` + 底层 `<img src="data:image/png;base64,...">`（绝对
定位）+ 上层内联 SVG（矢量/文本）→ WeasyPrint（url_fetcher 放行 image data URI，
返回 `URLFetcherResponse(url, body=..., headers={'Content-Type': ...})`）：

- **图像对象 2 个**（64×64 /FlateDecode，一彩色 + 一 SMask=alpha 通道——透明 PNG
  的标准嵌入形态），内容流 `Do` 操作 1 次，**文本可提取**（'Overlay 混合文本'）。
- 陷阱记录（实现必守）：WeasyPrint 70 自定义 fetcher **必须返回
  `URLFetcherResponse` 实例**；返回 dict 触发 AssertionError 被吞为 warning →
  资源静默缺失。既有 `_deny_url_fetcher` 的字体分支返回 dict —— WeasyPrint 70
  下 vendored CJK 字体内嵌实际静默失效（本机有系统 CJK 字体时该分支不触发，
  CI slim 镜像才会暴露）→ P4 一并修复（回归证据见 E3）。
- WeasyPrint 70 `fetch()` 错误路径访问 `url_fetcher._fail_on_errors`（自定义
  函数无此属性）→ 原始拒绝信息被 AttributeError 掩盖。放行逻辑必须保证
  合法 data URI 不进异常路径；拒绝路径的掩盖行为记录在案（P7 测试仍断言
  外部 URL 被拒——通过 PDF 无外部对象 + fetcher 抛错前日志验证）。

### E0.3 rsvg-convert 渲染 data-URI `<image>` SVG → 精确像素 ✅

400×300 SVG 内嵌 200×150 放置矩形（preserveAspectRatio=none）+ 文本 →
`rsvg-convert -w 400 -h 300`：

- rc=0；探针像素 (100,100)=(220,0,0)、(200,100)=(0,0,220) —— geo 放置矩形
  内左右两半精确命中源像元颜色。PNG 面可用（能力探测 + 缺席 typed 拒绝）。

### E0.4 基线复跑（BASE 不带本方向改动）

- `pytest tests/cartography/test_publication_vector_pdf.py` → **9 passed**
  （含 test_raster_sources_disclosed_and_omitted —— 断言 raster 源披露+省略，
  本方向落地后该测试需演进为「嵌入在场时不省略」双分支）。

## E1 — P0 支持矩阵探针（待写）

## E2 — P1/P2 冻结 + 窗口渲染（待写）

## E3 — P3/P4 合成/图例/PDF 嵌入（待写）

## E4 — P5 入口/Pi（待写）

## E5 — P6 manifest/像素核验（待写）

## E6 — P7 安全/性能/回归（待写）
