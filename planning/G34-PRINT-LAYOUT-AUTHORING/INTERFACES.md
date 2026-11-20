# G34 INTERFACES — 契约、编号查重与共享边界

## 1. 版本与编号（创建时查重，提交前复查最新 master）

| 项 | 值 | 查重依据 |
|---|---|---|
| schema version | `print_layout.v1` | 现有 `frame_product.v1` / `atlas_job.v1` / `raster_expression.v1` / `gis_journey_evidence.v1`；无同名 |
| ADR | `docs/adr/0290-print-layout-authoring.md` | 最高 `0289`；`0286/0287/0288` 有重复编号，故从 `0290` 起 |
| migration | **不需要**（作者模型随 MapSpec additive 存于既有 store，不新增表） | 主链最高 `0104_map_briefs` |
| 测试/旅程 ID 前缀 | `g34-` / `G34-`；Pi journey 名 `g34_print_layout_authoring` | journey registry 最高 `j22`（缺 j19/j20/j21）→ 若新增 Playwright journey 用 `j23` 且同步 `registry.ts` + `changed-lanes.mjs` |
| 工具名 | `webgis_print_layout_*` | `ToolRegistry` 现有无此前缀；`webgis_execute` 不注册 |
| REST 路由 | `/api/v1/print-layout` | 现有无同名 |
| 模板 ID | `tpl:<slug>:<version>`，slug 用 closed 词表 | 无冲突 |

## 2. 作者模型（`app/lib/cartography/print_layout_schema.py`）

`MapSpec.layout.print_layout`（Pydantic `extra="forbid"`，关闭词表，长度/数量/数值有界）：

```
PrintLayoutV1
  version: Literal["print_layout.v1"]
  paper: {profile: A4|A3|..., width_mm, height_mm, orientation}   # 复用 PAGE_PROFILES
  safe_area_mm: {top,right,bottom,left} >=0
  source: {mapspec_revision:int, style_revision:str, spec_fingerprint:str}
  font_asset_version: str            # sha256 of 受控字体资源集
  render_path: "publication_ir_svg_v1" (closed)
  template: {id, version} | None
  components: list[PrintComponent]   # ≤32（C2 上限），id 唯一
      id (复用 MapSpecComponent.id，不新建身份)
      kind: title|subtitle|legend|scale_bar|north_arrow|attribution|
            disclosure|map_slot|text_block|page_number|toc|group  (closed)
      frame_mm: {x,y,width,height}  # 毫米，含 0 ≤ x+width ≤ paper.width_mm
      z: int (0..31)
      group_id: str|None
      locked: bool                  # 投影自 workbench.lockedComponentIds，不建第二锁位
      typography: {font_id, size_pt, line_height, align, letter_spacing_pt,
                   color, wrap_mode, overflow_policy} | None
      text: {mode: literal|binding, value, binding} | None
      legend: {columns, column_gap_mm, swatch_mm, symbol_mm, row_gap_mm,
               group_by_layer, continuation} | None
      map_slot: {frame_id|None, fit: contain|crop, effective_extent} | None
  legend_defaults: PrintLegendLayout
  atlas: {pages: list[PrintPage] (≤20), toc: bool, page_number_start:int}
      PrintPage: {page_id (复用 AtlasPageBinding.page_id), enabled, order,
                  title, paper_profile|None, template_id|None,
                  overrides: list[PrintComponentOverride]}
  updated_at: str
```

**硬约束**：`version != "print_layout.v1"` → 显式 `UNKNOWN_PRINT_LAYOUT_VERSION` 拒绝；所有文本 ≤2048 bytes、模板 ≤16KB、组件 ≤32、页 ≤现有 `MAX_ATLAS_PAGES`。

## 3. 单位 corpus（`units.py`，Python 权威 + TS 镜像 + golden parity）

来源：`PX_PER_MM = 4`（`publication_ir.py`）、`dpi_scale = target_dpi/72`（`mapspec_to_svg.py:1175`）。

| 换算 | 公式 | 说明 |
|---|---|---|
| 逻辑 px | `mm * PX_PER_MM` | IR/浏览器设计面；**不替换常量** |
| SVG viewBox user unit | `mm * PX_PER_MM * (dpi/72)` = `mm*dpi/18` | 与 `scaled_width = width_val*dpi_scale` 同源 |
| pt → user unit | `pt * (dpi/72) / PT_PER_MM` = `pt*dpi*25.4/1296` | `PT_PER_MM=72/25.4`；物理字号不随 DPI 变 |
| 采样 px（栅格化） | `mm * dpi/25.4` | PNG 输出像素数随 DPI 变 |
| 反向 oracle | `user_unit * 18/dpi → mm` | 测试从 SVG 产物量回毫米 |

**单一 corpus**：`UNITS_CORPUS = {px_per_mm, pt_per_mm, logical_px_per_mm, dpi_divisor}`，Python 与 TS 各持一份，由 golden fixture `tests/cartography/golden_corpus/print_layout_units/basic.json` 逐字段对拍。

## 4. 领域服务接口（`app/services/print_layout_authoring/service.py`）

UI（REST）与 Pi（tool）**共用**，返回有界回执（不返回整页像素/字体 bytes）：

| 函数 | 输入 | 回执 |
|---|---|---|
| `capabilities()` | — | 纸张 profiles、字体资源（id/family/weight/style/available/sha256）、kind 词表、预算上限、render_path |
| `inspect(mapspec, *, session_id)` | 当前 spec | plan（或 `not_configured`）、pages、fonts、locks、validation、`spec_fingerprint` |
| `prepare_pages(mapspec, atlas_policy)` | 现有 planner 产物 | 真实页清单（page_id/title/binding digest/enabled/order/thumb ref）+ 目录/总数 |
| `propose(mapspec, proposal)` | 有界变更列表 | `dry_run` 结果：将采用的精确值、覆盖范围、锁跳过项、validation、冲突 |
| `apply(mapspec, proposal, *, expected_revision, origin)` | 同上 | 原子整笔 `set_layout` intent；`mutation_revision` + 回执；stale → `SUPERSEDED` |
| `preview(mapspec, *, page_id, dpi)` | plan | 组件 mm 框、lines/overflow、图例条目数/列数、比例尺 mpp/有效 extent、字体状态、warnings、SVG sha256（**不含 SVG 全文**） |
| `templates_save/apply/reset(mapspec, ...)` | 模板 | 模板版本/覆盖列表/复位只清 G34 覆写 |
| `submit_export(...)` | 现有导出入口 | `download_url` / `manifest_url` / `job_id` refs-only |

**原子性**：`propose` 先整体验证；`apply` 只提交一次 `set_layout`。任何失败 → 零写入，不留半应用模板。

## 5. 真实绘制合同（WP11 关键）

```
MapSpec layout.print_layout
  → PublicationIR.plan_publication_pages(): 每页装配 page.print_layout（含页级 override）
  → publication_export.compile_publication_page(): print_layout=... 传入
  → mapspec_to_svg.compile_mapspec_to_svg_detailed(print_layout=..., paper_mm=..., page_id=...)
      → project(): map_slot rect + contain/crop 等比映射（effective_extent 可查）
      → _render_chrome_groups(draw_plan): 组件 frame_mm → user units；typography → 真实 font-size
      → mapspec_svg_chrome_layout._render_title_chrome(draw_plan): 真实度量换行 lines
      → svg_marginalia.render_legend_box_authored(): 分栏/续排/比例符号不失真
  → assemble_pages_pdf(): @page size mm；`svg text:not([font-family])` 才套全局栈
  → PDF / SVG / PNG 产物
```

- **旧路径**：`print_layout=None` → 所有旧调用签名与输出字节不变（golden + sha256 锁定）。
- **PDF 字体**：作者选 `font_id` → SVG `font-family` presentation attribute；全局 CSS 改成 `:not([font-family])`，避免静默覆盖。
- **DPI**：同一 mm plan 在 96/150/300 下，SVG `width` 属性不变、`viewBox` 随 DPI 变；oracle 反算 mm 恒定；PNG 采样像素数随 DPI 变。
- **回执**：`SvgCompilation` / `PageCompileOutcome` additive `print_layout` 字段（version/page_id/component_geometry/legend_entries/text_lines/scale/effective_extent/font_status），供 UI/Pi 消费；不建第二证据库。

## 6. Pi 工具面（稳定工具，不改 RPC）

| 工具名 | tier | 作用 |
|---|---|---|
| `webgis_print_layout_capabilities` | 2 | 读取能力/字体/预算 |
| `webgis_print_layout_inspect` | 2 | 检查当前版式/页清单/锁/validation |
| `webgis_print_layout_propose` | 2 | 有界变更 dry-run |
| `webgis_print_layout_apply` | 2 | 原子应用（同一 service/CAS/锁） |
| `webgis_print_layout_preview` | 2 | 预览回执 |
| `webgis_print_layout_export` | 2 | 提交现有导出，返回 refs |

注册：`app/tools/__init__.py::_TOOL_MODULES` 追加 1 行。**不加 `NATIVE_TOOL_NAMES`**，不注册 `webgis_execute`；工具经 dynamic registered surface 或 `webgis_execute({toolName,arguments})` 到同一 `ToolRegistry.dispatch`。`session_id` 由 dispatcher 注入；缺 session → `SOURCE_UNAVAILABLE`。

## 7. 共享 legend / 锁 / 版本边界（避免互相覆盖）

- **legend 模型**：G33 拥有 `legend_spec` 的 `entries/color/label/breaks/unit`；G34 只在其旁加 `print_legend_layout` 子字段（columns/column_gap_mm/swatch_mm/...）。adapter 从 `derive_legend_items()` 读，**绝不写回** G33 字段。
- **锁**：唯一事实 `mapspec.workbench.lockedComponentIds`（`app/contracts/workbench_locks.py`）。`PrintComponent.locked` 是**只读投影**，每次 inspect 重新计算，不作为第二锁位。
- **页身份**：`PrintPage.page_id` 复用 `AtlasPageBinding.page_id`；排序只改 `order/enabled`，不重排绑定数据；`planner` fingerprint 纳入 plan 版本 + 页覆写，避免 executor 页缓存串页。
- **模板优先级**：`页级 override > 当前草稿 > 模板默认 > 内置兜底`；复位只删 G34 override，绝不触碰源图标题、人工标签、锁、图层可见性、frame 参数。
- **stale**：`expected_revision` 不匹配 → `SUPERSEDED`，不混合新旧页面内容。
