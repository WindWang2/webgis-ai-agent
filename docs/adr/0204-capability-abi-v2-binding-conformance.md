# ADR-0204: Capability ABI v2 — 绑定收敛、Conformance 闸与 Dispatch 证据

- 状态：Accepted（本地验证）
- 日期：2026-09-20
- 关联：ADR-0137（V8 统一能力图）、ADR-0181（能力图激活 V1）、ADR-0006/0014/
  0068（dispatch 单一拥有者）、ADR-0079/0080（compiled runtime manifest）、
  ADR-0103（ToolDescriptor V3）、ADR-0201（扩展包能力认证）、#1395（dispatch
  bind）、#1402（凭证/权限闸）、#1408（governor dispatch）
- 基线：`origin/master` = `5a4d4632`

## 背景

ADR-0181 激活了能力解析，#1395 把绑定推到 dispatch，但勘察
（`docs/dev/capability-abi-v2-recon.md`）确认五个结构缺口：

1. capability→tool 绑定有两个互不对账的声明面：算法 `tool_candidates`（喂
   manifest）与工具 `capabilities=` 声明（喂能力图 implements 边）。真实
   registry（345 工具）实测：声明面悬空 0，声明但算法链不可达 61 对/57 工具
   —— manifest 对后者全盲，同一运行时存在两份不一致的 provider 视图。
2. dispatch 绑定只挂在 Pi bridge 单点；ChatEngine legacy 路径零检查。
3. conformance 只存在于扩展包认证（ADR-0201）；core registry 声明面无对账闸、
   metadata 完整度无分级（缺字段静默注册）。
4. dispatch 证据无 capability 维度（无法回答「为什么允许这个 provider」）。
5. capability 级无 ABI 聚合画像（事实散在 provider 描述符上）。

## 决策

### D1 — manifest v4 是绑定收敛点（不是第二事实源）

`runtime_manifest` 本已是「运行时能力真相」的编译载体（ADR-0079），v4 把声明
面 B 并入既有 cross-registry 反查图：

- `_project_tool` 增 `capabilities`（sorted）→ 绑定内容进指纹（漂移 →
  `is_stale_plan` 可感知，#1084 语义自然延伸）；
- `capability_to_tools`/`tool_to_capability` 先算法派生（priority 序）、后
  声明面按字典序追加去重；新增 `declared_capability_bindings` 溯源字典；
- 新议题：`capability_id_dangling`（fatal —— 与
  `algorithm_dangling_capability`/`recipe_dangling_capability` 同级同语义；
  实测存量 0，不砖启动）与 `tool_capability_divergence`（warning，聚合单条，
  有界清单 —— 声明面分歧是**合法态**（交互/编排/巡检工具不走算法候选链），
  收敛披露而非惩罚）。附带信号迁移：`network_tool_orphan` 守卫现在能看到
  声明面绑定（原孤儿告警相应收敛进 unbacked 聚合披露）。
- `MANIFEST_VERSION 3→4`：绑定面进指纹是解析语义变化，旧计划应诚实判 stale。

不重写能力图、不替换 registry 权威：图继续消费两声明面（关系索引面），
manifest 是编译期对账与汇聚点，两者首次内容一致。

### D2 — core conformance 校验器（纯函数，manifest 消费）

`app/lib/gis/capability_conformance.py`：
`validate_capability_conformance(capabilities, algorithms, tool_metadata) ->
List[ConformanceIssue]`。四类检查、两级分级：

| code | severity | 语义 |
|---|---|---|
| `capability_id_dangling` | fatal | 声明的 capability id 不在词表（不可解析的绑定） |
| `capability_binding_unbacked` | warning | 声明无任何算法候选链支撑（合法但需可见） |
| `descriptor_metadata_incomplete` | warning | 声明 capability 却缺 network/deterministic/side_effect 分类/result_size_policy（禁止静默残缺注册） |
| `provider_output_contract_divergence` | warning | 同 capability 多 provider 的 `output_semantic_type` 互相冲突（无阻断执行语义，披露等价性风险） |

产出排序确定、条数有界；`compile_runtime_manifest` 消费（fatal 入
`issues.fatal` → 既有 strict fail-fast）；`validate_gis_library` 折叠 manifest
fatal（与图闸同段）。扩展包认证（ADR-0201）不动——那是包生命周期管线，
本闸是 core 编译期对账，二者互补。

### D3 — dispatch 绑定单一调用点（迁移，不是重写）

`check_tool_capability_at_dispatch` 实现不动（同 kill switch
`GIS_CAPABILITY_DISPATCH_BIND`、同拒绝条件、同 fail-open），调用点从 Pi
bridge 迁入 `ToolDispatchService.dispatch`（dedup 占位后、guardrails 前）：

- 四个调用方（legacy tool_pipeline / Pi bridge / workflow_engine / 测试直构）
  一次全覆盖——legacy 路径从「零检查」变为与 Pi 同语义（修复，非行为破坏）；
- 拒绝路径释放 dedup 占位（与 guardrail BLOCK 同纪律）并返回 typed error
  结果（`raw_result` 携带 `CAPABILITY_INELIGIBLE` details）→ Pi details 与
  legacy SSE 自动流达；
- `dispatch(+situation=None)` additive 形参：调用方可传
  `QualificationContext`（今日全部调用点缺省 → 与 #1477 逐位一致；situation
  供给是后续增量，不阻塞收敛）；
- Pi bridge 内联预检删除（避免双闸双证据）。已知行为 delta（review RB-P3）：
  Pi 的 capability 拒绝此前在记账前返回，现随 error 结果进入
  `_record_gis_progress(outcome="error")` 与 no-progress 连击统计 —— 拒绝
  本就是「无进展」，计入属预期语义；
- 拒绝备选面收紧（review RB-P2）：alternatives 仅列 tool-kind 候选（model
  不可被 LLM dispatch，不得作为「可执行替代」出现在拒绝理由里）。

### D4 — dispatch 证据（capability 维度）

- `TurnEvidence.add_capability_dispatch(entry)`（每 turn ≤16，thread-safe，
  与既有累加器同锁纪律）；
- `ToolDispatchResult.capability_evidence`（additive）：allowed/refused 均
  记录 `{tool, action, capabilities, status, alternatives[≤2], reason,
  code}`；
- 纪律：无参数、无凭证、无结果 payload——只记 id/code/score（与 evidence
  面既有脱敏边界一致）。

### D5 — capability ABI profile V2（derived 聚合，零新声明）

`capability_resolution.capability_abi_profile(capability_id)`：从图与工具
描述符**派生**的 capability 级聚合（`abi_version: 2`）——provider 面逐个
ABI 事实（status/side_effect/network/deterministic/idempotent/cost/
scale_class/execution_policy→cancellation 类/security_tier/溯源
declared|derived）+ 聚合旗标（deterministic 全称、offline_capable 存在、
凭证/权限并集、副作用类并集、输出语义型并集）+ fallback/conflicts。
`describe_capability` 嵌 `abi` 键。不引入 capability 级新声明字段——
第二真相源是红线（ADR-0137 纪律）。

## 后果

- 同一运行时首次拥有单一 capability↔tool 绑定汇聚点（manifest）+ 一致的
  关系索引（图）；绑定漂移进指纹（stale 可感知）。
- legacy dispatch 路径获得与 Pi 相同的 capability 闸；拒绝/允许首次双面
  可解释（证据）。
- 已知取舍：分歧面 61 对以 warning 披露（治理后续逐段收敛，不在本 PR 强
  改 57 个工具的声明）；`MANIFEST_VERSION` 升 4 使既有持久计划全量判
  stale（部署后首轮重规划，属预期语义）。

## 验收

见 `docs/dev/capability-abi-v2-design.md` Acceptance Matrix（16 项，全部
落在新增单测；核心场景含真实 registry 多 provider 解析）。
