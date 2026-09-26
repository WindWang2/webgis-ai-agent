# ADR-0214: Trace / Replay Oracle v3 — 从可回放到可判错的执行级回归基座

- 状态：Accepted
- 日期：2026-09-26
- 关联：ADR-0183（trace/replay v1）、ADR-0212（trace/replay 闭环 + 决策溯源 v2，#1486）、
  ADR-0213（unified cost planning）、ADR-0204（capability ABI/dispatch bind）
- 方向：F09（任务书 8 个工作包的裁决记录）

## Context

#1486 之后录制件可回放（T1 证据级 / T2 变异级 / T3 bind-gate），但四类缺口使
回归信号仍是 green-by-construction 或不可定位：

1. recorded 场景 `expect={}` 恒绿（`roundtrip.py`），gate 全红也不翻 ok；
2. receipt 级（真实 `ToolDispatchService` 合同：dedup / bind 拒绝 / 错误折叠 /
   ref 铸造）完全未重放（`deferred_levels` 诚实披露）；
3. per-tool governor estimate/actual 只活在 dispatch 适配器 finally 的局部作用域
   （CalibrationStore 聚合），无 per-call 持久投影，资源策略漂移不可定位；
4. legacy ChatEngine settle 不录制（录制面只覆盖 Pi bridge），重放语料存在盲区；
5. env fingerprint 只有 registry digest（裸 hash，无分类）；
6. 基线比对只报 digest 漂移，不可下钻到 step；
7. 失败 trace 无最小化；
8. `scrub_secret_strings` 使生产 runtime 反向依赖 replay oracle 包。

## Decision

**D1 — 秘密净化是平台能力，不是 oracle 私产。** `app/lib/redaction.py` 为唯一
权威实现；replay/sanitize re-export 兼容；runtime 两处 import 指向新家。
fuzz/negative tests 把「消毒 idempotent / never-raises / 秘密注入不出现在输出」
钉为不变量。

**D2 — 环境 fingerprint 按行为面分类，不做全量 env 快照。** v2 只收录封闭
白名单开关布尔 + 版本号 + 内容指纹（复用 `capability_graph.source_fingerprints`
与 runtime manifest fingerprint）。`env_drift` 输出 `{kind, behavioral, changed_keys}`：
policy/flags/registry/sources 变化是行为面，python/platform 补丁位是环境面。
registry digest 按 graph 指纹记忆化。

**D3 — 录制即期望（record-as-expect）。** trace 新增 `dispatch_evidence`；
roundtrip 从录制事实（final_verdict checks / map_product.task_complete /
dispatch_evidence / decision index）派生逐 turn expect，tag
`expect_source=recorded`。纪律：只期望录制时成立的事实，证据缺席的维度
不进 expect —— 回归信号从「没崩」升级为「录制时的裁决仍成立」。

**D4 — 资源事实是链上事实。** `TurnEvidence.add_resource_usage`（≤16 FIFO）→
`to_summary()["resource_usage"]` → `trace.governor = {entries, plan_cost_delta}`。
投影是 O(1) 有界的，观测面绝不阻断 dispatch；计时数值绝不进 exact/digest
（ratchet 行）。

**D5 — 两条 agent 路径同一录制缝。** legacy `execution_engine` 两个 settle 点
接入与 bridge 同款 `maybe_record_turn`（env 总闸默认关不变，never-raises 不变）。

**D6 — receipt 级重放 = 真实 dispatch 合同 + 假 provider。** T4 在进程内沙箱
跑**真实** `ToolDispatchService.dispatch`：`session_data_manager` 提为 ctor
可选依赖（唯一生产接线），registry 为 recorded-provider fake，env 三闸 +
recovery ledger 关闭 + MAPSPEC_STORAGE_DIR 沙箱化。比对 `status / geojson_ref /
error_code`，覆盖 dedup、bind 拒绝、错误折叠三类合同。fail-closed 纪律：沙箱
任何缺失 → 场景诚实 `not_run`，绝不伪造绿。

**D7 — 报告携带结构化投影，diff 下钻到 step。** 结构化投影随 report
（`-f json` 输出）走；committed baseline 文件形状不变（避免基线再生成
churn）。`diff_payloads/diff_reports` 把 digest_drift 分解为 gate_check_flip /
mutation_drift / dispatch_flip / goal_status_changed / receipt_drift，每条带
`scenario/turn/aspect/key` 定位；任一侧缺 projection → 标注
`projection_absent`，绝不产缺席侧 None→X 的伪翻转。CLI `--diff`（输入 =
完整 report）。

**D8 — 失败最小化是确定性 delta-debug。** `shrink.py` 按 turns → ops → faults →
mutations 优先级做分块折半 + 单元素删除，`max_rounds/max_candidates` 有界，
输出最小红场景 + removed 收据。CLI `--shrink`。

## Consequences

- 录制场景有明确可失败的期望（DoD#1）；T1–T4 四级重放全部实装，
  `deferred_levels` 收窄为诚实条件披露（DoD#4 的执行面）。
- capability/decision/dispatch/receipt/env drift 均可定位到 step
  （D2/D3/D4/D7，DoD#3）。T4 的沙箱在 import 期绑定 session 单例的全部
  模块（dispatch 服务 / mapspec.store / lifecycle_engine / mapspec_store /
  artifact ledger 函数面）上逐一替换替身 —— Redis 部署下同样零真实后端写。
- T4 沙箱内零真实外部副作用（内存 store + env 关闸 + 沙箱目录，DoD#4）；
  秘密防线中立化 + fuzz（DoD#4 的 secret 面）。
- 成本：`ToolDispatchService` ctor 增一个可选参数（向后兼容）；baseline 文件
  变大（结构化投影，仍为 KB 级）；shrink 是显式 CLI 动作，不进常规 suite。

## Alternatives Considered

- **LLM 重生成式重放**（重跑 prompt 对比语义）：非确定性、需网络，违反离线
  确定性纪律 —— 拒绝。
- **把 ToolDispatchService 的 session_data_manager 全局单例彻底 DI 化**：
  触碰全仓调用面，超出本方向最小接缝 —— 只提 ctor 可选依赖。
- **expect 全量回填（含 mutations T2）**：MAP_MUTATIONS 链记录无参数，重建是
  伪造 —— 维持 #1486 的诚实边界，变异级归 corpus 场景。
