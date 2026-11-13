# G34 BASELINE — 最新 master / PR / review / 源码核对

任务：`G34-PRINT-LAYOUT-AUTHORING` / slug `g34-print-layout-authoring`
执行时间：2026-11-12（北京时间）；研究快照 master = `ca7b3ec273f2b43336852951e2292a416b2238e6`

## 1. 仓库身份与工作区状态（改动前记录）

| 项 | 值 |
|---|---|
| remote | `https://github.com/WindWang2/webgis-ai-agent.git` |
| `gh repo view nameWithOwner` | `WindWang2/webgis-ai-agent` ✅ |
| `defaultBranchRef` | `master` ✅ |
| 主工作区 HEAD（**stale**） | `816705218a5aa6202bf15d745cb31cddb3437f44`（Merge PR #2036） |
| 主工作区 `git status --short` | `M vendor/pi` + 36 条 `?? .claude/skills/*` + `?? _tmp_47_f754af1b0a09e630d4cf829599d2868a`；**全部只读保留，未清空/未 stash/未 reset** |
| 已有 worktree | 11 个（含 `webgis-all-issues-review`、G09/G21/G22–G27、g3dv2-b、ui-polish），均未触碰 |
| `git fetch origin master` 后 `origin/master` | `ca7b3ec273f2b43336852951e2292a416b2238e6`（Merge PR #2087 / G27）— 与研究快照一致 |
| `gh auth status` | `WindWang2` / keyring / scopes 含 `repo`, `workflow` ✅ |

## 2. 本任务工作树（新建，未占用他人分支）

| 项 | 值 |
|---|---|
| branch | `zcode/g34-print-layout-authoring-20261112T213200Z` |
| worktree | `/home/kevin/projects/webgis/webgis-worktrees/g34-print-layout-authoring-20261112T213200Z` |
| base SHA | `ca7b3ec273f2b43336852951e2292a416b2238e6` |
| 分支/路径冲突检查 | `git branch --list` 空、`git ls-remote --heads origin <branch>` 空、路径不存在 ✅ |
| 统一 heavy-stage 锁 | `/home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock`（`flock`；所有同仓 worktree 共用） |
| 资源纪律 | pytest 单进程、`--no-cov`、Vitest `--maxWorkers=2`、Playwright `--workers=1`；`-j2` 上限；本机 16 CPU / 62 GiB |

## 3. PR / review 核对（GraphQL 单次查询，30 个 PR）

- **open PR = 0**（`gh pr list --state open --limit 200` → `[]`）。无待合并兄弟任务 PR；G29–G35 均未在远端建分支（`git ls-remote --heads origin | grep -iE 'g3[0-9]|g34|print|layout'` 为空）。
- 最近 closed PR（≥50，实取 60）：#2087–#2093 = G22–G28；#2084–#2086 = 集成修复；#2073–#2081 = G12–G20；#2033–#2036 = 集成修复；#2022/#2021 = G10/G11。历史相关 #2015（出版链四断链）、#1996（出版质量护栏）、#1993（显式出版版面）、#2012/#2011 亦读。
- 对 30 个相关 PR 分别读取 body / changed files / **review submissions** / **inline review comments（reviewThreads）** / **conversation comments**：**全部为 0**（`reviews_total=0`、`inline_threads_total=0`、`conversation_total` 除 #2079/#2021 各 1 条 bot 汇总外为 0）。与研究快照一致；查询成功（`graphql_raw.json` 无 errors），区分「空列表」与「查询失败」。
- 独立评审记录确实在仓库内：`planning/G22-PI-STATE-CONTINUITY/REVIEW.md` … `planning/G28-EVIDENCE-DRIVEN-TASK-ACCEPTANCE/REVIEW.md`。已读 G26（APPROVE-WITH-FIXES → 4×P1/5×P2 → R2 APPROVE）、G27（BLOCKER+4×P1+4×P2+2×P3；真实 Pi 全链 / 浏览器 E2E / CAS 引擎链标为外部阻断，Draft PR）。**这些是 PR 作者自述 +  reviewer 记录，不是本次复跑证据。**

## 4. 已合并基线核对（必查清单）

| 组 | PR | 结论 |
|---|---|---|
| G10 #2022 / G11 #2021 / G12 #2081 / G13 #2073 / G14 #2075 / G15 #2076 / G16 #2077 / G17 #2079 / G18 #2080 / G19 #2074 / G20 #2078 | MERGED | 均已在 `origin/master` 树内；G15 `frame_product.v1`、G16 `schema v2.1`、G20 raster authoring、G12 Pi v1.5.1 原生发现均可直接复用 |
| G22 #2093 / G23 #2089 / G24 #2088 / G25 #2092 / G26 #2091 / G27 #2087 / G28 #2090 | MERGED | G22 已完成 Pi **v1.5.2 / b4afdddf** gitlink 对齐；G25 frame runtime、G26 PublicationIR 编译、G27 尺度裁决、G28 完成证据均为本任务基线 |
| #2033–#2036 / #2084–#2086 | MERGED | 命令清单、MapSpec Studio、撤销栈、ChatEngine 下线、Tailwind/类型/journey 编号修复 |
| G09 参数化批量分析 / G21 有向 OD 流图 | **未检索到 PR** | 按提示词视为**已被分配**，本任务不占用；本地 worktree `zcode/goal-g21-directed-od-flow-…` / `webgis-wt-g09-workflow-batches-…` 存在但不代表可复用其模块 |

## 5. Pi 固定 pin 与契约

| 项 | 值 |
|---|---|
| `.gitmodules` | `vendor/pi` → `https://github.com/earendil-works/pi.git` ✅ |
| `origin/master` gitlink | `160000 commit b4afdddf44e059c67ccf5097e5317d913521b470 vendor/pi` = v1.5.2 ✅ |
| 主工作区 gitlink | `f9a26365568127461c47a07a37dcb42c140a3ac5`（旧，`M vendor/pi`）；**不修改主工作区** |
| 本任务处理 | 在**自己 worktree** 初始化固定 pin（只读构建），不改 vendor 源码 / 依赖 / 部署契约；G29 才拥有底层修改权 |
| RPC 约束 | `NATIVE_TOOL_NAMES` 冻结 7 个；`webgis_execute` 仅存在于 Pi 扩展、**不得注册进 ToolRegistry**；新标准工具注册到 `ToolRegistry` 后经动态 registered surface 或 `webgis_execute` proxy 到同一 `dispatch` |
| Pi-only | bridge 不可用 → unavailable/503，**不恢复 ChatEngine**（#2036 已下线；CONTEXT.md 仍留有历史描述，属文档冲突，以生产代码/ADR 为准，不恢复旧架构） |

## 6. 生产链权威结论（追到调用链，非文档推断）

- `PageLayoutIR.component_ir`（`app/lib/cartography/publication_ir.py:165-171`）文档自述 **「导出渲染循环不消费本字段」**；全仓仅 IR 生产 / golden fixture / 测试断言 / BASELINE 引用，**零生产 renderer 消费**。→ G34 不能只填 `component_ir`。
- 真实绘制链：`publication_export.compile_publication_page` (:234) → `mapspec_to_svg.compile_mapspec_to_svg_detailed` (:1068) → `_render_chrome_groups` (:539) → `mapspec_svg_chrome_layout._render_title_chrome` (:75) / `svg_marginalia.render_legend_box` (:168)。组件事实源是 **MapSpec `layout.components`**（`render_scene.resolve_components:152-157`），不是 `component_ir`。
- `compile_publication_page` 从 `PageLayoutIR` 只读 `paper.width_mm/height_mm, cover, frame_slots, frame_index, time_window, filter_layer_id, bounds`；`width=int(page_w*4)` 是 mm→逻辑 px 唯一落点（`PX_PER_MM=4`）。
- `mapspec_to_svg.py:1175` `dpi_scale = target_dpi/72`；`scaled_width = width_val*dpi_scale`，`viewBox` 用 scaled 值，`width` 属性用未缩放值 → 物理 mm 恒定、逻辑 user unit 随 DPI 线性变化。G34 的 mm/pt 换算必须走同一 corpus。
- `assemble_pages_pdf:378-379` 与 `_svg_to_page_html:1126` 的 **`svg, svg text {font-family: …}` 全局规则会覆盖 SVG 的 `font-family` presentation attribute**（验收场景 6 的真实风险）。
- `svg_marginalia.render_legend_box:177` `entries[:max_items]` 会 **slice 丢尾项**（验收场景 9/10 的真实风险）。
- `mapspec_svg_chrome_layout._render_title_chrome:110-128` 硬编码 `font-family="sans-serif" font-size="22"/"12"`，无换行、无度量（场景 4/5/6）。
- `_render_chrome_groups:584-703` 所有 chrome 坐标为硬编码 canvas px 常量，未按 mm 定位；`project()` (:1244) 用全画布 + `scaled_padding`，无地图槽 contain/crop。
- `PublicationPdfResult` / `PageCompileOutcome` / `SvgCompilation` 均无 authoring 字段 → 需要 additive 回执而非另建证据库。
- 约束求解器 `solve_layout_v5` 的 `placements` 只进 `ConstraintLayoutReport` 回执，不改可绘制几何；默认 `not_evaluated` 必须返回原 document identity（字节稳定契约）。

## 7. 本地验证环境（实测）

| 项 | 结果 |
|---|---|
| 主工作区 `.venv` | Python 3.13.17，含 pydantic/pytest 9.1.1/fastapi/**weasyprint/pypdf**/PIL/shapely/numpy/rasterio/lxml/**fontTools**/pydantic_settings/sqlalchemy/httpx ✅（从 worktree 以只读方式复用） |
| worktree import 实测 | `app.lib.cartography.publication_ir` / `app.services.publication_export` / `thematic_spec` 均从本 worktree 解析 ✅ |
| pytest 收集 | `tests/cartography/test_publication_ir_golden.py` 18 tests collected（`--no-cov` 后续使用以避开 85s 全仓 coverage 扫描） |
| 字体 | vendored `app/lib/cartography/fonts/NotoSansSC-Regular-subset.ttf`（7777 glyphs, upem 1000, 含「中」与 `A`）；系统 `NotoSansCJK-*.ttc` / `NotoSerifCJK-*.ttc` / `DejaVuSans*.ttf` 可用；fontTools 可实测度量与缺字 |
| frontend | 主工作区 `frontend/node_modules` 存在（vitest 4.x、playwright 1.65、typescript 5）；worktree 需 `--prefer-offline` 或复用；`pnpm` 可用 |
| vendor/pi rpc-entry | 主工作区未构建 `dist/bundle/rpc-entry.js`；本任务如需真实 Pi 子进程旅程，须在自己 worktree 初始化固定 pin 并 `scripts/build-vendor-pi.sh`（**重阶段，取锁**，如实记录是否成功） |

## 8. open issues / 远端分支相关性映射

- open issues = 45，绝大多数是 **R1 审计批次**（#2037–#2072 平台/数据/agent/felib 质量门）+ 少量 #2004–#2007。与 G34 直接相关者：**#2037**（批量图册提交双重 JSON 编码，属图册提交面，本任务不重做 atlas 执行器）、**#1996**（出版质量护栏，已 merged）。其余 R1 问题是应尊重的质量门，**不作为本任务目标**，不关闭任何审计 issue。
- 远端分支：无 `g34` / `print` / `layout` 相关分支；G09/G21 本地 worktree 属他人分配，不占用。
- 版本编号查重：ADR 最高 `0289`（`0286/0287/0288` 有重复编号）；migration 主链最高 `0104_map_briefs`；journey ID 最高 `j22`（缺 j19/j20/j21）。本任务使用带任务号的唯一编号，提交前对最新 master 复查。

## 9. 文档冲突记录

- `CONTEXT.md:51-59, 176-180, 289-291` 仍描述 `ChatExecutionEngine` / legacy ChatEngine+Pi 双路径 / `native-tools dump` 失败回落 ChatEngine；实际生产 #2036 已下线 ChatEngine，`pi_native_surface.py` 的 dump 失败是 **abort spawn**，不是回落。→ 以生产代码与更新 ADR 为准，**不恢复旧架构**。
- `publication_ir.py` 的 `component_ir` 文档明确其不被消费；本任务把它保留为兼容字段，另建真正被消费的 authoring 通道，避免把旧文档解释成新语义。
