# G11-EVIDENCE-MAP-BRIEFS — GOAL Loop Ledger

> 本任务级账本（替代全局 .goal-loop-ledger.md，避免并行任务冲突）。
> 格式：`第N轮 | 改动 | 验证结果 | 通过/未通过 | 下一步`

| 轮 | 改动 | 验证结果 | 通过否 | 下一步 |
|---|---|---|---|---|
| 1 | 启态核查（git/PR/issue/worktree）+ 双子代理 M0 审计 + BASELINE/OWNERSHIP/PLAN + venv 搭建启动 | master=8eb8aa1 与快照一致；G01-G07 全合并；仅 #1979 dependabot open；G09/G10 在途未合并（避让）；审计结论：底座在场、待开发=块级证据绑定+简报域胶合层 | 进行中 | venv 装完→跑 storymap 既有测试基线→M2 数据模型 |
| 2 | M1-M2：venv 就绪；storymap 基线 106 passed；MapBriefSpec + 迁移 0104（领号）；alembic upgrade/downgrade 实跑通过 | 测试/迁移全绿；test_alembic percent-encoded 1 例为环境预存红（移除迁移后复现依旧） | 通过 | commit 0eb4cef91 → M3 编译器 |
| 3 | M3：brief_compiler + factcheck + 32 单测（发现并修复：派生类事实证明通道裁决 bug；roundtrip 期望错写修正） | 32 passed | 通过 | commit 97b75621c → M4-M6 |
| 4 | M4-M6：store/preflight/release/export/分享 + 路由 16 端点 + 14 集成测试（发现并修复：无消息无 trace 会话不能起草 → 产物降级路径） | 14 passed + 回归 99 passed | 通过 | commit 53f64c3f0 → M7 工具 |
| 5 | M7：六工具 + Pi 适配器 + 13 安全负例（安全测试抓到 release_meta 泄漏 → 白名单投影修复；data_mutations 词表对齐） | 5+13 passed；51 回归 | 通过 | commit 35f4fe6ec → 前端 |
| 6 | 前端：client/面板/story-view/i18n(82 keys)/j13 双模式（修 useT 解构、点号 key 命名、map.set 扫描误报、j13 断言×3、real 栈 CORS 根因） | typecheck 绿；i18n+story 64 passed；j13 mock+real 1+1 passed；j4 mock+real 回归 2 passed | 通过 | commit e7b9e8f77 → M9 文档 |
| 7 | M9a：ADR-0288（领号+watermark 288）+ docs/features/evidence-map-briefs.md + TEST_MATRIX + FEATURE_ACCEPTANCE | 文档落盘 | 通过 | commit 80d9014dc → M8 独立冷读 Review |
| 8 | M8 审查（子代理 C，审查 SHA 80d9014dc）：P1×2（幂等键含墙钟——审查首跑实测复现 flaky；SVG 净化 8 载荷绕过）+ P2×4 + P3×7 → 全部清偿（含 heredoc 转义两处自伤 bug 修正） | 修复后 67 passed；幂等跨秒 ×5 稳定；审查者探针复跑 SVG/传播清零 | 通过 | commit 551902494 → 复审 |
| 9 | 复审（同审查者新 HEAD 551902494 独立对抗复现 27+ 探针/API 级并发）：结论"可提交 PR"；残留 N1(P2 一行正则)+N2/N4(P3) 即时清偿，N3 记跟进 | 67 passed；N1/N2 探针清零；良性与 CAS 行为保真 | 通过 | commit 6239f0551 → M9b 终检+PR |
| 10 | M9b 终检：master 无漂移（8eb8aa1）；关键验收 134 tests 连跑两遍全绿；前端 typecheck+i18n/story 64+j13 mock 复跑；push + PR #2021 创建 + 审查证据链评论 | PR #2021 在场（未合并） | 通过 | 完结 |
| 完结 | G11 证据化地图简报：BRF-O01..O12 全覆盖（证据见 TEST_MATRIX/FEATURE_ACCEPTANCE）；独立 Review→Fix→Re-Review 双轮（复审结论"可提交 PR"）；9 commits | PR #2021（未 merge） | Oracle 满足：模型/编译器/编辑/预检/冻结/导出/分享/工具/UI/e2e 双模式/安全负例/文档全量在场且实跑通过 | 2026-11-09 |
