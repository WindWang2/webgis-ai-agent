# ADR-0074: Cartographic QA —— 布局碰撞检查与修复回路总预算

日期: 2026-08-27
状态: accepted

## 背景

两个 QA 缺口（审计 CA-P1-4 / FE-P2-3）：

1. **布局 QA 是空壳**：`layout_constraints.detect_collisions` 只被自己的单测
   调用，不在任何生产路径；`VISUAL_OVERLAP` 恒 not_evaluated；前端靠 36px
   底部堆叠启发式防撞（自认补丁）。浮动面板（chart_panel/statistics_panel）
   上线后重叠既无检测也无上报。
2. **修复回路理论无界**：客户端唯一数量约束是 16 条 action_id 去重环——环淘汰
   后旧修复可重新派发；若修复每轮都改变观测（A↔B 震荡 / 后端持续换新
   action_id），POST×repair×reconcile×观测循环无上限（后端 ≤2 轮自律不能
   约束多 fingerprint 场景）。

## 决策

1. **`LAYOUT_COLLISION` 语义检查**（desired-state 证据，接入
   `evaluate_cartography_semantics` 规则 DSL）：zone 容量超限、exclusive zone
   重复占用、singleton 组件重复（title/north_arrow/scale_bar/attribution）、
   组件 layerId 悬空、floating 矩形重叠（归一化 x/y/w/h 相交）。
   - floating 组件豁免 zone 容量（位置归用户手势所有）；floating 间重叠报
     warning 且**刻意不 auto_safe**（修复会挪动用户手动摆放的位置——user wins）。
2. **客户端修复总预算** `MAX_TOTAL_SESSION_REPAIRS = 8`：耗尽后停止派发
   （观测照常上报），devOnly 告警一次，会话切换重置。

## 后果

- 布局质量从"渲染出来才知道撞没撞"变为 desired-state 可评：地图产品在落
  渲染前即可发现 6 个统计卡挤在同一角、图例绑定已删层等问题。
- 修复回路有了确定性的终止上界（8 次派发/会话），震荡场景最多付 8 轮成本
  后静默；后端修复建议照常下发，只是不再被无限执行。
- 像素级证据（真实 label 重叠）仍属 `VISUAL_OVERLAP` 的 visual 证据类，
  保留 not_evaluated 语义——desired-state 检查不冒充渲染证据。
