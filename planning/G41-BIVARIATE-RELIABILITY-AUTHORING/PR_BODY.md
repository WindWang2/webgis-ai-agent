## 目标

让用户与 Pi 通过**同一套工具入口**，把已有矢量数据作者化成两类可出版地图：

1. **双变量分级矩阵**（2×2 / 3×3）：两个数值字段的联合分级，含断点/方法/单位/边界归属与联合计数；
2. **估计值 + 有来源不确定度量的双通道表达**：估计值走色阶，不确定度走第二通道（误差/区间/等级），缺失与不可靠分开。

两类都要求：真实作者 UI、可预览→应用→撤销→重开、canonical 图例同源进画布/SVG/PDF/服务端排版、三格式真实回读。

## 语义红线（本 PR 的核心）

- **SE ≠ 方差 ≠ CI ≠ PI**：`measure.kind` 显式；方差要求平方单位；负值、低>高、非法 level 一律 `invalid`，不夹取、不自动排序、不补 95%。
- **缺失 ≠ 可靠**：`unknown` / `invalid` / `out_of_domain` 三个独立桶；`clip=clamp` 的越界样本单独成桶并披露；`clip=reject` typed 拒绝。
- **不伪造统计信心**：没有误差事实就 `NO_UNCERTAINTY_EVIDENCE` 并给可行下一步；`author_declared` 必须写来源；区间是否居中由 `allow_noncentered` 真实控制。
- **旧图零改写**：既有 `__biv_class` 与旧 bivariate 图不被触碰；新派生字段独立命名，冲突则拒绝。
- **长名不静默截断**：可见字形可缩短，完整文本保留在 `data-g41-*-full` / `<title>` 并可回读。
- **第三通道（两主变量 + 不确定度）明确不支持**，写入图例 `disclosures`，不用三角编码冒充。

## 改动

- 新领域模块 `app/services/bivariate_reliability_authoring/`（contract / snapshot / stats / classify / reliability / legend / expression / cache / service / errors）
- 3 个 typed 工具 + 3 条 REST（`require_owned_session`）加性接线
- 服务端图例真实绘制 `render_legend_box_authored`，G34 排版透传 canonical 结构
- 前端作者面板 + contracts/api + 双语 i18n；画布 / SVG / PDF 图例同源，移除旧的「矩阵已省略」假降级

## 证据（同版本实跑，未等线上 CI）

| 项 | 结果 |
|---|---|
| `pytest tests/unit/bivariate_reliability` | 73 passed（含手算 oracle） |
| 后端回归（thematic_authoring / G34 / v4 cartography / converter） | 138 passed, 2 skipped |
| `test_g41_publication_readback.py` | 6 passed（SVG/PNG/PDF 独立回读、长名全文、跨 DPI、混合图层完整性） |
| `PI_JOURNEY_E2E=1` 真实 Pi 旅程 | 1 passed，18/18 gates，pin `v1.5.2-0-gb4afdddf4` |
| 前端 G41 专项 + 回归 | 14 文件 140 passed |
| `tsc --noEmit` | 新代码 0 错误（仅 2 处既有 `cartography-result-card.tsx`） |

Pi 旅程只 stub LLM 响应；RPC entry、`tool_search` deferred 激活、`ToolDispatchService`、`UpsertLayerIntent` 写入、派生 ref 冻结、受权下载回读均为生产代码。

## 未覆盖（如实）

- 真实浏览器 E2E（环境无浏览器可执行文件）与深色底图 alpha 像素对比：**未跑**，不以外观推断代替。
- 选择句柄（>50 对象 / MVT 局部 handle）：属 G24/G38 边界，非 `ref:` 源明确拒绝。
- 模板 / 多 frame / atlas 组合导出：**未测**。

## 回滚

单笔 `UpsertLayerIntent`，撤销即 `mode="revert"`，整笔历史用 MapSpec undo。无 schema 迁移、无新表、无后台任务。

## 复审

独立 reviewer 两轮：第 1 轮 6 项 P0/P1（flip_rows 图面与箭头不一致、revert 恢复到 base 而非上一来源、可靠性边界规则未生效、`allow_noncentered` 死字段、长名静默截断、clamp 下越界桶失效）已全部修复并复验；第 2 轮对合流 diff 复审。详见 `planning/G41-BIVARIATE-RELIABILITY-AUTHORING/REVIEW.md`。
