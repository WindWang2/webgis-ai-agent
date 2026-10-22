# ADR 重编号映射表（issue #1679）

`docs/adr/` 曾出现 19 个编号被 2–6 篇决策共用（共 62 篇卷入），散文里的 `ADR-NNNN` 因此失去可解析性。本表记录 **旧 → 新** 全量映射，供人工审阅；重命名用 `git mv` 执行（历史可追），全部散文引用按上下文语义消歧后同步更新。

## 裁决规则

1. **主文件（保留原号）**：同一撞号组内**全仓散文引用数最多**的候选（引用数含 docs/、app/、frontend/、tests/、scripts/、planning/、CHANGELOG.md；不含 `.goal-loop-ledger.md` —— 该账本按仓库纪律不在本单改动范围）。并列时依次看：docs/adr 内互相引用数 → git 落库时间 → 主题核心性。
2. **其余候选**：按「组号升序 + 组内文件名升序」从 **0231** 起顺序分配未占用新编号（共 43 篇，0231–0273）。文件名 slug 保持不变，仅换编号前缀。
3. **引用消歧**：每处 `ADR-NNNN` 引用按 ±110 字符上下文（引用文件路径 + 主题关键词 + D/F/Wave 编号 + 模块域）判定其指向的决策；判定为主文件的引用不动，指向非主文件的改写为新编号。人工逐条复核了全部无法自动判定的残差（见下「已知残留」）。
4. **Interaction IR 半成品重编号收尾**：`0225→0227` 只改了一半 —— 前端 `interaction/outbox{,.test}.ts` 与 `planning/goal-02-interaction-ir/WORKLOG.md` 共 4 处仍注释 `ADR-0225`（现指向 RenderWork Protocol），本单一并改为 `ADR-0227`。

## 映射表

| 旧编号 | 旧文件名 | 决策主题 | 处置 | 新编号 | 新文件名 | 引用数 |
|---|---|---|---|---|---|---|
| 0077 | `0077-multi-pod-topology-turn-ownership.md` | Multi-Pod Topology & Ingress Sticky Sessions for Pi Turn Ownership | 保留原号 | 0077 | `0077-multi-pod-topology-turn-ownership.md` | 4 |
| 0077 | `0077-wrap-vendored-pi.md` | Wrap vendored Pi; do not fork the core | 改号 | 0231 | `0231-wrap-vendored-pi.md` | 2 |
| 0088 | `0088-autonomous-gis-product-runtime.md` | Autonomous GIS Product Runtime（统一动作 / 确定性 Runtime 修复 / 血缘最小重计算） | 保留原号 | 0088 | `0088-autonomous-gis-product-runtime.md` | 26 |
| 0088 | `0088-cartographic-component-library-v2.md` | Cartographic Component Library 2.0 | 改号 | 0232 | `0232-cartographic-component-library-v2.md` | 13 |
| 0088 | `0088-gis-data-analysis-runtime-v2.md` | GIS Data Fabric & Analysis Runtime V2 | 改号 | 0233 | `0233-gis-data-analysis-runtime-v2.md` | 1 |
| 0094 | `0094-enterprise-geospatial-data-fabric-v2.md` | Enterprise Geospatial Data Fabric V2 | 保留原号 | 0094 | `0094-enterprise-geospatial-data-fabric-v2.md` | 105 |
| 0094 | `0094-spatial-decision-intelligence-v3.md` | Spatial Decision Intelligence V3 | 改号 | 0234 | `0234-spatial-decision-intelligence-v3.md` | 2 |
| 0096 | `0096-geocompute-data-plane-v3.md` | GeoCompute & Data Fabric V3 — Unified Execution Plane | 保留原号 | 0096 | `0096-geocompute-data-plane-v3.md` | 66 |
| 0096 | `0096-agent-product-plane-vnext.md` | Agent/Product Plane VNext boundary | 改号 | 0235 | `0235-agent-product-plane-vnext.md` | 3 |
| 0099 | `0099-spatial-science-geoai-platform-vnext.md` | Spatial Science & GeoAI Algorithm Platform VNext | 保留原号 | 0099 | `0099-spatial-science-geoai-platform-vnext.md` | 123 |
| 0099 | `0099-map-product-lifecycle-v2.md` | Map Product lifecycle V2 — open / restore / fork / rerun / merge / auto-record | 改号 | 0236 | `0236-map-product-lifecycle-v2.md` | 40 |
| 0101 | `0101-geocompute-data-fabric-v4.md` | GeoCompute & Data Fabric V4 — Distributed Execution & Data Foundation | 保留原号 | 0101 | `0101-geocompute-data-fabric-v4.md` | 108 |
| 0101 | `0101-agent-tool-model-runtime-v2.md` | Agent Tooling & Model Runtime Foundation V2 | 改号 | 0237 | `0237-agent-tool-model-runtime-v2.md` | 62 |
| 0101 | `0101-cartographic-template-library-v3.md` | Cartographic Template & Component Library V3 | 改号 | 0238 | `0238-cartographic-template-library-v3.md` | 77 |
| 0101 | `0101-geoworkflow-recipe-conformance-foundation.md` | GeoWorkflow Recipe & Conformance Foundation | 改号 | 0239 | `0239-geoworkflow-recipe-conformance-foundation.md` | 16 |
| 0103 | `0103-pi-gis-runtime-v3.md` | Pi GIS Runtime V3 — Dynamic Tool Platform, Model Routing, Context Budget, Trace/Replay | 保留原号 | 0103 | `0103-pi-gis-runtime-v3.md` | 92 |
| 0103 | `0103-cartographic-design-system-v4.md` | Cartographic Design System V4 | 改号 | 0240 | `0240-cartographic-design-system-v4.md` | 6 |
| 0103 | `0103-data-artifact-workspace-foundation-v3.md` | GIS Data / Artifact / Workspace Foundation V3 | 改号 | 0241 | `0241-data-artifact-workspace-foundation-v3.md` | 6 |
| 0104 | `0104-gis-harness-autonomous-runtime-v4.md` | GIS Harness V4 — Autonomous Spatial Reasoning & Execution Runtime | 保留原号 | 0104 | `0104-gis-harness-autonomous-runtime-v4.md` | 129 |
| 0104 | `0104-data-control-geocompute-platform-v5.md` | Data Control Plane V4 + GeoCompute V5 + Durable Workspace | 改号 | 0242 | `0242-data-control-geocompute-platform-v5.md` | 4 |
| 0104 | `0104-gis-extension-platform-v1.md` | GIS Extension Platform V1 — Plugin / SDK / OGC Integration Ecosystem | 改号 | 0243 | `0243-gis-extension-platform-v1.md` | 52 |
| 0104 | `0104-professional-cartography-workbench-v4.md` | Professional Cartography Workbench V4（统一工作台投影与模式化工作区） | 改号 | 0244 | `0244-professional-cartography-workbench-v4.md` | 11 |
| 0104 | `0104-quality-reliability-platform-v1.md` | Quality & Reliability Platform V1 | 改号 | 0245 | `0245-quality-reliability-platform-v1.md` | 62 |
| 0105 | `0105-gis-extension-platform-v2.md` | GIS Extension Platform V2 — 隔离执行 / 能力代理 / 供应链 | 保留原号 | 0105 | `0105-gis-extension-platform-v2.md` | 63 |
| 0105 | `0105-workbench-v5-collaboration.md` | Professional GIS Workbench & Collaboration V5 | 改号 | 0246 | `0246-workbench-v5-collaboration.md` | 6 |
| 0118 | `0118-cartographic-rendering-v5.md` | Professional Cartographic Rendering V5 | 保留原号 | 0118 | `0118-cartographic-rendering-v5.md` | 77 |
| 0118 | `0118-federated-spatial-query-optimizer-v6.md` | Federated Spatial Query Optimizer V6 | 改号 | 0247 | `0247-federated-spatial-query-optimizer-v6.md` | 39 |
| 0118 | `0118-gis-harness-autonomous-runtime-v5.md` | GIS Harness Autonomous Runtime V5 — 可恢复、可诊断、渲染可证 | 改号 | 0248 | `0248-gis-harness-autonomous-runtime-v5.md` | 27 |
| 0118 | `0118-quality-reliability-security-platform-v2.md` | Quality, Reliability & Security Platform V2 | 改号 | 0250 | `0250-quality-reliability-security-platform-v2.md` | 9 |
| 0118 | `0118-semantic-workflow-compiler-v4.md` | Semantic Workflow Compiler V4 | 改号 | 0251 | `0251-semantic-workflow-compiler-v4.md` | 7 |
| 0118 | `0118-spatial-data-lakehouse-cube-v6.md` | Spatial Data Lakehouse & Cube V6 | 改号 | 0249 | `0249-spatial-data-lakehouse-cube-v6.md` | 45 |
| 0180 | `0180-pi-native-harness-kernel-sessionplan.md` | Pi-native GIS Harness Kernel + host-neutral SessionPlan 契约 | 保留原号 | 0180 | `0180-pi-native-harness-kernel-sessionplan.md` | 79 |
| 0180 | `0180-gis-situation-world-model.md` | GIS Situation / World Model v1 —— 结构化会话情境 | 改号 | 0252 | `0252-gis-situation-world-model.md` | 42 |
| 0180 | `0180-pi-typed-tool-surface-hardening.md` | Pi Typed Tool Surface Hardening — Surface Byte Budget, Pre-dispatch Input Gate, Surface Metrics | 改号 | 0253 | `0253-pi-typed-tool-surface-hardening.md` | 33 |
| 0182 | `0182-gis-skill-procedure-library-v1.md` | GIS Skill / Procedure Library V1 —— 领域作业方法知识层 | 保留原号 | 0182 | `0182-gis-skill-procedure-library-v1.md` | 73 |
| 0182 | `0182-harness-resource-governor-v1.md` | Harness 资源治理 Governor V1 —— 跨域准入、预算、背压与资源感知执行 | 改号 | 0254 | `0254-harness-resource-governor-v1.md` | 47 |
| 0183 | `0183-harness-goal-satisfaction-evaluator.md` | Harness-level Goal Satisfaction Evaluator | 保留原号 | 0183 | `0183-harness-goal-satisfaction-evaluator.md` | 39 |
| 0183 | `0183-gis-spatial-reasoning-memory.md` | GIS Spatial Reasoning Memory v1（方向 9） | 改号 | 0255 | `0255-gis-spatial-reasoning-memory.md` | 34 |
| 0183 | `0183-harness-replay-benchmark-explainability.md` | Harness Replay + Benchmark + Explainability——轨迹级回归体系 v1 | 改号 | 0256 | `0256-harness-replay-benchmark-explainability.md` | 24 |
| 0183 | `0183-mutation-transactions-v1.md` | Agent–User–Map Mutation Transaction System V1 | 改号 | 0257 | `0257-mutation-transactions-v1.md` | 33 |
| 0183 | `0183-semantic-map-product-graph-v1.md` | Semantic Map Product Graph V1 — 语义产品层（MapProductSpec / 视图关系 / 产品编译器 / 语义完整性） | 改号 | 0258 | `0258-semantic-map-product-graph-v1.md` | 28 |
| 0204 | `0204-capability-abi-v2-binding-conformance.md` | Capability ABI v2 — 绑定收敛、Conformance 闸与 Dispatch 证据 | 保留原号 | 0204 | `0204-capability-abi-v2-binding-conformance.md` | 50 |
| 0204 | `0204-f03-harness-lifecycle-convergence.md` | Harness Lifecycle Convergence —— canonical lifecycle 成为生产唯一生命周期语言 | 改号 | 0259 | `0259-f03-harness-lifecycle-convergence.md` | 21 |
| 0214 | `0214-composition-contract-component-abi.md` | Composition Contract — versioned Component ABI 与模板组合契约基座 | 保留原号 | 0214 | `0214-composition-contract-component-abi.md` | 60 |
| 0214 | `0214-map-plan-compiler.md` | Map Plan Compiler — MapPlanIR → 有序最小 MapSpec Mutations | 改号 | 0260 | `0260-map-plan-compiler.md` | 34 |
| 0214 | `0214-mapspec-render-runtime-data-plane.md` | MapSpec → Render Runtime & Data Plane 事务语义 | 改号 | 0261 | `0261-mapspec-render-runtime-data-plane.md` | 41 |
| 0214 | `0214-trace-replay-oracle-v3.md` | Trace / Replay Oracle v3 — 从可回放到可判错的执行级回归基座 | 改号 | 0262 | `0262-trace-replay-oracle-v3.md` | 59 |
| 0214 | `0214-visual-observation-repair-provider.md` | 生产级视觉观察/批评/修复 Provider —— 观察契约、taxonomy 归一、跨域融合与用户批准修复闭环 | 改号 | 0263 | `0263-visual-observation-repair-provider.md` | 37 |
| 0214 | `0214-workflow-resource-scheduler-convergence.md` | Workflow Resource Scheduler Convergence（节点级 ResourceEstimate 与背压） | 改号 | 0264 | `0264-workflow-resource-scheduler-convergence.md` | 49 |
| 0215 | `0215-context-revalidation-safe-reuse.md` | Evidence-Backed Context Revalidation & Safe Reuse | 保留原号 | 0215 | `0215-context-revalidation-safe-reuse.md` | 65 |
| 0215 | `0215-gis-dataset-semantic-contract.md` | GIS Dataset Semantic Contract（GISDatasetDescriptor vNext） | 改号 | 0265 | `0265-gis-dataset-semantic-contract.md` | 36 |
| 0215 | `0215-gis-intent-requirement-ir.md` | GIS Intent & Requirement IR（需求中间表示） | 改号 | 0266 | `0266-gis-intent-requirement-ir.md` | 19 |
| 0215 | `0215-worker-placement-envelope-and-inline-admission.md` | Worker Placement Envelope 投影与 Inline Scale 准入 | 改号 | 0267 | `0267-worker-placement-envelope-and-inline-admission.md` | 2 |
| 0216 | `0216-architecture-boundaries-contract-kernel.md` | 架构边界与共享 Contract Kernel（app/contracts 归位） | 保留原号 | 0216 | `0216-architecture-boundaries-contract-kernel.md` | 50 |
| 0216 | `0216-durable-turn-journal.md` | Durable Harness Turn Journal（会话级可恢复执行账本） | 改号 | 0268 | `0268-durable-turn-journal.md` | 25 |
| 0216 | `0216-subagent-delegation-lease-protocol.md` | Subagent Delegation Lease Protocol（可取消、可追因、fail-closed 的委派基座） | 改号 | 0269 | `0269-subagent-delegation-lease-protocol.md` | 13 |
| 0217 | `0217-unified-gis-action-ir.md` | Unified GIS Action IR（统一执行语义中间表示） | 保留原号 | 0217 | `0217-unified-gis-action-ir.md` | 24 |
| 0217 | `0217-large-data-plane-vnext.md` | 大数据数据平面 vNext — 目录瓦片服务化、窗口分页与断连真取消 | 改号 | 0270 | `0270-large-data-plane-vnext.md` | 1 |
| 0217 | `0217-situational-memory-graph-recompute.md` | Situational Memory Graph — Dependency-Anchored Recompute（H09） | 改号 | 0271 | `0271-situational-memory-graph-recompute.md` | 1 |
| 0217 | `0217-visual-observation-blob-lifecycle-and-safe-repair.md` | 视觉观测证据生命周期与安全修复闭环（C13） | 改号 | 0272 | `0272-visual-observation-blob-lifecycle-and-safe-repair.md` | 5 |
| 0228 | `0228-cartographic-product-manifest-v1.md` | Cartographic Product Manifest V1 —— live↔export 语义一致性证据与制图产品编译一致性 | 保留原号 | 0228 | `0228-cartographic-product-manifest-v1.md` | 10 |
| 0228 | `0228-ci-toolchain-no-runner-image-drift.md` | CI 工具链不得依赖 runner 镜像预装版本（#1596 方向 1 留档） | 改号 | 0273 | `0273-ci-toolchain-no-runner-image-drift.md` | 1 |

## 引用改写统计

- `ADR-NNNN` 引用：主文件引用保持原号；非主文件引用改写为新编号（撞号引用改写 1076 处 + Interaction IR 收尾 4 处）。
- `docs/adr/NNNN-slug.md` 路径引用同步改号（111 处）；另修复 CHANGELOG 内 2 处既存悬空旧路径（0204 世代遗留）。
- CHANGELOG 内「ADR-0204 世代」历史条目按分支语义解析到重编号后的 ADR（map-verify-repair→0209、cartographic-grammar→0205、trace-replay 闭环 v2→0212），依据 `docs/dev/f15-visual-observation-recon.md` 记载的重编号事实。

## 已知残留（本单刻意不改，需后续处理）

1. **F06 候选号悬空**：`feat/f06-capability-situation-credentials`（PR #1511，RuntimeSituation 生产供给 / 凭证桥 / 分歧治理 / 关系词表 v2）以「ADR-0215 候选」编号落地，但从未落 ADR 文件；其约 20 处代码/文档注释仍写 `ADR-0215`。0215 主文件为 F05（context-revalidation），这些引用改指任何现存 ADR 都是错的，故保留原样 —— 后续应为 F06 补写独立 ADR 或把注释改指 `docs/dev/f06-capability-situation-credentials-design.md`。
2. **`.goal-loop-ledger.md`**：按仓库纪律不在本单改动范围，其中 f08/f09/f11 等行仍写 `ADR-0214`（现指向 Composition Contract）。
3. **历史编号轶事**：`docs/dev/harness-goal-evaluator-decisions.md`、`tests/unit/test_pi_bridge_leak.py` 的「ADR-0180-0184」校准史、`app/lib/integration/{adr,merge_sim}.py` 的「6×ADR-0118 撞号」事故记录、recon 文档里的「ADR-0204 世代」均为历史陈述，数字仍可解析到主文件，不改；`docs/dev/execution-graph-recon-subagent-a.md` 的「0180 ×3 占号实录」按当时文件名记载（对应现名 0252/0253/0180-kernel），同属历史记录不改。
4. **0130 / 0109 / 0115 等历史悬空编号**：属更早批次重编号的存量（CHANGELOG 已记载「five-way renumber」），与本单无关，未处理。
