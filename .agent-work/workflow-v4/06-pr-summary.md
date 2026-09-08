# Workflow V4 — PR Summary

## 1. Problem / Motivation

现有 Semantic Workflow / Recipe Foundation V3 已建立 Recipe DSL V2、GIS 任务本体 V3（47 任务/9 域）与 15 阶段确定性编译器，但 Phase-0 审计（00-baseline.md）证实三个核心缺口：

1. **编译器 evaluation-only**：生产 chat 链走两段式 planner，编译器证据（本体匹配/资格裁决/候选 trace/回退层）生产不可见（ADR-0104:15 自认）。
2. **DAG 无类型**：capability_dag 是字符串投影，无 typed ports / artifact / CRS / 单位语义。
3. **方法论无一等模型**：无方法族→候选方法→资格排序契约；义务无组合继承；无包版本/受影响子图/语义 diff。

## 2. Current-state audit

见 `00-baseline.md`（164 recipes / 152 families / 12 composites / 7 scenarios / 46 tasks / 121 capabilities / 181 algorithms / 21 artifact types 实测清单 + 11 项审计问题逐条证据）。

## 3. Architecture

见 `01-architecture.md` 与 `docs/adr/0118-semantic-workflow-compiler-v4.md`。

核心决策：V4 = 编译期语义层（决定"应该做什么/为什么/候选方法/义务"），执行仍归 Harness runtime。10 项关键决策（qualify 排序取代任意选择、义务继承幂等语义、包 semver + immutable compiled form、参数 provenance、受影响子图正向闭包、获取只声明不抓取、制图表达-资格义务、证据上行生产接入、中央校验收编、不落库）。

## 4. Implementation waves（13 waves / 12 commits）

| Wave | 内容 |
|---|---|
| 1+2 | 12 方法族 + 44 候选方法 + 资格排序引擎 |
| 3 | Typed workflow DAG（typed ports：artifact/几何/crs_class/单位） |
| 4 | Compiler V4 additive 管线（15 阶段零漂移 + V4 阶段） |
| 5 | 义务继承链（provenance/幂等/最强语义） |
| 6 | WorkflowPackage（semver + sha256 指纹 + 兼容性） |
| 7 | 参数解析 + provenance（不阻塞人工） |
| 8 | 受影响子图（正向闭包，style 零科学重算，recipe 全失效） |
| 9 | 语义 diff（→ WorkflowChange 直接喂 recompute） |
| 10 | 获取备选声明（synthetic 仅显式）+ 制图表达义务 5 规则 |
| 11 | 双语方法语料（12 族 × zh/en × 歧义/负例）+ 五维编译器评估 + 专业词族解析 |
| 12 | 生产接入：compile_workflow_semantics 工具 + Plan.workflow_v4 证据（线程卸载） |
| 13 | ADR-0118 + 文档 |
| R1/R2 | 两轮独立 review 修复（见下） |

## 5. Key code paths

- `app/services/gis_harness/workflow_v4/`（11 模块，Epic ownership）
- `app/evaluation/methodology_corpus.py`（审定语料）
- `app/tools/semantic_tools.py::compile_workflow_semantics`（sync → THREAD 策略）
- `app/services/chat/plan_orchestrator.py::_compile_v4_evidence`（to_thread 卸载）
- `app/services/gis_harness/registry_validation.py`（methodology 四谓词对账收编）
- `app/services/gis_harness/plan_candidates.py`（DATA_FIT_SCORE 公共别名）
- `app/services/gis_harness/data_qualification.py`（geometry_category 公共别名）

## 6. Data / persistence changes

**无 DB migration**。V4 是编译期契约层：packages/diff/recompute 为可重建纯函数产物；运行态仍归 workflow_instance（会话章）。避免 Alembic head 冲突（10 个并行 Epic 隔离红线）。

## 7. API / contract changes

- 既有公开 API **零签名变化**；COMPILER_STAGES（15 阶段）契约零改动（测试锁定）；基线文件 workflow_compiler.py / workflow_schema.py / gis_ontology.py / app/lib/gis/** 零改动（review 核对 git diff 为空）。
- 新增：`compile_workflow_v4()`、`WorkflowCompilationV4`、workflow_v4/ 全部公开函数、`compile_workflow_semantics` agent 工具、`Plan.workflow_v4` additive dataclass 字段（默认 None，恢复路径不读）。

## 8. UI changes

无（渲染面消费 workflow_v4 证据为 follow-up；当前为审计/LLM 工具面可见）。

## 9. Security implications

- 工具 query pydantic 400 上限；profile 注入面全为类型检查只读访问，异常收敛 `{"success": False}`；
- CPU 编译离事件循环（THREAD 策略 / to_thread），无 loop 阻塞 DoS 面；
- 证据仅含注册表派生内容 + 指纹，无 session 数据/凭据；
- 指纹 = canonical JSON sha256；semver fullmatch 严格解析无 ReDoS。

## 10. Performance baseline / results

- 编译冷启动 ~0.3s（含 15 阶段 + planner memo miss）；memo 命中复编译有 `second ≤ first×1.5` 回归锁定；
- 结构性预算测试锁定：15+8 阶段、产物 <64KB、DAG ≤64 节点/≤128 边、包体积运行时强制；
- 全语料评估（37 表述 × 双编译）实测 ~2.2s < 5s 门禁；
- recompute/diff O(V+E)/O(N log N)；registry 全进程单例。

## 11. Local test matrix（精确结果）

- V4 新测试 **75 个全绿**（9 文件：methodology 14 / typed_dag 6 / compiler_v4 7 / obligations+package 10 / semantics 19 / corpus 9 / production 6 / budget 4 / central validation 2 —— 实数以合并时 CI 输出为准）；
- `tests/unit/gis_harness/` 全量 **922 passed**；
- 外批回归：plan_orchestrator 17 + semantic_gis_intelligence + reproducible_gis_runtime 26 = 64 passed；
- 语料评估 37/37 五维全绿（族正确/数据完备/无效方法拒绝/义务完备/确定性+可解释）；
- `ruff check app/ tests/` All checks passed；
- `validate_gis_library()` 0 issues（methodology 四谓词对账收编后）；
- V4 模块覆盖率 89-100%（repo 门 75%）。

## 12. Review Round 1 findings/fixes

6 MAJOR + 10 MINOR，全部修复（d320ed65）：角色词表统一（重放资格评估器）、REQUIRES_TRANSFORM 软化为四态、primary_output 悬空 + 校验器、零入度节点连通性兜底、diff target 真实节点投影 + parameter/edge diff、node.parameters 注入（参数重算生产可达）；详见 05-review-findings.md。

## 13. Review Round 2 findings/fixes

1 MAJOR + 9 MINOR，全部修复（c26df354）：事件循环阻塞（sync def THREAD + to_thread）、tool/orchestrator 披露对齐、包体积运行时强制、integer 严格校验、validate 死分支、orchestrator 输入界、memo 收益断言 + 真异常注入、ADR 计数、dem 路由词边界；详见 05-review-findings.md。

## 14. Rebase / integration verification

`git fetch origin` 后 origin/master 无新提交（445ad30e，0 behind）；rebase 恒等（"当前分支是最新的"）；rebase 后复测 gis_harness 922 绿 + ruff 全绿。共享文件（CHANGELOG/workflows/ci-local/OpenAPI snapshot/registries）零改动或纯加法（registry_validation 加法校验块），并行 Epic 冲突面最小。

## 15. Backward compatibility

- 不启用 V4 时生产行为零变化（15 阶段 + planner 链路零改动）；
- 语义工具失败/未注册 → agent 面回落既有工具；
- Plan.workflow_v4 失败 → None，规划不受阻；
- V1 seed recipe（无 workflow profile）在 V4 编译下走解析态映射，unknown 中性诚实语义。

## 16. Known limitations

1. Plan.workflow_v4 为会话内易失证据（与 gis_intent 同模式），跨重启审计需重编译（指纹可验证）。
2. 规划期 profile 缺席时资格裁决为 unknown 中性态（qualification_basis 披露），数据到位后由 finalize/profile 通道重评。
3. subworkflow 节点/多源义务继承（composite 经由编译管线的真实组合）已建模但无生产调用方——Harness V5 runtime 消费为 follow-up。
4. 语料义务维度目前以恒定义务为主（7 例），composite 义务链进语料为 follow-up。
5. 像素级制图验证诚实不做（归 Cartography V5）。

## 17. Follow-up candidates

- Harness V5 runtime 消费 typed DAG + RecomputePlan（部分重算调度/产物复用校验）。
- renderer/API 消费 Plan.workflow_v4 证据（blocked 态用户可感）。
- composite/nested 义务链经编译管线的端到端组合 + 语料扩充。
- workflow package semver 推进的端到端演练（版本晋升工作流）。
- profile 尺寸/深度界（工具注入面加固）。

## 18. CI 说明

本任务按要求**不等待/不依赖线上 CI/CD**，全部验收以本地静态检查、针对性单测、集成测试、性能预算与端到端确定性验证为准（见 §11）。
