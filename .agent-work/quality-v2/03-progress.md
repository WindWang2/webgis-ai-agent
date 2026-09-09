# 03-progress — 执行日志

## 2026-09-09（终态）

全部 12 waves 落地并提交；findings 241→0（棘轮锁零）；dispatch 151；闸 95% 全过。

- W1 `89d5bb7d` manifest v2 + behavioral + ratchet/waiver
- W2 `4a009b13` descriptor 收敛 152→92；W2b `f576798d` capability 词表扩展（16 planned 能力）→ 0
- W3 `c798a3a1` 30 工具真实 dispatch 行为测试（发现并修复 quadrat_analysis 生产 bug `43fab1ce`）
- W4 `77312b92` 20 算法 conformance oracle + 23 honest variants
- W5 `f473ab24` SEC-KG-01/02 收口
- W6 `7cb6262e` WS/SSE realtime 契约快照
- W7 `956af3bb` property/fuzz harness + 语料闸
- W8 `a427e718` SQLite↔PG 差异化存储
- W9 `1a7a67ef` chaos STORAGE + cancellation 抬升
- W10 `0d71cf50` runner V2 + 顺序轮换
- W11 `04362822` perf 预算迁移
- W12 `73e62d16` 生成物账本 + staleness 前置检查 + ADR-0118
- 棘轮锁零 `606e18ce`；证据面再生成修复 `686c`（gen 必须在 git add 之后）

### Phase E 两轮 review

- Round 1（架构/正确性）：1 MAJOR（SEC-KG-01 AST 旁路：from X import 模块名 + 裸名引用）→ 已修（ImportFrom.names + Name 匹配 + 动态 import 字符串探针，自验 5 形态全抓）；8 MINOR + 8 NIT
- Round 2（安全/性能/可维护性）：同 MAJOR（交叉确认）；4 MINOR + 2 NIT
- 全部 MAJOR/MINOR/NIT 已修复（详见 05-review-findings.md）
