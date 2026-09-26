# F10 — Cartographic Grammar Production Adoption · 独立 Review 记录

- 评审：Subagent C（独立评审员，未参与实现），对象 `origin/master(9e1ad229)...HEAD`。
- 结论：**approve-with-fixes**；下述发现全部清偿后收口（清偿见 §3）。
- 评审确认四问：(a) 无第二引擎（semantic_inputs 只复用两引擎 + 冻结投影；
  collapse 是 #1480 声明 spec 的执行器）；(b) 无 user pin 可被覆盖
  （_user_palette 在引擎裁决前捕获）；(c) 无推导失败崩溃路径（全 fail-soft）；
  (d) 版本 bump + 指纹纪律正确（derived_measurement 自动入指纹）；
  (e) cartographer 收纳路径 legend/paint/data 三面同口径。

## 1. 评审发现与清偿

| 级别 | 发现 | 清偿 |
| --- | --- | --- |
| P2-1 | `create_thematic_map` diverging→graduated 回落时未撤回 `grammar_decision_payload`（留下与实图矛盾的 layer 级证据） | **已修**：回落分支撤回 payload + `GRAMMAR_SEMANTIC_DISCLOSURE` 披露。注：统一语义后该路径对 auto 分支结构性不可达（contract/值证据的 signed_change 均要求值域跨 0），防御性保留 + 测试锁定正数域无 diverging_center |
| P2-2 | 语义推导失败仅 log（design 承诺的 `SEMANTIC_DERIVATION_FAILED` 码不存在），违背 no-silent 契约 | **已修**：create_thematic_map 失败路径落 `SEMANTIC_DERIVATION_FAILED` check 随 layer_meta 下发；无披露面调用点 log + 缺省降级（design doc 已记录该收敛决策）；新增 fail-soft 回归测试（monkeypatch 抛异常 → 工具不崩 + 披露在案 + sequential 缺省） |
| P3 | 组合审计重复 layer id 时多 decision 撞同一层 | **已修**：used-index 贪心匹配（index+id 双匹配优先，否则首个未占用同名层）+ 测试 |
| P3 | 多决策时 `decision_fingerprint` 只报第一个（证据不可归因） | **已修**：合并形态（前 8 位串并 + 计数，有界 64）+ 测试 |
| P3 | `classification_plan.grammar` 兼容面丢 `rejected` 键（非 additive） | **已修**：回填 `rejected_evidence`（同 #1480 {kind,reason} 形） |
| P3 | service 收纳路径宣称 `collapsed_property` 但不写数据（假承诺） | **已修**：`attach_collapse_to_spec(include_data_binding=False)`——style-only 面 `collapsed_property=""`（不宣称不存在的数据侧属性） |
| P3 | `sem_inputs.checks` 去重一次性计算可重复追加；cartographer 重复 init | **已修**：运行中去重；删重复 init |

## 2. green-by-construction 评估（评审意见 + 补强）

评审认定既有测试「多为真实」（版本拒收、非法词表、pin 优先级、哨兵负值拒绝、
#783 逐字节兼容、双解确定性、registry 全量 never-gate 冒烟）；缺口与补强：
- 失败路径无测试 → 新增 `test_derivation_failure_disclosed_not_silent`（变异面：
  monkeypatch 推导抛异常）。
- 重复 id 对账无测试 → 新增 `test_duplicate_layer_ids_not_cross_audited`。
- 合并指纹无测试 → 新增 `test_multi_decision_fingerprint_merged`。
- corpus 与实现同仓镜像风险 → 以跨引擎断言缓解（grammar 结论 × resolve_symbology
  色带族/center 双面验证，非单一实现自证）。

## 3. 清偿后本地验证（串行，repo venv）

- 新增/受影响套件全绿：grammar production wiring (22) + collapse (14) +
  semantic inputs (16) + corpus (41) + recipe eligibility (8) + scale
  reconciliation (6) + #1480 五套 (104)。
- 宽回归：`tests/cartography` 全量 **1512 passed / 0 新增失败**。
- 4 个失败复现于干净 origin/master（`9e1ad229`，F09 worktree 验证），
  为**存量问题**，与本分支无关：
  - `test_vector_pdf_route.py` ×3
  - `test_thematic_convergence.py::test_cartography_findings_forwarded_through_production_evidence_channel`
    （源码文本断言：`agent_pi_bridge.py` 无 `cartography_findings` 字样）
- 邻域（实现期间累计）：#1488 measurement/symbology (145)、eligibility+downgrade (87)、
  planner+template (39)、template/quality review (125)、cartography 核心 (70)、
  converter/mapspec (33)、harness e2e (24)、scale matrix + qualification (12)。

## 4. 与并行方向的边界

同基线并发 worktree：F11（template composition）/F12（map plan compiler）/
F15（visual observation repair）与本分支共享面最小化——本分支对共享文件
（cartography.py/templates.py/quality_loop.py/lifecycle_engine.py/recipes.py）
均为小接线（单点 try/except + additive 键），新逻辑全部落在新模块
（semantic_inputs / grammar_propagation / category_collapse）。
