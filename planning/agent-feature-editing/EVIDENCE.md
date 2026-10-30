# EVIDENCE（随里程碑填充）

## M0/M1
- worktree: ../webgis-worktrees/gis-agent-edit @ 4b3629c90（origin/master）
- 网络：github.com TLS eof ×5+（fetch/gh 皆失败）——PR_OVERLAP_MATRIX.md
- 三路 subagent 勘察报告归档于会话；关键 file:line 见 BASELINE.md

## M2–M8 证据（2026-10-29）

- 后端测试：tests/unit/feature_edit/ 55 全绿（core 27 / routes 12 / tools 12 / concurrency 3 / e2e 1——e2e 覆盖真实 IngestPipeline→IR 多选→工具预览→CONFIRMATION_REQUIRED→owner approve→隔离不变量→盖章→situation 观察→undo→历史→幂等重放全链）。
- E2E 中抓出并修复 P1×1：journal.apply_deltas_to_features 对同要素多 delta（先字段后几何）恒由原始要素重拷，后 delta 抹掉前序效果（commit 6e6243a65 前置 d60c73546）。
- 并发/锁：turn 锁透传不死锁 + 后台盖章收敛测试绿（test_concurrency.py）。
- 生成物：openapi 快照 / endpoint-scope-matrix / api-docs / staleness 账本两次再生（ref_id 改名后二次再生），check_generated_staleness 全绿。
- 前端：tsc --noEmit 绿；eslint 新文件零错误（硬编码 CJK 清零，i18n zh-CN/en-US 双语资源）；vitest 新增 11 绿 + 邻域 410 绿（全量见 M9 记录）。
- ADR-0277 落盘 + watermark 277 + ADR 编号契约测试 3 绿。
- 环境注记：Playwright/真实浏览器在本机未配置——地图渲染消费路径以 mapspec 盖章 + content_revision 管线断言代替（诚实降级，非跳过宣称）；前端交互面由组件测试覆盖。

## M9 证据（2026-10-30）

- 独立评审三轮：R1（P1×3/P2×10/P3×10 清偿 + 回归钉 9 条）→ R2（P0=0/P1=0/P2×3/P3×9 清偿）→ R3（五点验证 ✅ PR-ready）。全过程 INDEPENDENT_REVIEW.md。
- 后端全量：tests/ 除 1 个既有环境基线红（mutation_parity golden 的 pydantic 2.13 错误消息 URL 漂移——基线 4b3629c9 干净检出同样红，与本分支无关）全绿。
- 前端全量：vitest 494 文件 / 4674 测试绿 + tsc --noEmit 绿（一次并行跑观察到 1 例偶发失败，复跑两次全绿——flaky 披露）。
- 生成物：openapi/endpoint-matrix/api-docs/staleness 账本/situation schema/ADR 编号契约 全绿。
- 二次去重（网络恢复后）：origin/master 无前进（4b3629c90）；唯一 open PR #1868 与本分支实质能力无重叠（文件交集仅 3 个琐碎生成物/单行注册点）。
