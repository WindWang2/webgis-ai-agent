# DECISIONS — goal-04-carto-pub（R2 定案 2026-11-06；详 ADR-0283）

| # | 决策 | 状态 | 理由/边界 |
|---|---|---|---|
| D1 | 约束 DSL = 独立可序列化模块 `layout_constraint_spec.v1`（新文件），从描述符派生缺省；不改 mapspec_schema.py | **定案** | 规避跨域红线；缺省=V4 行为零回归 |
| D2 | solver 扩展走新入口 `solve_layout_v5`，不修改 V2–V4 行为与 golden | **定案** | 回归锁定 |
| D3 | D4 发现词表落 `semantic_checks_layout.py` 新模块 + selfheal_actions 路由，不写 quality_loop.py | **定案** | #1965 占用 |
| D4 | 双实现扩展一律共享 golden corpus 对拍（3 位小数口径） | **定案** | 既有跨树模式 |
| D5 | 六类出版用例生成器 + 结构 golden 优先；像素证据仅 Chromium 可行时执行 + 如实 NOT_RUN | **定案** | 本机 playwright 1.64.1+chromium 已确认可用 |
| D6 | 关键文本 wrap-not-clip：PublicationText 增量 `wrapped/lines`，key_text_wrapped(info) 披露；title_truncated 语义保留给非 key 文本 | **定案** | 验证：现状 clipText 固定 120/160 码点截断（layout-description.ts:60） |
| D7 | 缺瓦片披露：有界聚合（计数+首 N 样本+来源层） | **定案** | bounded evidence 先例 |
| D8 | ADR-0283 已起草（docs/adr/0283-publication-constraint-layout-and-label-disclosure.md） | **定案** | 下一编号 0283（0282 属开放 PR #1972） |

## R1/R2 期间核验修正（勘察误判清偿）

1. ~~D2"导出链标签抑制披露未闭环"~~ → **已闭环**：label_collision_relaxed/label_budget_exceeded 双侧发射且登记词表（render_diagnostics.py:203/207；mapspec-to-svg.ts:1082；mapspec_to_svg.py:1900）。export-chrome 注记的 label_suppressed_too_long 是另一枚死码，维持移除。
2. 确认真缺口：标注×图元（label_collision.py 仅 label-vs-label）；safe-area/版心不在 PublicationLayout（双侧 grep 证实仅 bleed）；约束不可序列化（V4 参数为入参）；standards_cases 无 dot_density 族。
3. 待 test-first 验证（写负例测试定夺）：显式 label.field 缺失的确定性失败码是否已有等价物（semantic_checks.py:2152 附近有 field-absent 检查，语义待核对）。
