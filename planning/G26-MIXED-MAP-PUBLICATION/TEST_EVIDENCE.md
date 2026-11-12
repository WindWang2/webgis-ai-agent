# G26 TEST_EVIDENCE

环境：worktree `webgis-wt-g26-mixed-map-pub-20261112T000000Z`（BASE `b8f2a6ecc`）；
Python 3.13.17（主仓 .venv 只读环境层）；weasyprint 70.0 / rasterio / PIL / numpy /
pypdf / rsvg-convert（librsvg 2.63.4，宿主在、Docker slim 不在 → 代码能力探测）；
frontend pnpm install（worktree 独立 node_modules）；vendor/pi dd30101cf 已构建
（Pi v1.5.1，`scripts/build-vendor-pi.sh` OK）。

复跑命令（均在 worktree 根）：

```bash
PY=/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python
$PY -m pytest tests/unit/raster_authoring/ tests/cartography/test_mixed_map_publication.py \
   tests/cartography/test_g26_legend_and_fonts.py tests/unit/raster_authoring/test_mixed_export_route_and_tool.py \
   -q -p no:cacheprovider --no-cov          # 方向核心 ~50 用例
$PY -m pytest tests/cartography tests/unit/raster_authoring tests/unit/test_publication_basics_wp5.py \
   -q -p no:cacheprovider --no-cov          # 受影响目录全量 3585 passed
PI_JOURNEY_E2E=1 $PY -m pytest tests/integration/test_pi_g26_mixed_export_journey.py -q --no-cov  # A26
cd frontend && npx vitest run lib/map-kit/publication-export.mixed.test.ts \
   lib/map-kit/publication-export.test.ts   # UI 路由 16 passed
```

## E0 — 关键技术探针（本地实跑，见 git 历史首 commit 记录）

- E0.1 WeasyPrint 70 内嵌 SVG 含 `<image>` → 整棵 SVG 丢弃（0 图像/0 文本/830B）
  → PDF 嵌入必须走 HTML `<img>` 分层（G15 html_body 先例的推广）。
- E0.2 HTML `<img data:image/png>` + 自定义 fetcher 返回 **URLFetcherResponse**
  （dict 被 assert 吞掉）→ 真实 PDF 图像对象（彩色+SMask）+ `Do` 算子 + 文本
  可提取。既有字体 dict 分支在 WeasyPrint 70 实际静默失效 —— 本向修复。
- E0.3 rsvg-convert 渲染 data-URI `<image>`：像素级精确（左红右蓝探针命中）。

## E1 — P0 基线（改造前）

- `tests/cartography/test_publication_vector_pdf.py`：9 passed（含
  raster_sources_disclosed_and_omitted 的省略语义 —— 本向后演进为双分支）。
- 静默丢层证据：numeric_raster 源无 tiles → SVG 分支 continue 无诊断
  （mapspec_to_svg BASE 行为；本向补齐诚实披露 + 测试锁定）。

## E2 — P1/P2 窗口渲染（11 测试，tests/unit/raster_authoring/test_publication_window.py）

- 全交集像元已知答案（冻结 stretch 逐角命中）；半区视图只读半区；4326↔3857
  重投影角/中心一致（±8 灰度容差）；不相交 → 透明 1×1 + no_overlap；nodata
  半区透明；RGB 全通道有效规则；stat_key 不符 → STALE_BINDING；style_id 篡改
  → STALE_STYLE；非法视图 typed；2048 边截断披露；内容寻址缓存同 bytes。

## E3 — P3/P4 合成/图例/PDF 嵌入

- 混合编译 13 测试（tests/cartography/test_mixed_map_publication.py）：SVG
  data-URI+文本保留、层序（栅格先于上层矢量标注）、绑定缺席披露、不相交披露、
  **窗口失败披露（退化视图；锁死 per-layer 宽 except 不吞栅格异常）**、PNG 像素
  探针（渐变角+矢量圆点）、双栅格坐标对齐、PDF 图像对象+中文提取+不误报省略、
  nodata 透明、style_id 冻结回执、4 帧窗口唯一+每页恰一绘制图像、帧无交集披露。
- 图例/字体 13 测试（test_g26_legend_and_fonts.py）：raster_rgb 通道序/不造色标/
  卡渲染、constant+approximate 脚注、单位行、无 notes 字节稳定、字体/图像
  data-URI fetcher（返回 Response 对象、24MB 上限、http/file/svg 拒绝）。

## E4 — P5 服务/路由/工具 + A26 真实 Pi

- 服务 10 测试（test_mixed_map_export_service.py）：真实 G20 提交链种子 →
  SVG/PNG/PDF 三面、CAS、格式/会话 typed、文件删除 typed（resolve_source 包裹）、
  烘焙底图 blocking vs allow_partial 清单、persist（成品+manifest+owner+签名URL）、
  G20 snapshot 兼容（pdf 拒绝、omitted_layers 保留）。
- 路由/工具 6 测试（test_mixed_export_route_and_tool.py）：/export/mixed 全链、
  CAS 409、格式 422、无栅格引导、ToolRegistry 真实 dispatch、缺会话 typed。
- **A26 真实 Pi 子进程旅程**（test_pi_g26_mixed_export_journey.py，opt-in
  PI_JOURNEY_E2E=1，1 passed ≈35s）：node stub LLM → uvicorn 生产 app（真实
  vendor/pi v1.5.1 bridge）→ 服务端真实 G20 种子 → `tool_search` 发现激活
  （ADR-0288 deferred surface）→ 裸名 `webgis_map_export` dispatch → 回执
  success + 签名 URL → 下载产物含 data:image/png 嵌入 → manifest 下载可读
  （schema_version/m apspec_revision 与种子冻结一致）。旅程发现并修复两个
  真实缺陷：manifest sidecar 所有权 fail-closed 404；degenerate 视图被
  per-layer 宽 except 静默吞。
- 前端 16 测试（publication-export.mixed + publication-export）：检测函数三态、
  栅格在场路由 exportMixedMap（不调 exportVectorPdf）、纯矢量零变化、blocking
  遗漏清单如实呈现；既有 10 测试不回归。

## E5 — P6 审计面

- manifest sidecar：schema_version/mapspec_revision/partial/omitted_layers/
  raster_layers（style_id/data_revision/overlap/embedded/网格）/renderer（引擎/
  页数/有效 DPI）/diagnostics/checksum（sha256）—— 服务测试断言 + A26 旅程下载核验。
- PDF 核验：页数/页面尺寸（MediaBox）/文本提取/绘制图像计数（内容流 Do ∩
  /Image 子类型 —— WeasyPrint 共享 Resources 陷阱已入测试注释）。
- A13 CJK 缺字扫描（脚本 /tmp 快照 + 测试内断言）：24 个常用汉字（标题/标注/
  图例/归属覆盖字符面）在真实混合 PDF 全部可提取，缺字集合为空；
  `system_cjk_font: True`（宿主）；vendored subset 路径经 fetcher 修复后可用。

## E6 — P7 回归与全量

- 受影响目录全量：**3585 passed, 3 skipped, 3 xfailed**（cartography +
  raster_authoring + publication_basics；含 golden/atlas/frame_product/label）。
- 前端全量 vitest run：0 失败。
- 旧路径字节稳定性：无 bindings 调用面输出不变（50 既有 publication 测试 +
  golden IR 测试通过；SVG 预算按剥离 data URI 核算仅在嵌入在场时生效）。

## A27（真实浏览器）状态

无授权浏览器会话环境 —— 前端链路（导出列表/下载/HUD 消息/混合路由）以
mock-API 级 vitest 覆盖；产物下载经签名 URL 在服务级可核验（A26 旅程）。
如实记 partial，不冒充完成。

## E7 — 前端全量失败归属核验（base/head 双侧）

frontend `vitest run` 全量：5179 passed / 4 failed / 1 expected-fail。4 个失败
（poi-info-panel translate 归一化、no-raw-cjk guard（G17 data-import 域裸中文）、
comparison-view ×2 clip-path `0px` 归一化）—— 均不在本方向 diff 触达的模块图
内。**base 侧复现**：同 SHA（b8f2a6e，G25 worktree，只读）运行同用例 → 同样
3+ 失败（jsdom 31 升级归一化差异 + G17 既有裸中文）—— master 既有，非本向
引入。本向触达文件（publication-export*.ts / api/publication.ts）全部通过。
