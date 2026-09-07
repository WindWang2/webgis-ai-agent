# ADR-0117: Algorithm Backend SDK V3 — 结构化资源包络、精度分类与不确定性产出闭环

status: Accepted
date: 2026-09-07
relates-to: ADR-0099 (GIS science contract backbone)、ADR-0083 (cost-aware
resolution)、docs/science/FOUNDATION_V3.md

## Context

science-v3 审计（.agent-work/science-v3/domains/08-registry-contracts.md）
确认基建的四类缺口：

1. **资源护栏散落**：`_MAX_IDW_OBSERVATIONS`、`RBF_HARD_CAP`、时维上限、
   pair 预算等散落各实现；backend_selection 只有 `estimated_bytes` 注释性
   诊断，没有声明式的 estimate-before-allocate 事实源。
2. **精度只有布尔**：`approximate: bool` 无法表达 exact 与 approximate
   变体共存，降级出窗时证据层无法结构化披露近似语义。
3. **容差不可消费**：`numerical_tolerance` 自由文本，数值验证框架无法
   机器读取 rtol/atol。
4. **不确定性声明无产出闭环**：`uncertainty_outputs` 只做词表成员校验，
   「声明了就必须真的产出并被测试断言」无机器可查约束。

## Decision

全部 additive 落在现有类型上，不新建第二 registry/resolver：

- **`ResourceEnvelope`**（algorithm_registry.py）：bytes_per_feature /
  bytes_per_cell / max_pairs / hard_max_features / hard_max_cells 声明位；
  `select_backend` 消费做结构化估算（字节、对预算超限、硬上限逼近 →
  resource_warnings），类型化硬闸仍在实现层（口径显式分离：声明面 vs
  执行面）。
- **`ApproximationClass`** 词表（exact/approximate/heuristic/sampling/
  streaming）：descriptor 级 + BackendVariant 级；validate() 强制与
  `approximate` 布尔交叉一致；选择层在所选路径非 exact 或出窗降级时
  产出 `approximation_disclosure`（不夸大科学等价性）。
- **`NumericalTolerance`**（rtol/atol/policy）：Wave 9 数值验证的机器
  可读锚；自由文本保留为人类口径。
- **`CancellationProfile`** 词表（none/coarse/chunk_boundary/fine）：
  算法对协作式取消的响应能力声明（诚实披露，不虚构取消点）。
- **`BackendEvidence`**：BackendDecision 的结构化证据类型（确定性键序），
  与 `to_diagnostic()` 的单条文本互补。
- **`uncertainty_producer_tests`**（Wave 8）：键 = uncertainty_outputs
  成员，值 = 真实产出并断言该不确定性类型的测试节点；validate() 用与
  conformance_tests 同款的文件+AST 级存在性校验闭环「declared → produced
  → tested」链。
- **Benchmark manifest**（scripts/gen_science_benchmark_manifest.py）：
  heavy 算法的声明面投影（复杂度/精度/包络/变体窗口/取消/容差），
  parity 测试锁定。

## Consequences

- 存量 171 算法零迁移：所有新字段缺省合法，validate() 只约束显式声明。
- manifest/catalog/runtime_manifest 指纹经既有生成链路自动获得新字段
  （approximation_class 进指纹投影；缺省值投影逐位不变，不误判 stale）。
- 后续新增重算法必须声明 resource_envelope + approximation_class +
  （有不确定性时）producer tests，否则 review 门不放行。
