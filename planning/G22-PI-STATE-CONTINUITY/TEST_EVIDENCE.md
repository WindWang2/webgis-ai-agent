# TEST_EVIDENCE — G22-PI-STATE-CONTINUITY

> 环境：worktree `webgis-wt-g22-pi-state-continuity-20261111T151408Z`，
> venv Python 3.13.17 + requirements.lock；node v24.21.0；vendor/pi 构建经
> `build-vendor-pi.sh`（npm ci，root lockfile）。命令均在 worktree 根执行。
> 日期：2026-11-11/12（UTC）。

## P0 — 版本与真实运行面

| 证据 | 命令 | 结果 |
|---|---|---|
| 基线 v1.5.1 doctor | `python scripts/pi_doctor.py` | 6 域 PASS（428 tools；native 7/7；事件词表全量） |
| 基线 v1.5.1 契约 | `pytest tests/integration/test_pi_vendor_contract.py tests/integration/test_pi_schema_coercion_golden.py` | 13 passed (130s) |
| v1.5.2 重建 | `bash scripts/build-vendor-pi.sh --reinstall`（持宿主锁） | exit 0；bundle rpc-entry.js 产出；npm ci 走 root lockfile |
| v1.5.2 doctor | 同上 | 6 域 PASS；vendor 域在 gitlink stage 前如实 WARN drift（stage 后消） |
| v1.5.2 契约 | 同基线命令 | 13 passed (32s) |
| v1.5.2 全栈旅程 | `PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_stub_llm_journeys.py` | 1 passed（J1–J8 全绿；首次在 capability 种子解堵 boot 后） |
| A01 构建产物=目标 SHA | build 日志 `submodule HEAD: b4afdddf44e059c67ccf5097e5317d913521b470` + doctor vendor 域 pin 核验 + tag `git rev-list -n1 v1.5.2` = `b4afdddf4` | 一致 |
| 稳定性（J6b 观测） | 旅程 ×6 连跑（v1.5.2） | 见下方「J6b 根因调查」节（回填） |

## master 基线破损记录（非本方向引入，BASE b8f2a6ecc 复现）

| 破损 | 证据 | 处置 |
|---|---|---|
| strict manifest 12 个 dangling capability id → app 启动 RuntimeError | 干净 BASE worktree uvicorn boot 失败（standalone 复现脚本） | 解堵补丁 commit 1（capability 种子，G15 惯例；owner 可迁移） |
| api_contract_fields 快照落后晨间 42 个新端点 | `test_response_field_signatures_unchanged` BASE 红 | 同 commit 机械再生（API_CONTRACT_FIELDS_UPDATE=1） |
| response_model 覆盖缺口（晨间端点） | `test_response_model_coverage` BASE 红 | **未修**（G10/G11/G17/G19 owner 的 API 债；PR body 披露） |
| mapspec 测试缺 jiti | `test_validate_and_compile` BASE 红 | worktree 符号链接（G12 先例；主工作区 frontend jiti 只读借用） |

## P1 — turn 隔离（A07/A08/A09/A12）

| 证据 | 命令 | 结果 |
|---|---|---|
| 专属隔离测试（写出时刻身份断言） | `pytest tests/unit/test_pi_turn_isolation_g22.py` | 7 passed |
| 取消/并发/泄漏回归 | `pytest tests/unit/test_pi_cancellation_unified.py tests/unit/test_pi_bridge_concurrency.py tests/unit/test_pi_bridge_leak.py tests/unit/test_pi_turn_state_machine.py` | 38 passed |
| 泄漏归因（mock 契约） | 中间失败 `object MagicMock can't be used in 'await'` → mock 补 issue/collect 后绿；BASE 对照绿 | 已修 |
| 全量 pi 单元 | `pytest tests/unit -k "pi"` | 1880 passed / 4 failed → 修 mock×2 + jiti 后余 1（response_model 基线债）；终态复跑见下 |
| A12 EOF→有界→强退 | `tests/unit/test_pi_rpc_client.py`（stop 顺序族，既有） | 通过（套件内） |

## P2 — 恢复目录（A13–A16/A18）

| 证据 | 命令 | 结果 |
|---|---|---|
| provider 套件（含 9 例 G22 新增） | `pytest tests/unit/context_assembly/` | 87 passed |
| A13 全层约束提取 | `test_g22_a13_hidden_constraint_beyond_scan_cap_survives` | passed |
| 预算挤压约束优先 | `test_g22_constraints_survive_budget_pressure`（≤1600） | passed |

## P3 — 对账（A17/A21 + #2061 + Redis 清扫）

| 证据 | 命令 | 结果 |
|---|---|---|
| 对账套件 | `pytest tests/unit/test_g22_recovery_reconciliation.py` | 6 passed |
| A10/A11 配置换代 @v1.5.2（#2061 修复复验） | `pytest tests/integration/test_pi_model_config_generation.py` | 2 passed |
| 会话隔离（既有） | `pytest tests/unit/test_pi_gis_usability_fixes.py -k "isolate or session"` | 1 passed |

## P4 — 重连/去重（A19/A20）

| 证据 | 命令 | 结果 |
|---|---|---|
| 专属套件 | `pytest tests/unit/test_g22_reconnect_dedup.py` | 4 passed |
| SSE 纯重放（既有） | `pytest tests/test_sse_resume.py`（+ J7 旅程内嵌） | 通过（套件绿；J7 反 vacuous 锁新增） |
| durable 幂等复用（既有 e2e） | `tests/jobs/test_job_celery_e2e.py`（get_by_idempotency_key 族） | 套件既有绿（引用） |

## P5 — N4 修复

- J7 llm_calls 计数：json.dumps separators 对齐 + `llm_calls_observed_before >= 1`
  反 vacuous 锁（`tests/integration/test_pi_stub_llm_journeys.py`）。
- 权限/receipt 失效分层：H01 族（permission→面投影）+ session_data 删除完整性
  既有测试族（引用）；A16 provider 侧新增（P2）。

## P7 — 失效注入 / soak / 回退

### A23 回退演练（v1.5.2 ↔ v1.5.1，持宿主锁链式执行）

| 步骤 | 结果 |
|---|---|
| `git -C vendor/pi checkout dd30101cf`（v1.5.1） | OK |
| `build-vendor-pi.sh --reinstall`（npm ci 重建） | OK |
| pi_doctor @v1.5.1 | no FAIL（vendor 域 WARN=工作树偏离 gitlink，符合演练预期）；build/rpc PASS |
| 契约+golden @v1.5.1 | 13 passed (29.2s) |
| `git -C vendor/pi checkout v1.5.2` + `--reinstall` | OK |
| pi_doctor @v1.5.2 | vendor PASS（pin ok v1.5.2；floor v1.5.1）+ build/rpc PASS |
| 契约+golden @v1.5.2 | 13 passed (29.1s) |
| 旅程终验 | 1 passed（needle 修正后） |

结论：回退 = pin-only 双向可行；floor 不动（v1.5.1 仍满足协议下限）。

### A24 soak

- `tests/unit/test_g22_soak_bounded.py`：160 turn 压力（receipt 逐出真实
  触发）→ abort 来源 ≤64 / receipts ≤128 / 交接门台账 ≤8 / resume 会话键
  不随 N 增长；200 层+30 锁+24 画幅 mapspec 重建块 ≤1600 且约束在。
  1 passed。

### 终态全量回归

- `pytest tests/unit -k "pi"`：**1883 passed / 1 failed**（唯一失败
  `test_response_model_coverage` = 晨间批次 response_model 基线债，
  BASE 复现记录见上表，非本 PR）。
- 前端：recovery-banner vitest 6 passed；ChatTab 消费者回归 76 passed。

## 独立评审三轮记录

- 第一轮 BLOCK（P1×2+P2+P3×4）→ 修复 commit「独立评审修复轮」（细节见
  REVIEW.md）；第二轮复核 7 项中 6 项 FIXED、#2 打包器 PARTIAL（重维度
  1638–1695 > 1600）→ 修复 commit「二轮复审残余清偿」（打包器 v2 构造性
  不变量 + 属性扫描 2900+ 组合 + drain 单次有界 + 补测 2 + ADR 残余段）。
- 第三轮收窄确认：**PROCEED**（reviewer 独立对抗脚本 612 例——600 随机
  维度 + 12 病态边界（400 字符 id、60 画幅、全零/全满、80 层全标注等）：
  max 1535、0 违例、358 重载荷带披露行；13 套件 208 passed；j17（现 j22）
  1 passed；journeys 1 passed）。唯一 informational：1500 字符 product_kind
  （G15 枚举外）触发丢弃+披露回退——有界且披露，非阻断。
- 旅程更名：`j17-restore-continuity` → `j22-restore-continuity`（#2090
  G28 已占用 j17 前缀；registry id/file/describe 同步，lane 锁 19 passed，
  更名后旅程复跑 1 passed (23.6s)）。

## J6b 根因调查（P5）

- 历史基线：master@v1.5.0 干净基线 10 跑 4 败（G12 PR_BODY 记录）。
- 机制：J6b turn-2 stub 发 `nearest_facility` 裸名；vendor "Tool not found"
  ⟺ 该请求激活面缺此工具（before_agent_start 投影）。
- 本轮插曲：首次 ×6 稳定性连跑 7/7 失败——根因是**本轮 N4 修复自身的
  needle bug**（`{"key":…}` 闭合括号不匹配 stub 行），反 vacuous 锁按设计
  如实拦截；修正后单跑通过。
- v1.5.2 ×6 结果：**6/6 passed**（26–30s/跑；加上 needle 修正后单跑，累计
  **7/7 连绿**）。结论：G12 记录的 v1.5.0 时代 turn-2 选择抖动在当前 pin
  （v1.5.2 + G12 每轮表面重置 + J8 确定性发现门）下 **7 连跑不可复现**。
  如实记录：不宣称根因已修（无法证伪历史间歇性），但现行硬门（J6b
  nearest_ok + J8 确定性发现）全绿且未放松；declared_nearest 维持证据字段。

