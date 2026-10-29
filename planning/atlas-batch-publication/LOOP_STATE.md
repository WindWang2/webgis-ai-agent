# LOOP_STATE — goal-loop 账本（任务面）

## 轮次记录

| 轮 | 改动 | 验证 | 结果 | 下一步 |
|---|---|---|---|---|
| 1 | M0 三路勘察 + planning 8 文档 + worktree | git/graph 核对 + 3 subagent 交叉 | ✅ 方向未被占用；契约冻结 | M1 环境+基线 |
| 2 | M1 基线（atlas 36 + jobs 247 绿）→ M2 契约/JobKind/接缝提取 | 283 绿（字节不变） | ✅ | M3-M7 executor |
| 3 | M3-M7 atlas_batch 全包 + 页指纹含数据内容 digest（发现并修复计数不变属性变更不轮换的缓存误命中缺口） | 40 用例绿（executor 缓存/重试/取消/busy + API + 工具 + 旗舰 13 页） | ✅ commit 1 | M8 前端 |
| 4 | M8 前端（api client/TasksTab/提交 UI/i18n）+ 端点矩阵+OpenAPI/字段契约快照再生 + capability 对齐 map_export_publishing（修复 runtime manifest fail-loud 红） | vitest 493 文件 4667 绿 ×3 + tsc/eslint 绿 + API 契约 117 绿 | ✅ commit 2 | M9 收口 |
| 5 | M9 回归：cartography 2962 绿 + jobs/工具面绿 + 敏感扫描干净 + mypy 未装如实披露 | 见 EVIDENCE.md | ✅ | 独立 review R1 |

## Oracle（完成条件）

1. ✅ 12 区旗舰验收测试绿（13 页 PDF 机器解析一致）；
2. ✅ 后端回归面全绿 + ruff（mypy 白名单外，披露）；
3. ✅ 前端 tsc/eslint/vitest 全绿；
4. ⏳ 两轮独立 review 无新引入 P0/P1/P2（结论落 INDEPENDENT_REVIEW.md）；
5. ⏳ `gh pr create` 真实成功（或完整失败日志）；
6. ✅ EVIDENCE.md 命令+输出齐全。

## 状态

- 当前里程碑：M9（独立 review → 清偿 → PR）
- 分支：zcode/cartography-atlas-publishing-vnext（2 commits，基线 4b3629c90）
