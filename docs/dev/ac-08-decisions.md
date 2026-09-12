# ac-08 出版级导出 V10 · 决策日志（docs/dev/ac-08-decisions.md）

按时间序记录实现中的决策与理由；重大架构决策见 `docs/adr/0157-publication-grade-export-v10.md`。

| # | 日期 | 决策 | 理由 / 备选方案 |
|---|---|---|---|
| D1 | 09-13 | `pip install -e .` 不可行 → requirements 安装 + `pytest.ini pythonpath=.` | pyproject 无 build-system，setuptools flat-layout 自动发现撞多顶层目录拒建；兄弟 worktree（ac-01…09）同。任务书 §0.1 该步为环境建议而非产物。 |
| D2 | 09-13 | P1 增量重定义：超时降级（非失败）+ 引擎单飞 + 栅格细节披露；不重建离屏地图实例 | 真重渲染已在 master（#527 修复）；tilezoom-dpr-probe 实证栅格取图 zoom 与 DPR 无关 → 栅格细节增益归矢量引擎（孪生 oversample ≤2 已有 parity 测试）。离屏重建有 style/图标闭包保真风险，且涉 06 线 runtime 边界。 |
| D3 | 09-13 | CJK 字体 = 仓内 vendored Noto Sans SC 子集（OFL；GB2312 全表 + ASCII + 标点；wght=400 静态 glyf TTF 2.3MB；双面部署） | jsPDF 仅支持 glyf TTF；仓内原零字体；fonttools instancer+subset 可复现（构建命令入 fonts/README）。备选转曲需字形向量数据（同样依赖字体文件）故非独立路径；栅格化降为最后兜底（§0.5 契约）。 |
| D4 | 09-13 | 版面描述中间层由本线定义（07 未合入）；chromeModel 为 canvas 运行时面，不入跨语言 JSON 契约 | §2 P2 协调条款；golden corpus 的 JSON 对拍面须双端可镜像，canvas 绘制器消费面是 TS 内部实现细节（金样已 pin）。 |
| D5 | 09-13 | WYSIWYG = 导出范围 ⊇ 遮罩且纵横比=图框（中心不动点扩张），非逐像素相等 | 纸张纵横比 ≠ 视口纵横比时逐像素相等无定义；扩张语义保「零内容裁切」+「裁切即导出范围」两条硬契约；≤1px 由解析投影测试 + fitBounds(duration 0) 接线测试覆盖。修复 extent.ts 度/归一化混用 bug（测试捕获）。 |
| D6 | 09-13 | P4「出图范围预览」以数据 + 诊断形态交付（exportExtent/maskExtent/overflow 入 IR 与 sidecar） | 可视化预览组件位于 `frontend/components/map/**`（07 线禁改区）；IR 即 07 可消费的预览数据面。 |
| D7 | 09-13 | CMYK 档：出血/裁切几何真实生效（PDF 超尺寸页 + trim 角线；SVG 标记）；色彩转换仅近似并披露 `cmyk_approximate_raster` | 栅格链（PNG/客户端 PDF）无 ICC 分色通道；假装精确违反 §0.5。真分色登记 follow-up（出版引擎/后端）。 |
| D8 | 09-13 | vector-pdf 死接口（#1213）的收口策略：本线打通「版面/字体/范围」三处同源 + 文本层与像素门禁，端点接线交由产品面决策 | 端点要求 ref 源水合 + WeasyPrint 可用（本机无 pango，容器字体未装）；narrative-export.ts:7-13 已明确文档场景不采用。强行接线会把 503/429 降级面暴露为用户主路径，风险大于收益；本线交付使其「可被接线」的全部前置。 |
| D9 | 09-13 | SVG 内容态标记 `data-export-content="vector|mixed"`（§5 mixed 语义） | 矢量编译对不支持的层类型（extrusion 压平/heatmap 近似/栅格外链）已有逐层诚实标记；根节点 content 态把「回落不静默丢层」升为机器可读断言。位图逐层嵌入需 live map 访问，超出纯编译器边界，保持复合回退路径（vector_svg_fallback_raster）。 |
| D10 | 09-13 | WeasyPrint 本机不可用（Windows 无 libpango）→ 后端对拍不依赖 WeasyPrint 渲染（golden IR parity + pdf_renderer 整饰单元测试 + 既有 503/降级语义测试） | 仓内既有降级语义（import OSError 捕获）即为此设计；容器级字体/pango 安装属 Dockerfile（禁改），登记 follow-up。 |
