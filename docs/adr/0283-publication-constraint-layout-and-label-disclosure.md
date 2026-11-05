# ADR 0283 — Publication Constraint Layout & Label Disclosure（出版约束版面与标注披露）

日期：2026-11-06
状态：Proposed（随 zcode/goal-cartographic-publication-vnext-20261105 交付）
前置：ADR-0084（像素布局引擎）、ADR-0238 D5/D8（Layout Solver V2/V3、结构化 golden）、ADR-0118 D1（降级词表后端权威）、ADR-0120 W6（导出标签孪生）、ADR-0156（V4 冲突自愈）、ADR-0157（WYSIWYG 范围/高DPI/出版档）、ADR-0214（组件 ABI）

## 背景

出版级用例（A4/A3 图册页、研究图件、双语长标题）暴露四类确定性缺口：

1. **约束不可序列化**：V2–V4 求解器的容量/宽度/折叠参数是调用方入参，
   MapSpec/组件描述符上没有可持久化、可对拍的约束投影——同 spec 不同
   调用点可能解出不同版面；safe_area（live 几何层 `layout_geometry.SafeArea`）
   未进入 PublicationLayout，导出链没有"版心"概念。
2. **关键说明会被截断**：layout-description 孪生对 title/subtitle 施加
   固定码点截断（120/160），长中文标题、方法论注记以"减文字"掩盖溢出
   ——与任务书 D1"关键说明不能截断、不以减少文字内容掩盖溢出"冲突。
3. **标注×图元冲突无治理**：导出标签孪生只解 label-vs-label（网格+AABB），
   标注可直接压在点符号/线图元上；显式指定标注字段缺失时缺一条确定性
   失败码路径。
4. **版式/标签维度无质量回路词表**：semantic_checks 覆盖图例/分类/色板，
   但"碰撞未解决/关键件被折叠/标注抑制超阈值"没有确定性发现码与
   fix_hint 修复路由。

## 决策

### D1 — LayoutConstraintSpec v1：可序列化约束投影（新模块，不改 MapSpec schema）

- 新模块 `app/lib/cartography/layout_constraint_spec.py`：
  - `ComponentLayoutConstraint`：`must_fit`（溢出时宁折叠/转栏不消失）、
    `no_truncate`（关键文本 wrap 不 clip）、`min_width_units`/`max_width_units`
    （1–4，与 solver 列单位一致）、`overflow_policy ∈ keep|collapse|suppress`
    （closed vocab；keep 保留+披露、collapse 折叠进溢出面板、suppress 仅
    optional 且必须带披露码；`must_fit × suppress` 互斥闭口拒绝）。
  - `LayoutConstraintDocument`（`spec_version: "layout_constraint_spec.v1"`）：
    page 级 `safe_area_mm`（上右下左，出版版心）、`key_component_ids`。
  - `derive_layout_constraints(components, page_profile)`：从组件证据推导
    缺省约束 —— **缺省 = V4 行为逐字段一致**（R9 P1-1 清偿语义）：
    折叠准入只显式化 `COLLAPSIBLE_TYPES`（其余 keep）；`must_fit` 仅当
    组件自身 `optional=False`（V5 required 化为 no-op）；单例/关键说明
    只进 `key_component_ids` 披露面（落入 suppressed 时发
    `key_component_suppressed` 发现 —— 诚实披露而非静默强制）。
    更强的保证（关键组件永不消失）由调用方**显式**声明（显式 > 缺省）。
- 求解入口 `solve_layout_v5(participants, constraint_spec=...)`：V4 之上
  消费约束（宽度钳制/下限提升/折叠准入/required 化），输出 V4 解 +
  `constraint_findings`（closed vocab：`max_width_clamped`/
  `overflow_suppressed`/`key_component_suppressed`/`constraint_unmatched`）。
- **不持久化求解结果**（ADR-0084 既定：布局是派生投影）；约束文档是
  **输入**的序列化，不是第二真相。MapSpec schema 接线（若后续需要
  `layout.constraints` 字段）留作独立 additive commit，本 ADR 不绑定。

### D2 — 关键文本 wrap-not-clip（PublicationText 增量）

- `PublicationText` 增量字段 `wrapped?: boolean` 与 `lines?: string[]`
  （no_truncate 文本超上限时按 CJK/拉丁宽度口径折行；孪生双侧同算法，
  复用 label_typography 宽度口径）。截断路径（clipText）保留用于
  非 key 文本；`title_truncated` 披露语义不变，新增
  `key_text_wrapped`（info 级）披露。

### D3 — 标注×图元障碍与字段缺失守卫（导出孪生）

- `solve_export_labels` 增量入参 `obstacles: List[Box]`（有界，≤ 上限
  200：点图元 AABB、可扩展线采样点）；候选位与障碍相交按碰撞同序处理
  —— 全候选均撞 → 抑制 + 既有 `label_collision_relaxed` 披露（detail
  携 obstacles 计数）。Python/TS 同 move，golden corpus 扩
  `label_vs_glyph` fixtures。
- 显式 `layer.label.field` 不在源字段集 → 确定性失败码
  `label_field_missing`（warning，semantic_checks 新检查；导出孪生不渲染
  该层标注、不伪造），与"缺字段不得擅自生成标注"对齐。

### D4 — 版式/标签发现词表与修复路由（semantic_checks 家族新模块）

- 新模块 `app/lib/cartography/semantic_checks_layout.py`：
  `LAYOUT_COLLISION_UNRESOLVED` / `KEY_COMPONENT_SUPPRESSED` /
  `OVERFLOW_SUPPRESSED` / `SAFE_AREA_VIOLATION` /
  `LABEL_SUPPRESSED_RATIO`（抑制比阈值梯 ≥0.10 warning / ≥0.25 error，
  两档封闭；证据带比率与计数）。
  证据类 deterministic，修复性标注 presentation-only。
  `label_field_missing` 不在本词表 —— 它由 D3 编译器孪生
  （label_field_guard.py + mapspec-to-svg 双端）在导出期发射。
- fix_hint 路由到**既有执行器词表**：版式类 → component_composer
  REPAIR_ACTIONS（change_anchor/collapse_to_overflow）；标注类 →
  selfheal_actions 已注册动作 adjust_labels。不新造动作 id、不建第二
  执行器；presentation commit 构造的执行接线归 quality_loop 消费面
  （该文件为并行 PR 占用，本 PR 只产出建议）。上限/幂等/user-wins
  复用既有闸（MAX_REPAIR_ITERATIONS=2、LOCK_REASON_USER_WINS）。

### D5 — 出版用例基线（六族 fixture + 结构 golden）

- `tests/cartography/publication_cases/`：dot_density_district /
  choropleth_graduated / continuous_raster_colorbar / long_name_labels /
  comparison_two_views / research_figure_inset_chart 六族 × zh|en ×
  16:9|A4|A3。生成器 + 结构 golden（layout 求解 + PublicationLayout 投影
  digest）+ 降级文案断言。像素/浏览器证据仅在 Chromium 可行时执行并
  如实标注（NOT_RUN 纪律）。

## 非目标

- 不改 V2–V4 求解器行为与既有 golden；不动 quality_loop.py；
- 不做通用 LP/约束求解（ADR-0084 已拒绝）；不引入像素级 screenshot
  golden 为阻断门（ADR-0238 D8 既定：结构化 golden 为准）；
- 不修 #1910（WeasyPrint data: 字体拦截，他人认领）——本线只做前端
  pdf-font 语义一致性验收；
- 不改 MapSpec schema 语义/版本（如确需接线另行最小 additive 审查）。

## 后果与风险

- 求解器新增 V5 入口为纯函数增量；约束缺省 = V4 行为（零回归面）；
- PublicationText 增量字段保持 JSON 兼容（旧消费方忽略未知字段）；
  双侧 golden 需同步再生（受控 diff 审）；
- obstacles 有界上限防止 O(n·m) 爆炸；全部新词表登记
  render_diagnostics.py（后端权威）并镜像 export-chrome.ts 注释。
