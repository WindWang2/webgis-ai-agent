# G44 PR_HANDOFF — 合并交接

## 身份

- Branch: `zcode/g44-field-contracts-20261115T124850Z`
- Base: `655f769ca17f17c5797da13b4c299b8fa600e370`（origin/master，fetch 复核无新提交）
- Worktree: `/home/kevin/projects/webgis/webgis-worktrees/g44-field-contracts-20261115T124850Z`
  （本地 `.venv`、`frontend/node_modules`、`vendor/pi` 构建产物与 `tmp/` 均不入提交）

## 合并前检查单

1. 迁移：`alembic upgrade head` 落 `g44_field_contracts`（两张 additive 新表 +
   部分唯一 active 索引；downgrade 反序删表；幂等守卫可重入）。空库与
   base 库升级均已真实验证。
2. 生成物已再生成并过 staleness 闸（openapi 快照 / quality manifest / drift
   report / generated-artifacts 账本）。
3. 编辑路径新增合同 DB 冷读：迁移必须随代码同批发布（fail-closed 语义，
   见 INTERFACES.md 运维注记）。
4. 无合同数据集零行为变化（F01 回归钉住）；观测画像语义未动。

## 复现关键验证

```bash
# 后端（venv 内）
pytest tests/unit/field_contracts/ tests/unit/feature_edit/ tests/unit/feature_proposals/ -q
# Pi 旅程（需 node + 已构建 vendor/pi，重阶段锁）
PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_g44_field_contracts_journey.py -q -s
# 浏览器旅程（real 档：后端 USE_REDIS=false + LLM stub :8899 + 前端 :3310）
E2E_MODE=real E2E_USER=… E2E_PASS=… pnpm exec playwright test j24 --workers=1
```

## 已知边界 / 后续增强

- 真实浏览器内「编辑器选择器→表单」腿因冷 dev server 图层水合时序未入 j24
  （组件级全绿；TEST_MATRIX 分层披露）。
- merge 继承属性的跨字段规则（F20）走同一候选态校验，未单列专项用例（复审
  备注）。
- 既有基线失败（#2123 域）：alembic percent-URL 门、4 文件裸 CJK、
  topology.py F841、cartography-result-card tsc —— pristine base 同条件复现。
