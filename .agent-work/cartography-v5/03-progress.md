# Cartography V5 — Progress Log

## Waves（全部落地，同一分支小步提交）

| Wave | Commit(s) | 内容 |
|---|---|---|
| W1 | 069a4b38 | render_diagnostics 权威词表（19 码→review 后 18 码）+ catalog schemaVersion 5 + 前端子集 parity 锁 |
| W2 | 82073ae1 | legend user-wins P0 修复 + 字段级 merge + popitem + controls 类型（review 后升级跨变异不变式 68ba610c） |
| W3 | 5283e632 | label 文本适配契约 fit/wrap + label_too_long |
| W4 | 5002baa7 | 孪生 SVG：可见性 + 截断接线 + thresholds 执行 + SvgCompilation |
| W5 | 078bcd82 | 真矢量 SVG 导出（孤儿编译器/整饰层复活）+ 长文本截断 + 回退披露 |
| W6 | 3302b37b | PDF 单标题源 + CJK 栅格化回退 + 诚实话术 |
| W7 | c3dc9cf6 + 5fe143b1 | 导出真相同源 + 图例 parity + 死码发射器 + nodata + sidecar 端点 + 前端透传 |
| W8 | 3d58bafe | swipe 对比导出显式组合/披露 |
| W9 | 41da22e7 | 多帧运行时（atlas pages / grid）+ cartogram 诚实降级 |
| W10 | 4a3867a3 | describeRenderScene 语义 oracle + 跨孪生长文本 parity corpus |
| W11 | 6d6a922f | 孪生编译结构性 perf 预算（20k 特征 differential） |
| W12 | b057a215 + 882666e9 | ADR-0118 + 模型目录 V5 状态（生成源字段） + CHANGELOG |
| 整理 | 517f4f65 / 0198562e | ruff/tsc strict 收敛 |

## 验证记录（Phase D）
- 后端：tests/unit + tests/cartography 全量 9278 passed / 105 skipped（修复 catalog 生成闸漂移后复测 cartography lane 894 passed）。
- 前端：全量 vitest 2654 tests（首次后台运行与 review 编辑竞态出现 1 个文件级失败；稳定树复测见 05-review-findings 后记录）；tsc 双 tsconfig 通过；eslint repo-wide 通过；ruff repo-wide 通过。
- parity：test_compiler_parity 6 绿（孪生 fixture 未动）；跨孪生长文本 parity 两侧 6 绿。

## Review Rounds（Phase E）—— 见 05-review-findings.md
