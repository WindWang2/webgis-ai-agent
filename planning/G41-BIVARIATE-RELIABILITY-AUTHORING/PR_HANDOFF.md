# G41 PR HANDOFF — 双变量与可靠性作者工作台

## 一句话

让用户和 Pi 通过**同一套工具**，把已有矢量数据作者化成两类可出版的地图：两数值字段的
双变量分级矩阵（2×2 / 3×3），以及「估计值 + 有来源的不确定度量」的双通道表达。两类都走
真实 UI、可应用/撤销/重开、canonical 图例进四个输出面，SVG/PNG/PDF 真实回读。

## 改动面（47 文件，+8194 / −20）

| 层 | 文件 | 作用 |
|---|---|---|
| 领域服务 | `app/services/bivariate_reliability_authoring/{contract,snapshot,stats,classify,reliability,legend,expression,cache,service,errors}.py` | 计划解析与词表、强同代冻结、成对统计、矩阵分级、可靠性分桶、canonical 图例、paint 投影、候选缓存、三段式提交 |
| 工具/路由 | `app/tools/bivariate_reliability_tools.py`、`app/api/routes/bivariate_reliability_authoring.py`、`app/tools/__init__.py`、`app/main.py` | 3 个 typed 工具 + 3 条 REST（`require_owned_session`），加性接线 |
| 出版 | `app/lib/cartography/svg_marginalia.py`（`render_legend_box_authored`）、`app/services/print_layout_authoring/legend.py` | 服务端真实绘制矩阵/可靠性图例，透传 canonical 结构 |
| 前端 | `frontend/lib/bivariate-reliability/{contracts,api}.ts`、`components/hud/bivariate-reliability-panel.tsx`、`layer-style-panel.tsx` | 作者面板（两模式、轴/方法/断点/单位/误差度量、预览→应用→撤销） |
| 图例与导出 | `legends.tsx`、`thematic-legend.tsx`、`export-chrome.ts`、`vector-svg-export.ts`、`types.ts` | 画布/SVG/PDF 同源图例；删除旧的「矩阵已省略」假降级 |
| i18n | `messages/{en-US,zh-CN}/{hud,map}.json` | 全量双语，无裸 CJK 泄漏（新增文件 0 处） |

## 关键语义决定（评审要点）

1. **强版本成对**：以权威单调 `content_revision` 前后核对 + 同会话锁 + 单次 `get()` 深拷贝；
   ABA 由单调代捕获；descriptor 缺失/rev=0 → typed 拒绝（`NO_STRONG_REVISION`）。
2. **旧图零改写**：既有 `__biv_class` / 旧 bivariate 图不改写、不被 inspect 触碰；新派生字段
   独立命名（`__g41_pair_class` / `__g41_rel_bucket`），冲突则 typed 拒绝。
3. **边界归属显式**：新 `boundary_rule`（缺省 `upper_inclusive`）贯穿矩阵与可靠性分桶两个通道；
   `legacy_lower_inclusive` 仅显式选用，用于对齐旧图。
4. **SE ≠ 方差 ≠ CI ≠ PI**：`measure.kind` 显式；方差要求平方单位；负值/低>高/非法 level →
   `invalid`，不夹取、不自动排序、不补 95%。
5. **缺失 ≠ 可靠**：`unknown` / `invalid` / `out_of_domain` 三个独立桶；`clip=clamp` 的越界样本
   单独成桶并披露，`clip=reject` typed 拒绝。
6. **不伪造统计信心**：无误差事实 → `NO_UNCERTAINTY_EVIDENCE` + 可行下一步；`author_declared`
   必填来源说明；区间是否居中由 `allow_noncentered` 真实控制。
7. **第三通道明确不支持**：两主变量 + 不确定度写入图例 `disclosures`，不用三角编码冒充。
8. **可逆**：提交记录整层 `previous_{source_ref,paint,legend_spec,data_binding}`；撤销原子恢复
   到**本域作者决定之前**的来源，而非 base 源。
9. **长名不静默截断**：可见字形可缩短，完整文本保留在 `data-g41-*-full` / `<title>` 并可回读。

## 证据（同版本实跑）

| 项 | 结果 |
|---|---|
| 后端单测 `tests/unit/bivariate_reliability` | **73 passed**（含手算 oracle，不拿生产函数生成期望） |
| 后端回归（thematic_authoring / G34 / v4 cartography / analysis converter） | **138 passed, 2 skipped** |
| 出版三格式回读 `test_g41_publication_readback.py` | **6 passed**（SVG/PNG/PDF 独立回读、长名、跨 DPI、拒绝普通色条替代、混合图层完整性） |
| 真实 Pi 旅程 `PI_JOURNEY_E2E=1` | **1 passed，18/18 gates**，pin `v1.5.2-0-gb4afdddf4`（`matches_expected_pin=True`） |
| 前端 G41 专项 | **140 passed（14 文件）** |
| 前端回归（受影响 12 文件） | 见 TEST_MATRIX T9 |
| `tsc --noEmit` | 新代码 0 错误；仅 2 处既有 `cartography-result-card.tsx` |

Pi 旅程只 stub LLM 响应：RPC entry、tool_search deferred 激活、`ToolDispatchService`、
`UpsertLayerIntent` 写入、派生 ref 冻结、受权下载回读都是生产代码。

## 已知边界（不声称覆盖）

- 真实浏览器 E2E（Playwright）**未跑**：环境无浏览器可执行文件；jsdom + 生产渲染回读不等于浏览器全链。
- 深色底图 alpha 的真实像素对比**未跑**，只有结构/数值/有界输出断言。
- B16 选择句柄（>50 对象 / MVT 局部 handle）**未实现**，属 G24/G38 边界；非 `ref:` 源明确拒绝。
- 模板 / 多 frame / atlas 组合导出**未测**。
- 既有失败（本次未改文件）：`tsc` 2 处、`no-raw-cjk` 4 文件、`check_import_boundaries` 9 条。

## 回滚

单笔 `UpsertLayerIntent`，撤销即 `mode="revert"`；整笔历史用 MapSpec undo。无 schema 迁移、
无新表、无后台任务，删除本 PR 不遗留数据。

## 复审

见 `planning/G41-BIVARIATE-RELIABILITY-AUTHORING/REVIEW.md`：第 1 轮 6 项 P0/P1 全部修复并复验，
第 2 轮由同一独立 reviewer 对合流 diff 复审。
