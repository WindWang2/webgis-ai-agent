# DECISIONS — 关键决策记录（含 ADR 引用）

## D1. 词表扩展走 QualityIssueCode 受控新增（不建第五套枚举）
先例：docstring「扩展需受控新增枚举值」；import 期守卫纪律（rules.py:240、repair_planning.py:250）。
新增 4 码：`timezone_missing`、`unit_ambiguous`、`field_role_ambiguous`、`admin_mismatch`。
UNIT_AMBIGUOUS ≠ INCONSISTENT_UNIT：前者=单位欠定（无可判定证据），后者=名实矛盾（名称提示与值域冲突）。两者并存，映射各自提案。

## D2. 检测器放 services 层（`app/services/data_quality/semantic_checks.py`），词表放 lib 层
lib 不 import services（分层纪律）。timezone/unit 检查挂 lib 的剖析证据路径（`_field_checks` 同级）可以保持纯剖析证据；admin/role 检查需要 guardrails 码表与 semantic_profile（services 可 import lib 与 guardrails substrate）→ 放 services。统一入口 `evaluate_semantic_checks(...) → List[QualityIssue]`（复用 lib 的 QualityIssue 模型，不造第二 issue 类型）。

## D3. 低置信角色闸放 `data_qualification`（五态语义内），不放 qualification_v8
理由：workflow 路径已有五态语义与回归锁；measure/denominator 的角色置信是**数据资格**语义而非 capability 资格语义。additive 可选参数 `semantic_profile`，缺省 None → 零行为变化（feature-off 即向后兼容）。低置信 → degraded + `FIELD_ROLE_AMBIGUOUS` reason + clarify 型 remediation（auto_applicable=False），**绝不** eligible、**绝不**静默绑定；无语义画像时维持现状事实（unknown ≠ unsatisfied 红线不破坏）。

## D4. V8 接线走 QualificationContext additive 字段（ADR-0181 先例）
新增 `quality_gate`/`blocking_issue_codes`（缺省零变化）。实现时细化的映射：`gate=blocked`（不可修复质量阻断）→ INELIGIBLE（硬失格，结构化 reason 携带阻断码）；`gate=degraded` → DEGRADED（软降级，且不掩盖节点既有硬失格 reason）；`ready/unknown` → 零增量（unknown ≠ 不满足红线）。`build_situation` 透传质量面（生产接缝）。`capability_descriptors.check_preconditions` 的 bool 返回不动（低置信信息不硬塞 bool 接口）。

## D5. 修复事务 = 既有件的状态机编排（无新执行路径）
`RepairSession`：`plan_id`+`source_digest` 确定性派生 session 键；状态 proposed→dry_run→applied→verified | failed | rolled_back。dry_run 复用 `dry_run_autofix`；apply 复用 `execute_repair`（新 ref 语义 = 失败零残留的结构性保证：源载荷 deepcopy、产物只进新 ref）；verify 复用 `evaluate_payload`/`run_quality_checks` 复评残留；rollback = 经 `ArtifactGraph.replacement_chain` 语义登记回指 source 的 superseding ref + 血缘事件（append-only，不物理回滚）。**原 artifact 永不被改写**（Oracle：修复失败不破坏原 artifact 由 deepcopy+新 ref 双保险）。

## D6. admin 检测 = guardrails 基底的值域投影（不复制码表）
import `spatial_guardrails/admin_division_verifier` 的码表/最近码函数；输出数据质量 issue（`admin_mismatch`）+ 映射建议（RemediationStep 形状，declare/normalize 类），不改 guardrails 本身的层级校验与红线。guardrails 仍拥有「层级父子关系校验」；本方向只做「数据集字段值能否对上已知行政区」这一质量事实。

## D7. 有界性/确定性沿用既有单点
样本 ≤MAX_VALUE_SAMPLES(200)；issues 上限沿用 QualityReport(_MAX_ISSUES=32)；session/plan/digest 排除墙钟；检测器纯函数、同输入同输出；E2E Oracle 要求连跑两遍 digest 一致。

## D8. 性能口径
10 万行级：检测器只吃剖析样本与聚合事实（O(fields×samples)），不物化全量；E2E 用生成式 100k-row fixture 验证 profile 路径耗时与内存有界，本地数字仅记录环境不冒充 SLO。
