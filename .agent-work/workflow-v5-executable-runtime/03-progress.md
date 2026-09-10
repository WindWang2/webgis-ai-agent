# Workflow V5 — Progress

## 提交记录（每 wave 独立 commit）

| commit | wave | 内容 |
|---|---|---|
| ccf85276 | W1 | V4 recompute 真实包形态边端点归一化 bug fix（B-1）+ 7 强闭包回归 |
| 57bc1533 | W2-4 | contracts/machine + fingerprints + binding（39 tests） |
| 7d748d2b | W5-6 | 4 表 models + migration 0034 + store/registry/reuse（55 tests） |
| 7cefc81b | W7-10 | geocompute 适配器 + driver + recompute 闭环 + service/projection |
| 825ec2ff | W11-12 | REST API 10 端点 + 3 chat/session 挂钩 + e2e 完成证明 |
| 8b99a222 | W13 | 子工作流运行时（boundary/深度环守卫/义务继承） |
| 5b4dfdb6 | W14 | 前端 inspector + ADR-0119 + CHANGELOG + manifest regen |

## 架构挑战（Subagent-A，agent_5af42753）处置

- B-1（BLOCKER）：V4 recompute 真实形态闭包失效 → W1 修复 + 回归测试；
- C-1/2/3（CRITICAL）：quiescence 门 + pending 队列、oracle 三方矛盾
  修正（READY→STALE 转移 + 集合定义）、每节点行 CAS + 完成永不放弃 +
  租约/孤儿清扫 → 全部落入存储形态与状态机；
- M-1~M-8（MAJOR）：迁移撞号 rebase 协议（§16）、义务变更走 data/output
  通道、指纹栈 content_revision/shape 级不复用/live descriptor、env_fp
  覆盖 geo 栈+registry 指纹、挂钩点核实 + 锁外 + role 绑定复用
  `_derive_bound_refs`、superseded 终态、子工作流上限/反向指针、幂等键
  去 instance_id（dataset_fingerprints 语义指纹自然跨实例去重）；
- MINOR 全项：SKIPPED→READY、CANCELLED 非终态限定、实例终态裁决、
  eligibility 含 failed、anonymous 域 session 同域、完整 plan 对象消费、
  claim token 双执行者协调、恢复 fp 失配诚实 miss。

## 本地验证（精确结果）

- `tests/unit/workflow_runtime/`：83 passed（contracts/machine 26、
  fingerprints/binding、store/registry/reuse/migration、driver/recompute/
  service、API 6、subworkflow 8、e2e 完成证明）；
- `tests/unit/gis_harness/`：全量 922+ 绿（V4 契约零漂移）；
- 两序组合（runtime↔gis_harness 先后）各 1068/1076 passed；
- changed-scope 邻接：pi_session_plan_host / chat_session_plan_route /
  workflow_api / geocompute_executor_v4 = 29 passed；
- quality 套件 334 passed（OpenAPI 快照显式刷新 —— additive 新端点
  breaking=0；quality manifest/report/generated-artifacts 账本再生成）；
- migration 单 head 断言 + up→down(0033)→up 可重入；
- `ruff check app/ tests/` 全绿；前端 vitest 3 passed + tsc/eslint 绿。

## 完成证明场景（Epic §18，tests/.../test_e2e_completion_proof.py）

真实 GeoExecutionEngine（buffer_smart 数值算子 + MATERIALIZE 落存），
20 合成点 → buffer(100m) 产出面要素 session ref → style change 零执行
（节点状态逐项不变）→ distance 100→250 仅 transform+output 标 STALE 并
真重算（新 ref、attempts≥2、data:subject 绑定不动）→ 同输入再次 STALE
复用命中（reuse.fingerprint_level=content）→ 全程 decisions 决策环落库。
