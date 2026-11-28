# BASELINE — GOAL-20261127-01 Pi 自适应执行策略

## 启动审计（2026-11-27，宿主机 linux/cachyos）

- 目标仓库：`https://github.com/WindWang2/webgis-ai-agent`（已确认 remote origin）
- `origin/master` = `f34cb69dda6150ee4135e6d2a476708149d9eef7`（与任务参考快照一致）
- 本任务分支/worktree：`zcode/goal-pi-adaptive-execution-20261127` @
  `/home/kevin/projects/webgis/webgis-pi-adaptive-execution-20261127`（从 origin/master 新建，目录与分支名均未占用）
- vendor/pi gitlink = `ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe` = 上游最新 tag **v1.6.1**（ls-remote 实证，无更新正式版）→ **不改 gitlink**
- 主 worktree（`webgis-ai-agent`）在 `zcode/full-code-review-fixes`，其 HEAD `aedf3ef9d` 已确认为 origin/master 祖先（已合并 #2284）→ 只读对待
- 其他 worktree：11 个活跃任务工作树 + 2 个 prunable 临时树，全部不触碰

## PR/issue 审计

- 开放 PR：0；开放 issue：0（gh 实证）
- 近期合并：#2289（会话文件管理面板）、#2288（整合 #2283–#2287）、
  #2287（04 线图符）、#2286（02 线数据供给）、#2285（03 线联动探索）、
  #2284（全量审查修复）、#2283（01 线 Pi 决策治理）
- #2276/#2280：Pi v1.6.1 宿主融合（rpc_client/event_mapper/pi_compat/doctor/ADR 0300-0301）

## 与本线最相关的既有交付（去重对象）

- **#2283**（GOAL-20261126-01 决策治理）：pi_decision.py + pi_decision_wiring.py
  （有界决策辅助 decide_task）、map_plan_tools、local_stats schema 卫生；
  真实 classifier provider 未接线（无凭据，`set_decision_assist_transport` 注入点已留）。
  其 WP1_CAPABILITY_LEDGER.md 是本线 WP1 审计的重要输入。
- **#2280/#2276**：pi_rpc_client.py（settings/models.json 注入、spawn 面板）、
  pi_event_mapper.py（SSE 映射 + typed 诊断）、pi_doctor.py、vendor 契约测试。
- 预存失败（base 同样失败，非本线回归，只记录）：
  1. `tests/integration/test_pi_stub_llm_journeys.py` J6b（turn-2 selector 未激活 nearest_facility）
  2. `tests/test_tool_meta_contract.py` 2 项（index.mjs `e.g.,` 示例正则失配）

## 环境记录

- 模块未 build 前的已知环境性失败：`test_pi_vendor_capability_snapshot.py::test_this_checkout_is_gitlink_at_v161`（vendor dist 缺失，build 后复验）
- 资源纪律：编译/测试并发 -j4 上限，与同机其他任务共存时降级；独立端口/缓存路径在 TEST_MATRIX.md 登记
