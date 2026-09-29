# E15 — 离线 Harness 评测实验室（Offline Harness Evaluation Lab）设计

- 状态：Accepted（随实现 PR 一起评审）
- 日期：2026-09-29
- 基线：`origin/master@77d2678d`
- 关联：ADR-0104 D3（质量平台"派生 + 闸"形态）、ADR-0183（trace/replay v1）、
  ADR-0212（replay 闭环 v2）、ADR-0214（oracle v3 / 视觉观察 / 资源调度）、
  ADR-0204-f03（单结算 seam）、#1555（j1 journey page.evaluate 绕过真实 UI）、
  #1552（evaluation corpus / science_oracles 测试薄弱）

## 1. 问题

F09 之后已有四个各自可用的离线评测面，但它们互不连接、fake 资产三处分散、
故障覆盖有洞、replay 只能单进程：

| 既有面 | 位置 | 缺口 |
|---|---|---|
| trace/replay oracle v3（T1–T4） | `app/lib/harness/replay/` | 无统一场景契约；故障词表缺 cancel/duplicate/policy/resource；单进程顺序执行 |
| evaluation corpus 分层 runner | `app/evaluation/` | 与 replay 双轨，无统一报告/指标投影 |
| science oracles（14 域 JSON） | `tests/science_oracles/` | 只被专属测试与脚本消费，不在统一回归报告里 |
| 制图语义检查 + 视觉 fixture | `app/lib/cartography`、`app/lib/harness/visual_judge` | 离线消费入口分散在 harness gate / quality loop |

同时 fake LLM/provider 先例有三处（patch `_call_llm`、`_RecordedProviderRegistry`、
前端 `llm-stub.mjs`），没有可被场景规格声明的正式化 deterministic fake；
e2e 主链 journey（j1）用 `page.evaluate` 直打 `POST /api/v1/upload` 冒充用户
（#1555），上传 UI 入口根本不存在（DatasetManager 的 upload 档只有文字提示，
无文件选择器）。

## 2. 决策

**D1 — 统一场景契约 `LabScenario`（ScenarioSpec v1），编译而不是第二 runner。**
`app/lib/harness/lab/spec.py` 定义 versioned（additive-only）场景规格：
`kind ∈ {replay, settlement, benchmark}` + 用户目标 + 声明式数据面（fixture
alias）+ scripted provider 收据 + 故障计划 + 期望（evidence/state/export）
+ 可选 science oracle 域引用。`compile_replay_scenario()` /
`compile_benchmark_case()` 把规格**投影**到既有 runner 的输入
（`replay.Scenario` / `GISBenchmarkCase`），执行全部委托既有 evaluator ——
符合 ADR-0104 D3"不建第二 runner"；lab 是编排/接线层，不是第二套评测逻辑。

**D2 — deterministic fake 正式化。** `lab/fakes.py`：
`ScriptedToolProvider`（duck-typed registry：metadata + dispatch，canned 收据
按 (tool, 规范化参数) 精确对齐 + 出现序游标回退，从 `_RecordedProviderRegistry`
泛化并故障感知）；`LabClock`（`replay.determinism.DeterministicClock` 的
monotonic/advance 门面）；`FaultInjector`（计划驱动的确定性故障编排器）。
不复制任何现有 fake —— replay 侧 T4 继续用 `_RecordedProviderRegistry`，
lab 侧新 fake 服务于 settlement/一致性模式。

**D3 — 故障计划词表补洞。** 既有 `replay/faults.py` 十类（纯场景变换）之外，
`lab` 增补执行面故障：`cancel`（用户取消 → reduced settle）、`duplicate_event`
（同收据二次投递 → dedup 合同）、`policy_deny`（bind gate/policy 拒绝）、
`resource_reject`（governor 准入拒绝分类）、`disconnect`（客户端断开优先
cancelled 语义）。每类绑定 fail-closed 断言：注入后必须出现指定的诚实劣化
或恢复证据，"静默 pass"即实验室红。生产路径零改动（结构保证默认关闭）。

**D4 — settlement 模式驱动生产结算 seam。** `lab/settlement.py` 用
`settle_turn_projections`（真实管线 + 边界替身）与 governor
admission/cancellation 驱动跨切面终态语义：cancel → 无 map_product 的
reduced settle + 幂等二结算；duplicate → 幂等门；policy deny → 拒绝分类不
伪造成功；resource reject → `NO_CAPABLE_WORKER` 族分类 + 预算尊重。与 F03
的终点×终态矩阵测试互补（那里钉 bridge 单路径语义，这里钉场景级跨切面合同）。

**D5 — 统一指标投影 + 诚实裁决。** `lab/metrics.py` 把各 adapter 输出投影到
九个核心指标：goal completion / GIS semantic correctness / cartographic
compliance / evidence completeness / recovery correctness / user-wins
violations / context-token cost / tool retries / wall-clock。每维裁决 ∈
{pass, fail, not_evaluated}：**任何 fail 即 spec fail**（不得用平均分掩盖
correctness failure）；声明了却没评出（declared-but-not_evaluated）同样使
spec 不绿。计时/字节数是 tolerant 观测行，绝不进 digest。

**D6 — 报告与基线。** `lab/report.py`：机器可读 JSON（逐 spec × 逐维裁决 +
指标 + 确定性 digest）+ 人读 markdown 摘要。基线对比报 digest 漂移；写基线
只经显式 `--write-baseline`（有红拒绝，`--force` 才放行），无任何自动"刷绿"。

**D7 — replay 多进程分片。** `replay/bench.py` 新增
`run_suite_multiprocess(procs≤N)`：场景按序切片，子进程跑切片报告（每片独立
`OfflineReplayer(seed)`），父进程按 scenario_id 归并。归并结果与单进程
**逐字节同 digest**（重放投影本身 session 无关：normalized_fingerprint 剥离
session 别名、run_token 只参与隔离不进 digest），由专项测试钉住。默认 worker
≤2，本地资源纪律。

**D8 — science oracles 惰性接入，不建 app→tests 静态边。** app 侧 lab 定义
adapter 协议与注册表；science 域的加载器在 `tests/science_oracles`（是测试资产），
由 CLI/tests 侧 adapter 经 importlib 惰性注入（与 replay 视觉裁判
`CARTO_VISUAL_JUDGE="module:callable"` env 缝同构）。app 不 import tests。

**D9 — e2e 真实 UI 主链（#1555）。** DatasetManager 的 upload 档接真实文件
选择器 → `lib/api/upload.ts uploadFile()`（POST /api/v1/upload multipart，
既有传输合同）→ attach 数据集；j1 上传腿改走真实 UI 交互（setInputFiles），
删除 `page.evaluate` API bypass；mock 档在 api-stubs 补 `/api/v1/upload`
有状态 stub。e2e 不新增任何 API 直打。

## 3. 禁止破坏的既有不变量

- `replay.Scenario/OfflineReplayer/bench` 的公共契约与 `tests/harness_replay/`
  全部既有测试（多进程是 additive 入口，单进程路径逐位不动）。
- `GISBenchmarkCase` schema additive-only；`app/evaluation/report.py`
  `render_markdown` 字节兼容。
- `settle_turn_projections` 词表（clean/cancelled/aborted/failed）与 reduced
  settle 语义；governor admission 决策契约。
- 生产代码零测试钩子：故障注入只发生在 lab/fake 边界与场景变换层。
- app → tests 无静态 import（science oracle 走惰性 spec 加载）。

## 4. 非目标（Out of Scope）

- 不做在线/分布式评测；不引入消息队列或外部编排器。
- 不重写 `apply_mutation`/`chat_stream` 巨石方法（#1544 另行处理）。
- 不替换前端 `llm-stub.mjs`（real 档 nightly 资产）；后端 lab fake 与其并存。
- 不把上传做成通用文件管理器（#1221 上传线仍归原方向）；本范围只要求
  主链上传入口真实存在且 e2e 走 UI。
