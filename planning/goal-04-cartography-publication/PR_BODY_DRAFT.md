# feat(carto): 出版级制图引擎 vNext —— 约束版面 / 标注治理 / 多格式一致导出（ADR-0283）

## 范围（goal-04-carto-pub，基准 origin/master 40cda057a）

在既有 MapSpec/组件库/L1–L5 校验之上补齐出版级能力的**增量**（不建第二制图引擎）：

1. **D1 约束版面**：`layout_constraint_spec.v1` 可序列化约束投影（must_fit /
   no_truncate / min-max width_units / overflow closed vocab / safe_area_mm 声明）
   + `solve_layout_v5`（缺省输入 = V4 逐字段一致，零回归面）；关键文本
   wrap-not-clip 全链贯通（layout-description 孪生 em 宽度折行 → canvas
   逐行绘制 + 副标题让位 → SVG 矢量链逐行）——长中文标题不再以减字掩盖溢出。
2. **D2 标注治理**：导出孪生 obstacles（label×图元障碍，双侧同 move + 共享
   golden fixtures）；显式标注字段缺失守卫 `label_field_missing`（不渲染不伪造）；
   跨入口图例口径一致性测试族。
3. **D3 导出一致性**：缺瓦片有界披露（per-source count + 首 5 样本 z/x/y，
   `basemap_tiles_missing`）；导出冻结边界测试锁定（组合后突变不渗入）；
   `decidePdfTextLayerDisclosure` 纯函数（#1910 非重叠前端验收锚点 +
   事实错误归因披露修正）；safeAreaMm 版心声明进入 PublicationLayout 双侧。
4. **D4 质量回路**：`semantic_checks_layout`（5 码 closed 词表 + 抑制比阈值梯
   0.10/0.25 + fail-closed not_evaluated + fix_hint 只路由已注册动作）；
   selfheal 注册 relayout_change_anchor / relayout_collapse_overflow
   （presentation-only，复用既有修复链，quality_loop.py 未触碰）。
5. **D5 出版基线**：六类用例 × zh|en × 16:9|A4|A3 = 36 条结构 golden
   （点密度+区级统计 / 分级设色 / 连续色条 / 长名称标注 / 双图对比 /
   研究图件 inset+图表），语义不变量：key 组件零抑制、长标题双语 wrap
   内容完整、发现码 ⊆ closed 集、受控再生一致。

## 与既有 PR 的区别

- 不重做 #1870（设计候选）/ #1871（图册 durable job）/ #1875（linked views
  与 label/export 保真）/ #1880（多尺度）——它们的资产清单见
  `planning/goal-04-cartography-publication/PR_OVERLAP_MATRIX.md` B 表。
- 不抢修 #1910（WeasyPrint data: 字体拦截，publication_export.py 他人认领）：
  本 PR 只交付前端不变式与验收锚点。
- 避让开放 PR #1965 的 4 个直接交集文件（quality_loop.py 等，全程只读）与
  #1972 的 tests/acceptance 面（零交集）。

## 实现与验证证据

（R10 时以真实命令输出填充：Python 定向/全 lane、前端定向/全量、tsc/eslint/ruff）

## 未运行（诚实披露）

- 全栈浏览器 E2E（未启动 Next+FastAPI 全栈；以结构 golden + jsdom harness 替代）
- `pnpm build` 生产构建（typecheck 已覆盖类型面）
- #1910 后端回执一致性核对（blocked on 他人修复）

## 技术债 / 后续

- MapSpec `layout.constraints` 字段接线（如需 spec 级持久化，另行最小 additive 审查）
- line/polygon 层的障碍接线（本轮接 circle 层）
- 像素级浏览器回归（Playwright 可用，建议后续以真实栈单独建线）

## 回退开关

全部为 additive 模块/字段/测试；未变更任何既有求解器行为与 golden。回退 =
revert 本 PR（无数据迁移、无 flag 依赖）。

## 影响面

app/lib/cartography/（新模块 ×2 + layout_solver/layout_description/
render_diagnostics/label_collision/selfheal_actions 扩展）、
frontend/lib/export|map-kit|mapspec-runtime（wrap/障碍/披露）、
tests/cartography/（新测试 ×N + golden corpus 增量）。
