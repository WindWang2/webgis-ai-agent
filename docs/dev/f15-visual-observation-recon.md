# F15 Recon — Production Visual Observation / Critique / Repair

- 基线：`origin/master` = `9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24，PR #1494）
- 分支：`zcode/f15-visual-observation-repair-20260926-9e1ad229`
- 勘察方式：Subagent A 只读深审（file:line 级）+ 主 agent 复核关键契约原文
- 结论：**方向任务书的「未完成面」部分过时** —— ADR-0185/0186 的运行时与自愈编译器
  均已合入 master；真实缺口收窄为 5 个接线/契约缺口（见 Still Missing）。

## 1. 基线状态

- `git fetch origin --prune` 后 `origin/master` = `9e1ad229`（seed snapshot 的同 SHA，
  但本地 master `d5315716` 与 origin 分叉：本地含 16 个 audit-batch commits、
  origin 含 187 个本方向无关 commits）。**worktree 一律从 origin/master 派生。**
- open PR 仅 #1489（dependabot node 25 docker bump）—— 与本方向零交集。
- open issues：#1436（i18n）、#1377（audit 延期跟踪）—— 无视觉相关。
- 最近功能波次 #1479（map-verify-repair loop，ADR-0209）、#1480（cartographic grammar，
  ADR-0205）、#1486（trace-replay v2）、#1487（gis-context scopes）、#1488（measurement）
  均已复核；#1479 的 visual seam 已有生产调用方（finalizer 尾段）。
- 注意：ADR-0204 系列在 post-#1488 收敛（`b5187620`）中重编号：
  map-verify-repair 闭环 ADR 现为 **ADR-0209**，grammar 为 **ADR-0205**。

## 2. Already Done（不得重做）

| 能力 | 位置 | 来源 |
|---|---|---|
| UnifiedFinding 契约（finding_id/finding_class/user_owned/recurrence_fingerprint） | `app/services/gis_harness/completion/unified_findings.py:144` | #1479 |
| 制图 review + visual 产物入统一投影 | `unified_findings.py:363,:460` | #1479 |
| finalizer 环内 no-progress 硬停 | `completion/pipeline.py:250-291` | #1479 |
| visual seam 生产接线（finalization 触发 + 有界 snapshot + 恒 degradation_only） | `pipeline.py:497-574` + `gis_harness/visual_evaluator.py` | #1479/W9 |
| VLM 视觉批评运行时 v2（严格契约/fail-closed 矩阵/双 provider/记忆化/fake_vlm/golden_images） | `app/lib/harness/visual_judge/*` | ADR-0185 |
| 视觉自愈微变异编译器（4 op/收敛账本/事务入口） | `app/services/mapspec/visual_healer.py` + `lifecycle_engine.apply_visual_heal_patch:2916` | ADR-0186 |
| 制图 grammar solver + 审计 | `app/lib/cartography/{visual_variables,grammar_solver,scale_rules}.py` | ADR-0205 |
| 确定性视觉初筛/本地裁判 | `app/lib/harness/local_visual_criteria.py`、`app/lib/harness/visual_evaluator.py` | ADR-0158 |
| blob 存储（内容寻址） | `app/services/durable_blob_store.get_filesystem_blob_store()` | 既有 |
| 出网面登记 + governor 通道 | `network_dependency.py:152`、`dispatch_adapter.py:43` | 既有 |

## 3. Still Missing（本方向真实缺口）

1. **G1 provider-neutral 观察契约**：`_assemble_visual_snapshot` 产出无类型 dict，
   无 render revision / screenshot ref / component boxes / deterministic checks refs
   的契约面；provider 与管线之间没有稳定输入/输出契约。
2. **G2 生产 provider 缺席**：仓库内没有任何模块实现 `GIS_VISUAL_EVALUATOR`
   指向的 `module:callable`；v2 引擎（需要截图字节）与 seam snapshot（无字节）
   之间无适配器。部署侧只能自备。
3. **G3 视觉 taxonomy 未归一**：v2 五维（readability/color_discriminability/
   composition_balance/information_density/spatial_alignment）、healer 四类
   （label_collision/contrast/layer_order/opacity）、F15 词表（overlap/crop/
   legibility/contrast/label_collision/legend_mismatch/empty_space/hierarchy）
   三套共存，无单一归一点，也无与 deterministic findings 的去重/融合。
4. **G4 user-approved visual repair 无入口**：`visual_repair` 触发词全仓无消费者；
   `intent_codec` 14 意图不含 `ApplyVisualHealPatchIntent`；healer 无生产调用方。
   visual error finding 在 plan 面 deferred（executor=user）后无 API 兑现。
5. **G5 跨运行 recurrence 硬停与截图隐私面**：无跨 finalization 运行的视觉
   finding recurrence 记录（W11 账本是 per-epoch 修复面）；无 screenshot
   ref-only 存储/保留纪律（后端今天根本无截图通道）。

## 4. Overlap / Must Not Touch

- open PR #1489：无交集。
- 主工作区 5 个未提交文件（geo_raster×2、endpoint-scope-matrix.csv、
  test_endpoint_scope_matrix.py、test_ogc_stac_hardening.py）：他人在改；
  本方向若登记新 endpoint 需基于 origin/master 版本的 CSV 重新生成（不在
  本地脏副本上工作）。
- #1479 已收口语义（W-A…W-D、LOOP_STOP/STATUS/VERDICT 词表）只 additive；
- `visual_evaluator.py`（harness seam）的「默认关=零行为变化」m1 语义与
  `_sanitize_finding` 判废词表不改；
- `lifecycle_engine.py` 五注册面不加新 Intent（改走既有
  `ApplyVisualHealPatchIntent` 事务入口，零 Union 扩展）；
- 禁做清单（`docs/dev/map-verify-repair-loop-recon.md` §3）：不建第二 finding
  类型/第三修复通道/不迁移 domain 词表/不把视觉评估当 verifier。

## 5. Integration Seams（实现落点）

1. 新包 `app/services/gis_harness/visual_observation/`：
   `contracts.py`（VisualObservationInput/Result、ref-only screenshot）、
   `taxonomy.py`（封闭 8 类 + 单一归一点）、`fusion.py`（与 deterministic
   同 entity+category 融合，deterministic wins）、`store.py`（截图 blob +
   ref-only 索引 + 保留纪律）、`rules.py`（确定性 rules-half 检查）、
   `provider.py`（`module:callable` 生产入口，rules_only/vlm/hybrid）、
   `recurrence.py`（跨运行指纹账本 + 硬停）、`repair_bridge.py`
   （finding → healer 缺陷翻译 + 提案存储）。
2. `pipeline.py` additive：snapshot 增补（revision/screenshot ref/component
   boxes）+ 评估后 recurrence 记账 + `visual_loop` 披露。
3. 新路由 `app/api/routes/visual_repairs.py`（plan/apply 两步、显式批准、
   CAS、收敛硬停披露、`visual_repair` 触发复验）+ `app/schemas/visual_repair_schema.py`。
4. 复用：W11 账本、healer 收敛账本（MAX_VISUAL_HEAL_ITERATIONS=2）、
   `apply_visual_heal_patch`（锁/CAS/revision 单调/idempotency），
   `LOOP_BUDGETS["repair"]`、governor SELF_HEAL —— 不建新预算/哈希。
5. 测试：契约/provider/recurrence/endpoint/corpus 五个新测试文件；
   corpus 用 `golden_images.py` 确定性渲染（无浏览器依赖）。

## 6. 本地已知失败

- 未发现 master 红/flake 记录可借鉴；本地未跑全量（资源纪律）。
- nightly browser lane（`REQUIRE_BROWSER=1`）与本方向测试面无交集
  （corpus 用 golden_images 纯 Pillow 渲染，不需要 Node/浏览器/网络）。
