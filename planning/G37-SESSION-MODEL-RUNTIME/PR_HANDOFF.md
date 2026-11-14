# G37 PR_HANDOFF

## 交付物

- **PR**：（创建后回填 URL）
- **branch**：`zcode/g37-session-model-runtime-20261114T151704Z`
- **worktree**：`/home/kevin/projects/webgis/webgis-worktrees/g37-session-model-runtime-20261114T151704Z`
- **base SHA**：`1276692737a8cf58d0604fb40ae0e70fc61779a2`（origin/master，提交前后两次复核无漂移、open PR=0）
- **head SHA**：`7096c0e54`（3 commits：feat → review-fix → browser-lane）

## 用户可操作的功能说明

管理员在部署 env 声明 `SESSION_MODEL_CATALOG_EXTRA`（同一已授权端点上的额外模型 id）后，用户在聊天输入区的「模型」行可以：查看允许目录与不可选原因；为**当前会话**选择模型或恢复默认继承（CAS，双标签并发明确冲突）；看到最近回合实际生效的模型（get_state 回读真值，非 UI 乐观态）、可选的供应商 responseModel、思考档（当前端点不可控时如实说明）、每回合与会话累计 token 用量（含 cache 分项与 incomplete 标记）与费用来源（provider 账单/估算/未知）。会话 token 预算可在管理员上限内收紧；下一轮在 prompt 发出前被确定性拒绝并给出可行动提示。agent 侧可通过 3 个只读工具查询目录/本会话模型状态/预算。

运维要点：3 张新表迁移 `g3701`（纯加性，可 downgrade）；`SESSION_MODEL_TOKEN_CAP_MAX/DEFAULT`（0=不启用）；价格表可选 env `SESSION_MODEL_PRICE_TABLE_JSON`（缺省=费用显示 unknown，绝不显示免费）。

## 测试证据（见 TEST_MATRIX.md）

- 41 单测 + 260 例回归面通过（3 个 base 既有失败已逐一干净 base 复现取证：test_pi_bridge_lock×2（MagicMock await）、test_api_auth_required（data_import/pi_interactions/temporal_media 路由，与本任务无关）、test_pi_rpc_client_810::stop_reap）。
- 真实 Pi 旅程（J0–J4）修复前后各一次通过。
- 浏览器全链（Chromium 真浏览器 → 后端 → 真实 Pi → dispatch → UI + 预算拒绝）通过。
- alembic upgrade/downgrade 回环、前端 tsc/eslint/vitest 通过。

## 独立评审

R1 APPROVE-WITH-NOTES（1×P1/2×P2/10×P3）→ 全部处置（见 REVIEW.md 对照表）→ R2 复审结论：（回填）。

## 预算披露

任务书要求 ≥320,000,000 tokens 持续工程预算；宿主未暴露可查询的 usage-budget 接口 —— 如实记录「未配置/未核验」（与 G35 PR_HANDOFF 同款披露）。

## 遗留/未测（如实）

- 供应商真实账单不可得；compaction 原生 usage 上报路径未在旅程触发（coverage 面披露）。
- 浏览器/Pi 旅程按车道纪律 opt-in（PI_JOURNEY_E2E=1，浏览器另需 REQUIRE_BROWSER=1）。
- 不等线上 CI（任务书授权）；本地关键验证全部实跑。
