# 08 Registry / Contracts / Backend / Performance 基建审计

> 审计人：主 agent（原派发 agent 因 API 限流失败，改由主 agent 直接审计）。
> 基线：origin/master = 16d1c70。日期：2026-09-07。

## 1. 基建 Census

| 模块 | 职责 | 行数 | 关键消费方 | 健康度 |
|---|---|---|---|---|
| app/lib/gis/algorithm_registry.py | AlgorithmDescriptor/BackendVariant 定义 + validate() | 514 | resolver、manifest、catalog gen、全部域包 | 良好；validate 覆盖广 |
| app/lib/gis/backend_selection.py | ScaleProfile → BackendDecision（纯函数） | 177 | 工具包装层 diagnostics、tests/benchmarks/test_backend_scale_decisions.py | 良好；缺 resource envelope 消费 |
| app/lib/gis/cost_model.py | 执行策略/成本分/规模分层/运行策略词表 | 175 | resolver、backend_selection | 良好 |
| app/lib/gis/capability_registry.py + capabilities/*.py | 能力层（输出 artifact 合同） | ~1300 | registry validate（A1 输出⊆能力门） | 良好 |
| app/lib/gis/parameter_contracts.py | 参数契约 + validate_parameters | 415 | 工具层 apply_contract、parity 测试 | 良好 |
| app/lib/gis/contract_validation.py | 输出契约校验 | 199 | 工具层 | 良好 |
| app/lib/gis/uncertainty.py | 类型化不确定性块 + 封闭词表（7 类） | 236 | 证据块、descriptor 校验 | 良好；缺 producer-test 机器校验 |
| app/lib/gis/scientific_evidence.py | ScientificEvidenceBuilder | 267 | 工具包装层 build_evidence | 良好 |
| app/lib/gis/scientific_errors.py | 类型化科学错误（14 类，含 ResourceScaleMismatch） | 169 | 全部实现层 | 良好 |
| app/lib/gis/scientific_preconditions.py | 17 固定 + 参数化前置 checker | 367 | resolver 硬门 | 良好；词表可扩 |
| app/lib/gis/method_references.py | 103 条方法引用 | 747 | descriptor validate | 良好 |
| app/lib/gis/runtime_manifest.py | compile-once 运行时快照 + 指纹 + stale guard | 478 | GIS harness、plan 重放 | 良好 |
| app/lib/gis/algorithm_resolver.py | capability→algorithm→tool 决策链 | — | planner | 良好 |
| app/lib/gis/artifacts.py | ArtifactTypeDescriptor（语义类型） | — | registry validate、cartography | 良好 |
| app/lib/cancellation.py | CancellationToken/OperationCancelled（协作式取消） | — | geocompute executor、jobs | 良好；**未与 descriptor 关联** |
| app/lib/geo_analysis/raster_guard.py | 栅格资源闸（estimate-before-allocate 的栅格特例） | — | 栅格工具 | 良好 |
| scripts/gen_science_catalog.py | catalog 生成（字节级 parity 测试锁死） | ~110 | tests/unit/gis/test_foundation_v2_infra.py | 良好 |
| scripts/gen_science_oracles.py | oracle 语料生成 → tests/science_oracles/ | — | oracle replay 测试 | 良好 |
| tests/benchmarks/ | 确定性 count/bytes 规模门 + scale guard 回归 | — | pytest -m perf | 良好（test_backend_scale_decisions 16 用例、test_spatial_science_benchmarks 13 用例） |

## 2. validate() 覆盖矩阵与缺口

已有检查：capability 存在性、输出 artifact ⊆ 能力声明（A1）、artifact 词表、native⇒tool_candidates、available_tools parity、fallback 存在性+语义双向、unit_requirements 词表、参数契约存在性/非空、method_references 存在性、preconditions 存在性、uncertainty_outputs 词表、random_seed_policy↔deterministic 一致性、backend 词表、PRODUCTION/VALIDATED/DEPRECATED 成熟度必要条件、conformance 节点 AST 级存在性。

**缺口**：
- G1：`uncertainty_outputs` 只校验"在词表内"，无 **declared uncertainty → producer test** 机器校验（声明了 `validation_metrics` 的算法没有强制存在一个实际产出 ValidationMetrics 的 conformance 测试）。
- G2：`approximate: bool` 无分类学（exact/approximate/heuristic/sampling/streaming），无法表达"exact 变体 + heuristic 变体共存"。
- G3：`numerical_tolerance` 是自由文本，机器不可消费（Wave 9 数值验证需要一个结构化 tolerance 供 golden 测试引用）。
- G4：无声明式 **ResourceEnvelope**（bytes/cell、pair budget、硬上限）。现有护栏散落在各实现（spatial_weights._MAX_IDW_OBSERVATIONS、rbf RBF_HARD_CAP、sar_temporal 时维上限、raster_guard…），无统一声明位；backend_selection 的 estimated_bytes 只是注释性诊断。
- G5：无 **CancellationProfile** 声明（哪些算法能在 chunk 边界响应取消）。取消原语已存在但 descriptor 不知道算法的可取消性。
- G6：`complexity` 自由文本无词表（如 "O(n log n)"），benchmark manifest 无法机器读取渐近风险。

## 3. Backend SDK 概念对照表（V3 目标 × 现状）

| V3 概念 | 现状 | 建议 |
|---|---|---|
| AlgorithmBackend | BackendVariant（id/backend/tool/deterministic/min/max_features） | additive 扩展字段，不换类型 |
| BackendVariant | ✅ 已有（≤4 变体/算法，窗口一致性校验） | 保持 |
| ScaleEnvelope | ScaleProfile + cost_model.scale_tier + 变体窗口 | 增强：变体窗口消费已完备；补 pair-budget 感知 |
| ResourceEnvelope | ❌ 缺声明位；散落实现层 guard | 新增 descriptor 字段 `resource_envelope`（结构化：bytes_per_feature/bytes_per_cell/pair_budget/hard_max_*），backend_selection 消费做 estimate-before-allocate 诊断 |
| ApproximationClass | ❌ 仅 approximate:bool | 新增 `approximation_class` 封闭词表字段（exact/heuristic/sampling/streaming/approximate），与 approximate 布尔一致性校验 |
| NumericalTolerance | 自由文本 | 新增结构化 `tolerance`（rtol/atol/policy），自由文本保留兼容 |
| DeterminismPolicy | ✅ random_seed_policy + deterministic | 保持（够用） |
| CancellationProfile | 取消原语存在，descriptor 无声明 | 新增 `cancellation_profile` 词表字段（none/coarse/chunk_boundary/fine） |
| OutputArtifactContract | ArtifactTypeDescriptor + 输出⊆能力门 | 增补 expected output size policy（bench manifest 用） |
| BackendEvidence | BackendDecision.to_diagnostic() 进证据块 | 增强：降级出窗时披露 approximation 语义 |

## 4. Uncertainty 体系现状与缺口

- 词表 7 类：scalar/field/raster/statistical_significance/sensitivity_envelope/validation_metrics/monte_carlo_summary。**覆盖了任务要求的 analytical variance（scalar variance measure）、bootstrap（method 字段）、permutation（statistical_significance）、CV residual（validation_metrics）、Monte Carlo、prediction/confidence interval（UncertaintyMeasure.measures）、sensitivity envelope**。
- 缺：**approximation uncertainty** 类（近似后端引入的额外不确定性的显式披露槽位）—— 建议 additive 新词 `approximation_disclosure`。
- 缺：G1 所述 producer-test 校验。

## 5. Evidence/Trace 体系现状

ScientificEvidenceBuilder 覆盖 capability/algorithm/version/tool/契约/参数/输入/假设/局限/变换/fallback（含科学等价性）/警告/诊断/不确定性/验证/复现。BackendDecision 经 to_diagnostic 进 diagnostics。**缺口**：backend 降级（matched=False）时证据文本有理由但无结构化 approximation 字段（随 §3 BackendEvidence 增强解决）。

## 6. Benchmark manifest 现状与缺口

现有：tests/benchmarks/{test_backend_scale_decisions,test_spatial_science_benchmarks}.py —— 类型化拒绝 + count/bytes 结构门，monkeypatch 收紧常数保持测试快速。**缺口**：无按算法的机器可读 manifest（渐近风险/最大规模/pair budget/后端切换阈值/取消间隔/输出尺寸策略）—— G4/G6 解决后可由 registry 投影生成（沿用 gen_science_catalog 模式，避免手工漂移）。

## 7. Cancellation/Resource guard 现状

CancellationToken 贯穿 geocompute executor（deadline 从调度起算、checkpoint 落节点）。实现层有 ResourceScaleMismatch/RasterResourceExceeded。**缺口**：lib 级重循环算法（kriging 矩阵求逆、GLCM、viewshed 射线）多数无取消点（CancellationToken 未下沉到 geo_analysis 层签名）—— 这是真实缺口，但**逐函数加 token 参数是本 wave 的可选深化**；最低成本方案：descriptor 声明 CancellationProfile（诚实披露可取消性），重循环实现按 profile 在 chunk 边界检查 token（新增算法默认支持，存量算法按域逐步补）。

## 8. Catalog 生成链路与 parity

gen_science_catalog.py 按 capability 排序投影全部 descriptor → docs/science/ALGORITHM_CATALOG.md；test_foundation_v2_infra.py 字节级 parity 锁死。**结论：文档目录一律走生成器，新字段（resource_envelope 等）需要同步扩展生成器，否则 parity 测试失败提示。** gen_science_oracles.py 同理（oracle JSON 已 gitignore-negation 全部入库）。

## 9. 架构风险与建议

- **单一事实源保护**：不新建 resolver/registry。所有 V3 SDK 概念落在现有文件（algorithm_registry.py 新字段 + backend_selection.py 新消费 + uncertainty.py 新词），manifest/catalog 经既有生成链路自动获得。
- runtime_manifest 是运行时权威投影；descriptor 新字段若进 manifest 投影需同步 fingerprint 投影（canonical JSON 键序），测试会捕获漂移。
- validate() 新校验必须是 additive：只对**新声明**的字段提出新要求，不给存量 171 算法制造迁移负担（向后兼容：字段缺省合法）。

## 10. Wave 1 落地设计（建议采纳）

1. `algorithm_registry.py`：
   - 新词表 `APPROXIMATION_CLASSES = {exact, approximate, heuristic, sampling, streaming}`（descriptor.approximation_class，默认 "" = 未声明，向后兼容）；
   - 新 `ResourceEnvelope(BaseModel)`：bytes_per_feature/bytes_per_cell/pair_budget_multiplier/hard_max_features/hard_max_cells（全 Optional，有界校验）；
   - 新 `NumericalTolerance(BaseModel)`：rtol/atol/policy_note（替代自由文本的机器可读层）；
   - 新词表 `CANCELLATION_PROFILES = {none, coarse, chunk_boundary, fine}`（descriptor.cancellation_profile）；
   - validate() additive 规则：approximation_class 声明时 approximate 布尔一致性；ResourceEnvelope 数值合法性；cancellation_profile 词表；tolerance 数值范围。
2. `backend_selection.py`：select_backend 消费 ResourceEnvelope → estimated_bytes 结构化计算（estimate-before-allocate 诊断进 BackendDecision）；降级出窗（matched=False）时 rationale 显式 approximation 披露；新增 `BackendEvidence` dataclass（variant/backend/scale_tier/approximation_disclosure/resource_estimate/tolerance）附 to_diagnostic。
3. `uncertainty.py`：词表 additive 追加 `approximation_disclosure` + 对应块类型。
4. manifest/catalog 生成器扩展投影（防漂移由既有 parity 测试保证）。
5. 测试：test_algorithm_registry_phase2.py 增补新字段校验用例；test_backend_scale_decisions.py 增补 envelope 消费用例。
