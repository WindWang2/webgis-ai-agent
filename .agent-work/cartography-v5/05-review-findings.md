# Cartography V5 — Review Findings & Fixes

## Round 1（架构/正确性/回归）— 结论：需修复后合并 → 修复完成
- BLOCKER-1 多帧画布别名（所有帧=最后一帧，静默错帧）→ r2 `88acf963` 修复（逐帧位图快照 + 共享 live canvas 回归测试）；r1 复核确认。
- MAJOR-1 词表死码 ×3（label_suppressed_too_long 零发射器 / small_multiple_panel_skipped 容器语义 / terrain_3d_scale_caveat 零发射器）→ `fcc26295` 清零（词表 19→18、ComposeFramesOptions.skippedCode、is3D 发射），18/18 码有发射器。
- MAJOR-2 atlas 截断模板病句 → `fcc26295`。
- MAJOR-3 frames 无生产入口 → `68ba610c`：export_thematic_map 增加 frames 参数（≤50、fail-loud 校验）→ export_map 命令 → ExportRequest；5 项契约测试。
- MAJOR-4 跨变异 legend user-wins 回翻 → `68ba610c`：抑制条件升级为 merge 后状态不变式（durable 决策），ADR D2 同步 + 回归测试。
- MINOR（记录）：report 链线程不可强杀（同预算协作超时自终止，部分产物已补 degraded 标记 68ba610c）；被拒诊断无服务端日志落点；sidecar 404/403 探测序与 download 一致；图例派生四处重复（测试锁定，收敛列为 follow-up）；MAX_SVG_LABEL_CHARS 双侧字面量（parity corpus 锁定）；comparison registry 双实例并存边缘。

## Round 2（性能/安全/UX/可维护性）— 结论：修复后可合并 → 修复完成
- BLOCKER B-1 多帧错帧 → `88acf963`（同 R1 BLOCKER-1）。
- CRITICAL C-1 诊断 message 无界插值/无 ID 上界 → `88acf963`（先截断后插值 + MAX_ID_CHARS=200 + 端到端 200KB 测试）。
- CRITICAL C-2 upload 契约测试 TDZ 死测试 → `88acf963`（vi.hoisted 重构，真实运行）。
- MAJOR M-1 legend_entries_truncated 披露词与事实相反 → `88acf963`（message 改为 live 截 8/导出全集 + categorical nodata 计数修正 + catalog 重生成）。
- MAJOR M-2 降级清单 >8 静默截断 + atlas 成功消息不注明跳帧 → `88acf963`（总数披露 + frameStat）。
- MAJOR M-3 grid 超浏览器画布上限静默白图 → `9c90ab4e`（16384px 守卫 + 诚实抛错 + 测试）。
- 安全结论：sidecar 端点无 IDOR/穿越（basename + fail-closed owner）；正则无 ReDoS；SVG 服务端 sanitize 时序在位；TTL 清扫覆盖 sidecar。
- perf 结论：20k 编译 0.112s（预算 15s）；无 O(n²) 实证；compose/chrome 模型无重复构建。
- MINOR/设计项（记录为 follow-up）：50 帧 × dpi300 内存峰值 ~1.9GB（无帧级降采样）；前端无跨帧总 deadline（最坏 25min）；图例条目派生单源收敛；swipe 导出无 before/after 标签（与 live parity 一致）；单特征内部超时不可中断。

## 评审 commit
- `88acf963` fix(review-r2)
- `9c90ab4e` fix(review-r2)
- `fcc26295` fix(review-r1)
- `68ba610c` fix(review)（主 agent 落地的 R1 剩余 MAJOR + MINOR 收敛）
