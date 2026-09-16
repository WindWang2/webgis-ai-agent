# PARALLEL OWNERSHIP — 并行施工矩阵（执行时快照 faa453a8）

| 文件/模块 | 本分支动作 | 并行 PR 触碰 | 冲突风险 | 策略 |
|---|---|---|---|---|
| app/core/config.py | 增量设置 + validator | 无 PR 直接触碰 | 低 | 尾部追加；rebase 时人工核对 |
| app/core/network.py | session 挂 trace | 无 | 低 | |
| app/services/chat/llm_client.py | acquire() 挂 event hook | #1335/#1336 不触碰此文件 | 低 | |
| app/services/data_fabric/security.py | adapter.send() 前置 egress 检查 | #1351 相邻（quality 面） | 中 | rebase 核对 send() 函数体 |
| app/api/routes/health.py | 增组件 | 无 | 低 | |
| app/schemas/health_schema.py | 增可选字段 | 无 | 低 | |
| manage.py | 新子命令 | 无 | 低 | |
| frontend/lib/providers.ts | 增 local provider + 过滤 | #1353 只加新组件 | 低 | |
| tests/conftest.py | _ENV_BASELINE 追加键 | 多方向都会加键 | 中 | rebase 时合并追加 |
| .env.example | 追加键 | 同上 | 中 | 同上 |
| app/lib/harness/visual_judge/vlm_provider.py | ad-hoc httpx 换守卫 helper | #1356 cartography 相邻 | 中 | 只动 client 构造行 |

## 热区禁入

- `modelops/geoai/**`、`frontend/components/geoai/**`（#1336）
- claim/tenant/mission 修复面（#1335）
- `app/services/data_fabric/` 的 quality/harmonization 文件（#1351）——本分支只动 `security.py` 与（如需）个别 adapter 的错误路径。

## 合入前动作

PR 前 `git fetch --all --prune` → 列 open PR diff touched files → 与本分支 `git diff --name-only origin/master...HEAD` 求交 → 语义冲突逐个处理。
