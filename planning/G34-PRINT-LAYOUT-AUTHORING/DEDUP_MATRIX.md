# G34 DEDUP MATRIX — 需求 × 生产调用链 × 归属判定

判定词表：`present` / `partial` / `missing` / `owned-by-other` / `blocked`。
每个判断都追到**生产调用链**（文件:行），不以文档声明或测试自述代替。
`present` 的要求转为**兼容回归**，不制造第二套实现。

Base = `ca7b3ec273f2b43336852951e2292a416b2238e6`；open PR = 0。

## A. 既有能力（复用边界）

| # | 要求 / 能力 | 判定 | 生产调用链证据 | G34 处置 |
|---|---|---|---|---|
| A1 | MapSpec `layout.components` 标题/副标题/装饰写入 | **present** | `frontend/lib/mapspec/studio-layout.ts:95-130` → `component-mutation.ts:80-147` → `POST /api/v1/chat/sessions/{id}/mapspec/mutations` → `mapspec_mutations.py:46-79` → `mutation_handlers._handle_setlayout:830` | 只做 additive `layout.print_layout` 分支；复用同一 CAS/锁/撤销链，不建第二 store |
| A2 | 浮动组件拖拽/resize/吸附/键盘 + 一次 CAS 收尾 | **present** | `floating-chrome.tsx:261-268, 347-397, 442-507` → `commitComponentPatch` | 复用交互原则与 single-CAS 模式；**不宣称首次拖拽**；纸面坐标另建 mm 数值属性 |
| A3 | 毫米纸张 / `PagePaper.width_mm` / `PX_PER_MM=4` / `@page size mm` | **present** | `publication_ir.py:34-39,101-115`；`publication_export.py:251-252,289-290,372,1131` | 同一 corpus 扩展 mm/pt/逻辑px/采样px 四向换算，**不全局替换 PX_PER_MM** |
| A4 | C2 LayoutIR v2 `frame/style/typography/constraints/content` 词表 | **partial** | `layout_description.py:120-210`；`frontend/lib/layout/ir.ts`；只被 `atlas_layout.py:81` / `publication_ir.py:428` 写进 `component_ir` → **零 renderer 消费** | 复用词表作为作者模型语义对齐参考，但真正消费面必须新接到 `mapspec_to_svg`；不把「填 IR」当完成 |
| A5 | `solve_layout_v5` 约束求解 / `not_evaluated` 披露 | **present** | `publication_constraint_layout.py:156-265`；`layout_solver.py:803-835`；`cartography.py:73-101` | 复用求解器；作者锁优先；默认 `not_evaluated` → 原 document identity 字节不变，**不造第二自动布局系统** |
| A6 | atlas 标准工具 / durable jobs / 用户提交下载 / page_budget | **present** | `atlas_export_tools.py:15-125`；`atlas_batch/{submit,planner,page_compiler,executor}.py`；`publication_ir.py:433-579,774-830`；`map.py:864-954` | 只加页清单预览/顺序/启用/页级覆写；不重做 planner 的 category/feature/time/frames/product 分页算法 |
| A7 | G26 受控 CJK 字体 / 混合 PDF/SVG/PNG | **present** | `publication_export.py:78-89,462-548`；`mixed_map_export.py:283-504`；`raster_authoring/publication_bindings.py:42-50` | 复用 `@font-face` 与冻结绑定；新增 per-component 字体选择 + 缺字诊断；修正全局 CSS 覆盖 |
| A8 | 多画幅 `frame_product.v1` / frame 身份 / 共享分级 | **present** | `mapspec_schema.py:1237-1238`；`publication_export._compile_product_page`；G25 frame runtime | 纸面地图槽只引用既有 frame/product；**不建第二 frame runtime** |
| A9 | `legend_spec` canonical 条目/单位/nodata/范围外 | **present** | `thematic_spec.py:31-50,150-163,295-303,627-662`；`render_scene.derive_legend_items:217-267`；`svg_marginalia.render_legend_box:170-216` | 复用条目语义与 `plan_legend_draws`；只改**排版**（列/间距/样本尺寸/分组/续排）；**不重算断点或类别值**（G33 所有） |
| A10 | G27 尺度裁决 / `PublicationScaleContext` | **present** | `publication_ir.py:927-1024`；`export_adjudicate_layer` | 保留；纸面 DPI 只影响采样，不改语义 zoom |
| A11 | ToolRegistry / dispatch / `webgis_execute` / Pi native surface | **present** | `registry.py:496-579,1145-1193,2106-2137`；`tools/__init__.py:11-176`；`pi_native_surface.py:23-159,421-581`；`extensions/webgis-tools/index.mjs:246-400` | 新标准工具只加 `_TOOL_MODULES` 一行；**不加 NATIVE_TOOL_NAMES、不注册 webgis_execute、不造旁路** |
| A12 | MapSpec mutation CAS / `expected_revision` / undo | **present** | `mapspec_mutations.py:75,245`；`intents.py:212-228`；`frontend/lib/workbench/undo.ts`；`user-mutation.ts:600-650` | 作者动作（含多组件对齐/模板应用）整笔走同一 intent + 一次 CAS；不建第二 undo 栈 |
| A13 | Workbench 用户锁 `lockedComponentIds` | **present** | `app/contracts/workbench_locks.py:36-64`；`frontend/lib/map-kit/constraint-layout-apply.ts:11-60`；`mapspec_schema.py:861-863` 明示「不设第二锁位」 | 直接消费开放 `mapspec.workbench.lockedComponentIds`；**不建第二锁位** |
| A14 | 项目保存 / 会话恢复 / localStorage 只存 anchor | **present** | `map-state-restore.ts:386-401`；`session-anchor.ts:4-8`；`persistence.ts:169-246` | 作者模型挂 MapSpec（随 map-state 恢复）；**不建 localStorage 作者文档** |
| A15 | G28 完成证据 / G23 durable job / G24 选择句柄 / G22 恢复目录 | **present** | `planning/G2*/` + 对应服务 | 只做最小 adapter；不复制证据库 |

## B. G34 专属要求（当前状态 → 本任务增量）

| # | 要求（工作包） | 判定 | 缺口的生产调用链证据 | 本任务增量（唯一所有权） |
|---|---|---|---|---|
| B1 | WP01 纸面生产路径与组件能力矩阵 | **partial** | `studio-layout.ts` 只碰 `components`；`component_ir` 零消费（见 A4） | 建立能力矩阵 + 三类真实样本；明确 opt-in 输出面 |
| B2 | WP02 有版本作者模型 + 单一权威保存 | **missing** | `MapSpecLayoutConfig` (`mapspec_schema.py:977-993`) 无 authoring 分支；`SetLayoutBody`/`SetLayoutIntent`/handler 无对应字段 | additive `print_layout.v1` 挂 `layout.print_layout`，走既有 `set_layout` + CAS；未知版本显式拒绝 |
| B3 | WP03 物理单位 / 地图槽 / 纸面预览 | **missing** | `mapspec_to_svg.py:1175-1196,1244-1266` 只有 `dpi_scale` + 全画布 project；无 mm/pt 换算 corpus、无 map slot | 新建 `units.py` 单一换算 + UI 同源 corpus；project 改为 map_rect contain/crop 等比；预览缩放不改持久坐标 |
| B4 | WP04 精确布局操作 + 现有锁 | **partial** | `floating-chrome.tsx` 有手势但只持久化 client px `placement`；`workbench_locks` 未进纸面操作 | 数值属性/多选/对齐/等距/分组/顺序 + 整笔原子提交；锁组件不动；失败不留半套 |
| B5 | WP05 受控字体 / 文本框 / 真实度量 | **missing** | `mapspec_svg_chrome_layout.py:110-128` 硬编码 `sans-serif/22/12`；无 fontTools 度量、无 lines/overflow | font allowlist + 资源版本 + 缺字/weight fallback 诊断 + 真实度量换行 + 溢出政策；修全局 CSS 覆盖 |
| B6 | WP06 图例分组/分栏/续排 | **missing** | `svg_marginalia.py:177` `entries[:max_items]` slice；`_render_chrome_groups:650-652` 只发 truncation 诊断 | 受控列数/间距/样本尺寸；容量不足 → 扩框/续排/明确阻断；**零 slice**；比例符号样本共享比例 |
| B7 | WP07 版式模板 + 受控动态文本 | **missing** | `layout.composition` (`mapspec_schema.py:987-993`) 是组合身份块，不含版式模板；无占位符绑定 | 有限版式配置模板（closed placeholders、无脚本/eval/SQL）；确定性优先级；复位只清本领域覆写 |
| B8 | WP08 图册页清单 + 有限页级编排 | **partial** | `atlas_batch/planner.py:39-85` 有 `AtlasPageBinding` 但无启用/排序/页标题/页级覆写；`publication_ir.py:774` 用 `sorted(groups.keys())` 固定顺序 | 稳定排序/启用/标题/模板/本领域覆写；页码目录按最终启用页序；遵守 `MAX_ATLAS_PAGES` |
| B9 | WP09 Studio 纸面工作台 UI + 恢复 | **missing** | `map-studio-tab.tsx:96,329-332` 只有 layout/frames/history | 薄入口 + 独立面板；generation 保护；文本编辑不触发地图快捷键；提交/草稿/字体未就绪/导出不可用四态 |
| B10 | WP10 Pi 共用作者服务 | **missing** | `tools/__init__.py:142` 末项 G26；无 print layout 工具 | 标准工具（能力/检查/有界变更/预览/应用/导出）→ 同一 service/CAS/锁；refs-only 回执 |
| B11 | WP11 接入既有编译器 + 真实成品 oracle | **missing** | `compile_publication_page:286-298` 不传任何 plan；`PageLayoutIR:137-171` 无被消费字段 | `PageLayoutIR.print_layout` → compile → `_render_chrome_groups` → 真实 SVG/PDF/PNG；DPI oracle |
| B12 | WP12 性能/失败恢复/独立 review | **missing** | 无本域取消/字体缺失/导出依赖缺失/worker 重启终态 | 取消 + 字体不可用 + 存储失败终态；独立 reviewer（第 3 槽位） |

## C. 并行边界（不得越界 / owned-by-other）

| 任务 | 本任务不得触碰 | 判定 |
|---|---|---|
| G29 Pi 原生运行中交互 | RPC `extension_ui` 协议、Pi gitlink/依赖/构建/部署契约、`pi_rpc_client` 内核 | **owned-by-other**；本任务只在固定 pin 上用现有 RPC/extension |
| G30 可执行 GIS 技能作业 | `chat.skill_name` → 技能版本/refs 绑定入口 | **owned-by-other** |
| G31 跨图层拓扑质量 | 多数据集 DQ 规则/finding/例外 | **owned-by-other** |
| G32 矢量变更提案 | 持久 feature/field/geometry 变更集、三方差异 | **owned-by-other** |
| G33 专题表达编辑 | 分类断点、类别值、颜色、图例**内容语义**、单位含义 | **owned-by-other**；G34 只改图例纸面位置/尺寸/分栏/续排，用清晰子字段 adapter，绝不覆盖 G33 字段 |
| G35 时间动画媒体 | 冻结时间帧、逐帧编码、媒体下载 | **owned-by-other**；G34 不消费 G35 产物、不含编码器 |
| G09 / G21 | 已分配但未检索到 PR | **owned-by-other**，不占用 |
| 共享入口（registry/router/i18n/chat/Studio/工具索引/MapSpec schema/编译器） | 只做本域**最小加性接线** | 本任务按 A 表约束改动，避免全文件格式化/通用重命名 |

## D. 验收场景 → 当前可观察状态（执行前）

| 场景 | 当前 | 证据 |
|---|---|---|
| 1 普通矢量图进 A4 纸面工作台 | **missing** | 无纸面工作台入口 |
| 2 标题框 x=15mm 宽=180mm 在 UI/PDF/SVG 一致 | **missing** | `mapspec_svg_chrome_layout.py:106-117` 用 `canvas_w/2`, 固定 150px 块，无 mm |
| 3 96/150/300 DPI 同版式 | **partial** | `mapspec_to_svg.py:1175` 有 dpi_scale，但 chrome/字体是硬编码未缩放 → 语义不一致 |
| 4 长中文+拉丁长词混排 | **missing** | 单行 `<text>`，无 wrap/lines |
| 5 字体缺字/weight 不存在 | **missing** | 无 font 资源探测 |
| 6 PDF 选择受支持字体不被全局 CSS 覆盖 | **missing（真实回归风险）** | `publication_export.py:378-379,1126` 全局 `svg text` 规则 |
| 7 多选三组件对齐/等距一次原子 + undo | **partial** | 有 undo 栈，但无多组件对齐操作 |
| 8 锁定组件不移动 | **partial** | 锁存在（`workbench_locks.py`），但纸面操作未消费 |
| 9 图例两列含 nodata/范围外无 slice | **missing** | `svg_marginalia.py:177` slice |
| 10 图例超框容量 | **missing** | 只发 `publication_layout_truncated` 诊断后仍截断 |
| 11 比例圆图例三数量级不失真 | **missing** | 无 proportional symbol 纸面布局 |
| 12 地图槽长窄框 contain/crop 不拉伸 | **missing** | `project():1254-1259` 全画布线性映射（非等比拉伸） |
| 13 混合数值栅格+矢量地图 | **present（G26）/ G34 整饰 missing** | `mixed_map_export.py`；整饰未接 plan |
| 14 既有多画幅产品放纸面 | **present（分页）/ G34 版面 missing** | `publication_export._compile_product_page` |
| 15 三页图册重排并关一页 | **missing** | `publication_ir.py:774` 固定 `sorted()` |
| 16 模板页级覆盖后恢复默认 | **missing** | 无模板 |
| 17 动态页标题数据缺失 | **missing** | 无绑定 |
| 18 应用前 revision 变化 | **partial** | `expected_revision` CAS 已有（`mapspec_mutations.py:245`），但作者服务未接 |
| 19 页面切换旧缩略图/字体请求返回 | **missing** | 无 generation guard |
| 20 保存项目关闭浏览器重开 | **partial** | map-state 恢复链已有（A14），作者模型未挂 |
| 21 Pi 与 UI 同一版式 | **missing** | 无共用 service |
| 22 故意让 exporter 忽略 component frame → oracle 失败 | **missing** | 目前无 oracle 可失败 |
| 23 预览/导出中取消或字体服务不可用 | **missing** | 无本域取消/降级终态 |

## E. 结论（避免空 PR / 重复实现）

- **已有且完整**：A3/A5/A6/A7/A8/A9/A10/A11/A12/A13/A14/A15 → 转为兼容回归断言，不重写。
- **已有但不完整（需最小接线）**：A1/A2/A4 → additive 字段 + 复用既有链。
- **本任务真实缺口**：B1–B12 全部；其中 **B11（真实绘制消费）是核心验收门**，只做 schema/UI/component_ir 不算完成。
- **不制造空 PR**：G34 已有足够独占增量（纸面 mm 作者模型 + 真实绘制消费 + 字体度量 + 图例续排 + 页册编排 + Pi 工具 + UI）；不依赖 G33/G35 合并。
