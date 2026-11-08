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
| 7 | M9a：ADR-0288（领号+watermark 288）+ docs/features/evidence-map-briefs.md + TEST_MATRIX + FEATURE_ACCEPTANCE | 文档落盘 | 通过 | commit → M8 独立冷读 Review |
