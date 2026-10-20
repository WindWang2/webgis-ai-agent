# H02 WORKLOG

## Wave 9 — 独立评审 + 清偿 + 双验证 + PR（2026-10-20）

- 独立 reviewer（FIX-FIRST）：P1×2（manifest 属主校验 / typecheck mapIdle）+ P2×4 + P3×5 → commit c99ec307 全清偿（REVIEW.md）。
- Oracle 双验证：cartography lane 连续两遍 2339 passed / 19 skipped / 3 xfailed（一致）。
- 终态 fetch：origin/master 仍 = 0415ad8d（无漂移）；`git diff --check` 干净。
- 全部 6 commits：bd66b1b0 → 2569a7c5 → 957ec577 → d9e383f5 → bfef67c8 → c99ec307。
- PR body 按 docs/dev/pr-body-template.md 存档 docs/dev/h02-pr-body.md 并创建 PR。

## Wave 8 — perf + ADR（2026-10-20）

- perf 预算 6 项（-m perf）：100/1000 层 3.5/27ms、100 次逐字节一致、29 模板 O(1)；PERF.md。
- ADR-0228（manifest v1 全景）。

## Wave 4 — 版面截断契约（2026-10-20）

- title/subtitle 120/160 确定性截断 + title_truncated 双链披露；golden 不漂移。

## Wave 6+7 — 回归契约 + 场景工厂（2026-10-20）

- #1713×2 / #1726 / #1747 xfail 契约（不动他属文件）。
- 33 场景跨语言工厂（Py 56 + TS 25 测试）。

## Wave 5 — manifest + nodata + ref 水合（2026-10-20）

- product_manifest.py（六维验证 + bounded 投影）挂载导出路由 sidecar。
- nodata guard 四下降点同语义；SVG ref 水合 + layer_ref_unavailable。

## Wave 3 — user-wins pin（2026-10-20）

- 被锁组件 placement 在 agent 再制图保持。

## Wave 2 — 后端一致性修复（2026-10-20）

- G1 指纹 v1.6 可见性；G2 representation 报告；G3 组合身份落 MapSpec；G4 required_slots 真契约；G5 export 槽位裁决；G6 conformance 产品面。

## Wave 1 — 缺口固化（2026-10-20）

- 基线（pristine master）：`pytest -m cartography` → 2259 passed / 19 skipped / 0 failed（172.62s）。
- G1–G6 failing tests（10 红 + 2 guard）。

## Wave 0 — 侦察（2026-10-20）

- Phase 0 全链调查（3 侦察 agent + PR/Issue triage）→ RECON.md；worktree + venv 3.12（CI 同款约束安装）。


