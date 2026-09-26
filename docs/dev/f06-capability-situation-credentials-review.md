# F06 — 独立 Review 报告（Subagent C Gate）

- 审查对象：`zcode/f06-capability-situation-credentials-20260926-9e1ad229` vs `origin/master (9e1ad229)`
- 审查方式：独立只读深审（实读代码 + 实跑测试，venv 串行），不采信实现者自评
- 初判 VERDICT: **FIX-FIRST**（1×P1 + 1×P2 + 6×P3）；清偿后复跑全绿

## 十项审查结论（清偿前基线）

| # | 维度 | 结论 |
|---|---|---|
| 1 | 架构单一真相 | PASS — presence 唯一注入点（tool_security）；situation 复用 bind 白名单；reason codes 是 qualification 纯投影；治理复用同一 conformance 校验器；无第二注册表 |
| 2 | 正确性 | 三态 auto-supply / merge 优先级 / unknown-False 区分 / depends_on 守卫均正确；P1 命名空间冲突（见下） |
| 3 | 并发/事件循环 | PASS — 探针 loop-check + to_thread；CM 不包 yield（generator-throw 缺陷已规避，测试钉死）；ContextVar 纪律与 confirm_tier3 同款 |
| 4 | 异常面 | PASS — 新 broad except 逐一定性不吞上游错误；唯一死分支已清（P3） |
| 5 | 数据泄露 | PASS — presence 投影无 secret；denial record 值级 scrub；负例有效 |
| 6 | 兼容性 | **P1 + P2（见下）**；kill switch 双闸可逐位还原 master |
| 7 | user-wins | PASS — 权限授予保持 tier3/plan-approved；env 权限仅披露不授予（双向钉死） |
| 8 | 测试有效性 | PASS — 负例真实（fail-open×3 / no-secret 含负控 / ratchet 可失败）；互染清理到位；canary 下限缺口已补（P3） |
| 9 | 性能 | PASS — 供给路径实测 ~16µs/call；图新增边门控于声明（当前零声明零成本） |
| 10 | schema/replay 兼容 | PASS — additive 键；TOOL_CALLS riding 记录经 `_STRUCTURED_PAYLOAD_KEYS` 结构化通道，`_collect_raw_chain_decisions` 可提取，replay metrics 消费链实跑验证；drift rederive 对 denial kind 诚实返回 None |

## 发现与清偿

### P1 — 可用性事实与 #1402 权限门命名空间冲突（已修）
- **问题**：`USE_REDIS` 默认 True ⇒ `celery_broker=True` 恒入 `dependency_available`；权限门（qualification_v8 L282-289）把 `dependency_available` 非空当作「已声明授予面」→ 任何工具日后声明 `required_permission` 即在零运维配置下从 unknown 翻转为 deny —— 违反「零配置⇒逐位一致」承诺（实跑实证 bare=eligible vs supplied=ineligible）。
- **修复**：可用性事实迁入 QualificationContext 新增的专用 additive 字段 `runtime_availability`（与 `dependency_available` 命名空间隔离）；消费契约 = 工具声明 `provider_dependencies` 命中本表且值为 False → 失格（结构化 `dependency` reason，键缺席 unknown 不裁决）—— worker/backend 可用性由此获得真实资格语义 + alternatives 解释通道；bind 白名单同步放行；负例回归 `test_availability_namespace_isolated_from_permission_gate` 钉死隔离。

### P2 — depends_on/alternative_to 不进 manifest 指纹（已修）
- **问题**：两字段改变解析语义（依赖降级/替代并集）却不在 `_project_capability` 投影内 → 声明变更后持久计划不判 stale（对照：fallback_capabilities 在投影内）。
- **修复**：两字段补入投影；**声明为空不占条目** ⇒ 今日零声明零指纹漂移，声明落地才触发诚实 stale（与 declared bindings 同纪律）。

### P3（全部已修）
1. dispatch 供给 `except: if situation is None: pass` 死分支 → 清理为 `pass` 并注释语义。
2. reason codes 截断口径三处不一（6/4/4）→ 单点 `MAX_REASON_CODES` 贯穿 bind details/evidence + denial record。
3. `sorted(granted)[:4]` 静默截断 → 上限 8 + `RuntimeSituation.truncations` 诚实披露（进 bounded view）。
4. EnvCredentialPresenceProvider 不校验槽位值形态 → secret 形态启发式拒收（known 前缀 sk-/ghp_/AKIA/PEM + ≥40 位混合高熵），fail-closed（宁可丢 presence 披露，不让值面流入资格解释面）。
5. 治理 canary 无正向下限 → 新增 `test_live_inputs_non_degenerate`（空采集可失败，杜绝空洞通过）。
6. 「单飞防惊群」注释与实现不符 + async 供给无生产 caller → 注释诚实化；chokepoint 改用 `merge_situation_facts_async`（探针在非循环线程执行，`GIS_SITUATION_PROBE_WORKERS` 真实生效）。

## 清偿后回归证据（本地串行 -n 0）

- 新套件 5 文件：**58 + 26 = 84+ 项全绿**（security 17+4 / situation 26+3 / supply 9 / relations 12 / governance 20+1）
- 邻域回归 **174 passed, 1 skipped**：bind_unified / tool_dispatch_service / binding_conformance / abi_profile / runtime_v2_manifest / capability_resolution / qualification×3 / audit_1395 / audit_1402 / relations_v2 / governance+canary
- planner 面 **55 passed**：session_plan / plan_orchestrator / planner
- graph v8 + scientific contracts：64 passed + 1 known flake（`test_ra3_cross_scope_model_no_false_duplicate`，顺序依赖，单跑绿 —— master 既有，两 worktree 对照实证）
- 四 dispatch 调用面 import 冒烟 OK

## 遗留（不阻塞，登记 follow-up）

- `credential_metadata` 当前无生产消费者（契约完备、有界、测试覆盖；等 extension/credential store 接入时消费 expiry/owner 披露）
- denial 记录使 TOOL_CALLS bucket（≤8/turn）更早驱逐早期记录 —— 既有有界驱逐语义，如需可后续给 denial 独立 stage（需链 schema 演进，#1486/#1503 面）
- celery 策略工具的 presence 授予不跨进程（worker 侧无消费者；gate 在派发侧 chokepoint 已裁决）
