# ADR-0290 — 专业纸面版式与图册编排工作台（G34）

状态：Accepted（2026-11-12，G34 任务范围）
相关：ADR-0051（MapSpec mutation CAS）、ADR-0118（layout 字段级 merge）、
ADR-0211（publication chrome 覆盖回执）、ADR-0214（组合契约身份）、
ADR-0160（C2 LayoutIR v2）、ADR-0282（Pi 原生 RPC）、ADR-0286。
编号查重：`docs/adr/` 现存最高编号 0289；0286/0287/0288 有历史重号
（已由 `tests/test_adr_numbering_contract.py` 记录为既有基线失败），
本 ADR 使用唯一 0290，不参与重号。

## 背景与问题

`MapSpec.layout.components` 已能声明标题/装饰开关，`PublicationIR` 已有毫米
纸张与 frame slots，`publication_export` 已能产出真实矢量 PDF，G26 已提供
受控 CJK 字体与混合栅格嵌入，G15/G25 已提供多画幅产品。但**没有任何
「纸面作者」通道**：

* `PageLayoutIR.component_ir` 文档明确写「导出渲染循环不消费本字段」，
  只流向 golden fixtures 与测试断言；往里填字段不改变任何成品字节。
* `mapspec_to_svg._render_chrome_groups` 的组件坐标是硬编码 canvas px，
  `mapspec_svg_chrome_layout._render_title_chrome` 的字体/字号是硬编码
  `sans-serif / 22 / 12`，无换行、无真实度量。
* `svg_marginalia.render_legend_box` 用 `entries[:max_items]` 直接 slice 丢尾项。
* `project()` 把经度和墨卡托 Y 分别线性映射到整幅画布（非等比拉伸），
  没有地图槽 contain/crop 语义，也没有有效范围披露。
* `assemble_pages_pdf` / `_svg_to_page_html` 的全局
  `svg, svg text { font-family: … }` 会以 CSS 优先级静默覆盖作者字体。

因此「在浏览器里看起来对齐」与「PDF/SVG/PNG 成品真实几何」没有共同决定。

## 决定

### 1. 单一权威承载位：`MapSpec.layout.print_layout`（`print_layout.v1`）

新增 `app/lib/cartography/print_layout_schema.py`，Pydantic v2、
`extra="forbid"`、关闭词表、长度/数量/数值有界、未知版本显式拒绝
（`UNKNOWN_PRINT_LAYOUT_VERSION`）。additive 挂到 `MapSpecLayoutConfig`；
写入面复用既有 `set_layout` intent + `expected_revision` CAS + checkpoint +
provenance + 撤销栈。**不新建**第二套 MapSpec store、layout undo 栈、项目
版本库、全局 ID、事件总线或证据库。无该分支时旧绘制路径与字节完全不变。

### 2. 作者决定必须到达真实绘制消费面

新增 `PageLayoutIR.print_layout`（与 `component_ir` 的 write-only 性质明确
区分），由 `plan_publication_pages` 装配、`compile_publication_page` 真实读取
并下传 `compile_mapspec_to_svg_detailed(print_layout=..., paper_mm=...)`；
后者调用 `print_layout_authoring/compile.py::build_draw_plan` 得到毫米框、
pt 字体、真实文字行、图例分栏/续排、地图槽等比映射，最终由
`_render_authored_chrome` / `project()` / `render_legend_box_authored`
画进 SVG；PDF/PNG 复用既有 WeasyPrint / rsvg-convert 装配。
G15 product 页由 `print_layout_authoring/html.py` 把同一计划同时投影到
外层 SVG 与 HTML 组合面，不新建 live runtime。

### 3. 四类单位分离，单一换算 corpus

`units.py` 是唯一事实源：逻辑 px（`PX_PER_MM=4`，不替换既有常量）、
SVG viewBox user unit（`mm*4*dpi/72`）、pt（`pt*4*(dpi/72)/PT_PER_MM`）、
采样 px（`mm*dpi/25.4`）。Python/TS/golden 三方逐字段对拍；96/150/300 DPI
下物理毫米与字号恒定，仅 user unit 数与 PNG 采样像素数变化。

### 4. 受控字体与真实度量

允许列表只含部署中真实存在且可被引擎加载的资源，运行时用 fontTools 探测
文件、cmap、hmtx；`resolve_font` / `check_glyphs` / `wrap_text` 给出
`fallback_used`、`FONT_WEIGHT_UNAVAILABLE`、`missing_glyphs`、
真实 `lines` / `overflow` / `status`。绝不把字符串长度当精确测量，
绝不把用户字符串拼成字体 URL。G34 PDF opt-in 时无条件内嵌同一份
vendored 子集（G26 只在系统无 CJK 字体时嵌入），且全局 CSS 兜底改为
`svg text:not([font-family])`，不静默覆盖作者选择；旧路径逐字不变。

### 5. 图例完整性零 slice

从 canonical `legend_spec` / `derive_legend_items` 读全部条目（含 nodata、
范围外、unit、notes、比例符号），G34 只排版不重算断点或类别值。
容量不足时按 `expand | continue | block` 处理并披露；比例符号共享同一
面积比例尺度，不因分栏分别缩放。

### 6. 页册策划与受控动态文本

`prepare_pages` 消费既有 `plan_publication_pages` 的真实分页，页身份与数据
绑定以 `binding_digest` 固化；排序/启用/标题只改 `order/enabled/title`，
页码与目录从最终启用清单计算。模板是有限版式配置（无脚本/eval/SQL），
绑定只读可信元数据，缺值显示可见占位。模板/页级覆写/草稿优先级确定；
复位只清本领域覆写，不碰源图标题、人工标签、锁、图层可见性或 frame 参数。
作者计划摘要参与 atlas 页缓存 fingerprint，避免串页。

### 7. Pi 与 UI 共用同一服务

`app/services/print_layout_authoring/service.py` 是唯一入口；REST 路由
`/api/v1/print-layout/*` 与 7 个 `webgis_print_layout_*` 标准工具都调用它。
工具注册到既有 `ToolRegistry`（`_TOOL_MODULES` 追加一行），经 dynamic
registered surface 或 `webgis_execute` 长尾代理到同一 `dispatch`；
**不**加入冻结的 `NATIVE_TOOL_NAMES`、**不**注册 `webgis_execute`、
**不**改 Pi RPC 内核或 vendor pin。所有写一次 dry-run → 一次 `set_layout`
intent → 一次 CAS；用户锁来自 `workbench.lockedComponentIds`，选中集合含锁
组件默认整笔拒绝，避免半应用模板。回执 refs-only，不把整页像素、字体 bytes
或栅格数组送进模型上下文。

## 后果

* 纸面作者能力与主 WebGIS 视窗共存且语义明确；主视窗不必变成印刷页面。
* 旧 spec 无 `layout.print_layout` → 全部既有导出字节不变（回归 oracle 锁定）。
* SVG/PDF 保留矢量文字；PNG 明确不具备可搜索文字（产物 oracle 分别验证）。
* 新增字段进 `PublicationIR` golden（按仓库 `PUBLICATION_IR_UPDATE_GOLDEN`
  流程刷新，仅新增空 `print_layout: {}`），`types.generated.ts` 按
  `ts_projection` 重新生成（仅新增一行）。
* 不宣称 CMYK/ICC 印前认证、任意字体上传或复杂文字系统 shaping。

## 回退

删除 `MapSpec.layout.print_layout` 分支即完全回到旧绘制路径；所有新增代码
路径都是 opt-in（由该分支存在与否驱动）。不需要数据迁移（无新表）。
