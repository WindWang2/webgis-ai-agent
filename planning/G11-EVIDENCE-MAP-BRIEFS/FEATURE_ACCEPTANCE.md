# G11 FEATURE_ACCEPTANCE —— 功能验收证据

## 交付物清单（commit）

| commit | 内容 |
|---|---|
| 0eb4cef91 | M2：MapBriefSpec 模型 + 迁移 0104 + 领号 + planning M0 审计 |
| 97b75621c | M3：编译器 + 确定性事实核验 + 32 单测 |
| 53f64c3f0 | M4-M6：store/preflight/release/export/分享 + 16 端点 + 14 集成测试 |
| 35f4fe6ec | M7：六工具 + Pi 适配器链 + 13 安全负例（含 release_meta 泄漏修复） |
| e7b9e8f77 | 前端：brief client + 面板 + story-view 挂载 + i18n + j13 双模式 e2e |
| 80d9014dc | M9a：ADR-0288 + docs/features + TEST_MATRIX + FEATURE_ACCEPTANCE |
| 551902494 | M8：独立审查清偿（P1×2+P2×4+P3×5，含审查回归负例） |
| 6239f0551 | 复审残留清偿（N1/N2/N4；N3 记跟进） |

## 实际验证命令与结果（全部本机实跑）

```
后端（.venv，Python 3.14）：
  pytest tests/unit/test_mapbrief_core.py            → 32 passed
  pytest tests/unit/test_mapbrief_api.py             → 14 passed
  pytest tests/unit/test_mapbrief_security.py        → 13 passed
  pytest tests/unit/test_brief_tools.py              → 5 passed
  pytest tests/unit/test_storymap_orchestrator.py …  → 106 passed（基线回归）
  pytest tests/unit/test_skill_induction.py …        → 82 passed（能力面回归）
  alembic upgrade head → downgrade 0103 → upgrade（sqlite 实跑，表/索引断言）
  scripts/alloc_migration.py --check                 → 74 迁移通过

前端（pnpm）：
  pnpm typecheck（双 tsconfig）                      → 0 error
  vitest test/i18n app/story                         → 64 passed
  vitest 全量                                         → 5024 passed / 3 预存红（master 债，TEST_MATRIX 附录）
  playwright j13 --grep mock                         → 1 passed（14.5s）
  playwright j13 --grep real（真实后端 + LLM stub）   → 1 passed（18.9s）
  playwright j4  --grep mock + real                  → 2 passed（回归）

浏览器真实后端旅程（j13 real，关键断言）：
  - 真登录（Account 表单）→ 真实 chat 种子（真实空间分析产物落地台账）
  - 草稿 rev 1（面板显示真实章节/真产物来源/MapSpec 指纹）
  - 编辑正文 → rev 2（乐观 revision）；锁定
  - 预检报告可见 → 冻结 v1 → 下载事件：HTML 含 brief-bundle、零外链正则通过
  - 分享 → 外部者无凭证 GET 会话/简报端点 401/403/404 → 撤销
```

## 工具成功/任务成功/地图显示/导出/发布 分开披露

- 工具成功：brief_create_draft 等六工具 dispatch 返回 typed 回执（5 passed）
- 任务成功：草稿创建/编辑/预检/冻结持久化行存在（14 passed 断言 DB 状态）
- 地图显示：j13 real 中 Story 页 MapPanel 挂载（工作台回归 j4 双模式通过）
- 文件导出：j13 real 下载事件 + 文件内容断言（零外链/bundle 在场）
- 内容发布：release 行 status=ready + manifest files 全 ready + 幂等复冻

## 未验证项（诚实披露）

- 真实 LLM 写作质量（本线 deterministic 生成；LLM 仅组织解释文字的接入位
  定义为 generator="stub_llm"/"llm"，未接真实模型）
- 生产 PostGIS/Redis 环境下的规模表现（本机 sqlite + 内存 store 验证；
  资源有界性由代码内预算常量与单测守护）
- nightly CI real lane（不轮询线上 CI；本地 real 模式全绿为交付依据）

## 回滚方式

单分支 6 commits，`git revert` 按切片可逆；迁移 0104 downgrade 实测可用；
无共享接缝破坏性变更（main.py/_TOOL_MODULES/i18n 均为 additive 一行级）。
