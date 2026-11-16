# G43 PR_HANDOFF

- PR：见推送输出（对 master）
- branch：`zcode/g43-turn-context-control-20261115T124713Z`
- worktree：`/home/kevin/projects/webgis/webgis-worktrees/g43-turn-context-control-20261115T124713Z`
- base SHA：`655f769ca`（master，含 G40 #2124 与 #2122 迁移合并）
- head SHA：`c05ae0cb9`（feat 7f5b24db8 + review-fix c05ae0cb9）

## 功能一句话

每个 Pi 聊天轮的「实际装配 + 提交」事实持久化为可查回执（源粒度 + 真实权威
版本 + 诚实状态机）；用户可查看每轮回执、并对下一轮可选项目资料（知识条目/
制图记忆/空间记忆）设置优先或排除（CAS，控制域不可关）；预览零副作用；
@tool 只读查询；前端面板 + 工作台 + SSE/恢复。

## 验证证据

- 真实 Pi 旅程 J1–J5（PI_JOURNEY_E2E=1，vendor v1.5.2 @ b4afdddf，stub 供应商
  边界记录完整 prompt）：1 passed —— 含排除缺席边界证据 / not_sent 零请求 /
  非流式同语义 / 历史消息回执去重。
- 后端 188 passed（1 失败为基线既有 test_fetch_loads_plan_once...，干净 base
  复现，见 #2123）；FE vitest 10 passed；tsc 本域 0 错误；ruff 本域 clean。
- 迁移：空库 → head ✓；g40 head → g43 head ✓。
- 独立 review 两轮：R1（1 P1 + 2 P2 + 8 P3）→ 全部修复 → R2 **ACCEPTABLE**
  （见 REVIEW.md）。

## 环境注

- 旅程以 USE_REDIS=false 自足运行（本机 Redis 16379 未起；官方回退路径）。
- vendor/pi 物化自本地 module store（同 pin），已 build（bundle+dist）。
- 未等待线上 CI（协议约定）；浏览器 Playwright 未运行（如实记录）。

## 后续可做（非本 PR 范围）

- 浏览器级走查补充（vitest 组件证据已覆盖主要状态）。
- not_sent_reason both-None 分支补键（P4 nit N1）。
- #2123 基线失败（test_fetch_loads_plan_once...）属仓级债务。
