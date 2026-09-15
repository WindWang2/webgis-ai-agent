# AGENT-05 审查纪要 — Specialist Agents Pack: Cartographer & CriticAuditor

- 分支：`agent/05-specialist-agents-pack-carto-auditor`
- 日期：2026-09-15
- 目标书：`agent-swarm/05-specialist-agents-pack-carto-auditor`
- 设计：`docs/adr/0189-specialist-agents-carto-auditor.md` +
  `docs/dev/carto-auditor-spec.md`
- 测试：`tests/unit/test_specialist_carto_auditor.py`（T1–T22 矩阵，24 用例）

## 1. 交付清单

| 文件 | 性质 | 说明 |
|------|------|------|
| `app/services/agent_swarm/specialists/cartographer.py` | 新增 | CartographerAgent：三域制图知识库挂载 + 七步确定性 compose + 审计驱动 revise |
| `app/services/agent_swarm/specialists/auditor.py` | 新增 | CriticAuditorAgent：只读审计 + V0–V4 一票否决 + 《交付质量审计单》 |
| `app/services/agent_swarm/specialists/ledger.py` | 新增 | ArtifactLedger：ref→payload 有界账本（Zero Big Data in Context 取货位） |
| `app/services/agent_swarm/specialists/__init__.py` | 新增 | 05 起新专家入 `specialists/` 子包 |
| `app/services/agent_swarm/duo_session.py` | 新增 | CartoAuditDuoSession（≤2 修正轮对抗闭环）+ InProcessSpecialistRuntime |
| `app/services/agent_swarm/contracts.py` | 追加 | `MapSpecDeliveryRef` / `DeliveryAuditReport`（追加式冻结，extra=forbid） |
| `app/services/agent_swarm/registry.py` | 追加 | `cartography_specialist` / `audit_judge` 角色档 + 两专家注册 |
| `app/services/agent_swarm/__init__.py` | 追加 | 新专家/契约/duo session 惰性导出（PEP 562） |
| `tests/unit/test_specialist_carto_auditor.py` | 新增 | TDD 套件（测试先行，先红后绿） |
| `docs/adr/0189-…md` + `docs/dev/carto-auditor-spec.md` | 新增 | 阶段一产物 |

## 2. 合并调和纪要（基线偏差，显式记录）

任务书 §0.1 指定从 `origin/master` 检出，但 agent_swarm 系列代码尚在
本地分支（origin/master 无 `app/services/agent_swarm/`）。本分支实际
基线为 **`agent/04`（ADR-0188 专家包基座）+ 合并 `agent/03`（ADR-0187
swarm 总控）**。合并的 add/add 冲突（两分支各自新建了同名
`contracts.py` / `__init__.py`）按「内容零改动、只改模块路径」调和：

- ADR-0187 委派契约层改名 `delegation_contracts.py`（5 处 import 重
  定向：orchestrator / dispatcher / aggregator / agent_pi_bridge / 测试）；
- ADR-0188 输出契约保留 `contracts.py`；`__init__` 合并 registry 急切
  注册与 PEP 562 惰性导出；
- 调和后基线验证：`test_swarm_orchestrator.py` +
  `test_specialist_data_compute.py` 51 用例全绿。

## 3. 独立性审查（裁判逻辑是否严格客观独立）

逐条核对 ADR-0189 D2 红线制度的落地：

1. **实现隔离**：`CriticAuditorAgent` 无任何写面——角色档
   `allow_mutation=False`、工具白名单仅 `{audit_spatial_quality,
   gis_skill_replay_check}`（与制图面零交集）；`audit()` 只调
   `review_cartography`（只读审，不跑 repair）、
   `resolve_goal_contract` / `evaluate_goal_satisfaction`（纯函数）。
   T15 锁定「审计前后载荷深度相等」。
2. **不可自我确证**：auditor 不采信交付券自述——必须经账本取到载荷
   才审（V0：`ref_id` 不可达即 fail，即使券自称 succeeded）；审计单
   `audited_fingerprint` 用 `cartographic_fingerprint` 独立重算，与
   券内声明互为印证（T15）。对抗回路里 revise 的修复面被限制为
   审计单 suggested_fix 驱动的 AUTO_SAFE 修复（`revise_no_op` 诚实披
   露无修复可执行），cartographer 无法"绕过裁判改卷"。
3. **fail-closed**：无证据 ≠ pass（`partial`/`not_evaluated` →
   `verdict="not_evaluated"`，T14）；goal 契约在而评估证据不足 →
   不入 pass；visual/assisted 证据永不单独背书 PASS（复用
   `PASS_CAPABLE_CLASSES` 同门纪律，不自造豁免）。
4. **不造第二 verdict**：`goal_score` 是逐需求 ledger 的派生口径
   （required 且 fulfilled / required 总数，缺证据不入分子），字段
   `goal_score_derivation` 必填披露；READY 族 / final_map_status 等
   G7 权威按原样采信；报告结论 advisory，不改写上游状态。
5. **一票否决的不可对冲性**：任一 veto 直接 `verdict="fail"`，与
   goal_score 高低、review warnings 数量无关；veto 条目携带
   rule_id + severity + message + audited_fingerprint + evidence +
   suggested_fix 六元组（因果链不可抵赖，T11 逐字段断言）。

结论：裁判与实现者在输入面（只读券 + chapter 证据）、执行面（无
mutation 工具）、裁决面（独立重算指纹 + 四红线否决）三个维度均解耦，
对抗博弈的胜负判据不掌握在被审计方手中。

## 4. 对抗收敛审查

- T17：R0 注入缺陷（关图例）→ 审计 V1 否决 → revise 执行
  `set_map_legend_visibility` AUTO_SAFE 修复 → R1 pass，轮次 1 ≤ 2；
- T18：不可修复缺陷（零要素）→ 两轮后 `failed_escalated`，6 张
  receipt 全程上交，绝不静默放行；
- T19：四会话并发共享 Governor（集群信号量 ≤3 纪律），终态零滞留
  （`active_count == 0`），receipts 全部合法词表。

## 5. 门禁证据

```
pytest tests/unit/test_specialist_carto_auditor.py            → 24 passed
pytest … + test_specialist_data_compute + test_swarm_orchestrator → 75 passed
pytest tests/unit/gis_harness/test_goal_satisfaction_{v1,wiring,corpus} → 38 passed
ruff check app/services/agent_swarm/ tests/unit/test_specialist_carto_auditor.py → All checks passed
import app.agent_pi_bridge / app.services.agent_swarm        → OK（registry 四专家）
```

（cartography 质量回路回归结果见附录运行记录；CI 全局覆盖率门槛
`--cov-fail-under=75` 不受影响——本包为纯新增路径。）

## 6. 偏差与遗留

1. **命名微差**：文档阶段账本名 `MapSpecLedger`，实现定名
   `ArtifactLedger`（同时承载 `ref:mapspec-*` 与 `ref:audit-*` 两类
   取货位），文档已同步更名；
2. **目录双态**：04 两专家在 `agent_swarm/` 平面、05 起入
   `specialists/` 子包——显式过渡（ADR-0189 D7），迁移须另立 ADR；
3. **compose 的槽位映射**：solver 槽位名与 MapSpec position 词表一致
   （六槽），越界槽位诚实降级为 `none`，不虚构像素级位置；
4. **v1 非目标未做**（ADR-0189 D7）：LLM 审计叙述、第二 evaluator、
   quality_loop 语义修改、04 专家迁移；
5. **后续建议**：(a) `InProcessSpecialistRuntime` 的请求注入面将来
   可升级为 ref 存储读取，接 ADR-0187 的 LLM 委派路径做端到端集成
   测试；(b) 审计红线可随语义检查表扩充（如色彩可分辨硬失败是否升
   红线）再议。

## 7. 资源约束执行记录

- 并发 subagent 峰值 = 3（勘察阶段三路并行，之后全程主线程串行
  开发），满足 ≤3 约束；
- `$goal` / `$loop` 未出现在本会话可用技能列表，以手动收敛循环
  （TDD 红绿迭代 + 阶段门禁）替代执行，收敛判据 = 测试 100% 通过 +
  ruff 干净 + 回归全绿。
