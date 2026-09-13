# feat(harness): visual judge & adaptive self-healing — full review coverage, wired L5 oracle, 5+ repair actions with re-evaluate/rollback (ADR-0158)

## 目标

接通"自适应制图"的最后一公里：让"不好看"能自动变好。修复闭环三处断裂 ——
① 评审只覆盖 fingerprint 路径（模板 symbology/heatmap、图层样式 command 族永不评审）；
② L5 `goal_satisfaction` 恒空转（visual oracle 无生产调用方）；
③ 自愈动作空间单一（只有投影恢复，且无效果判定）。

## 《复核纪要》（防重复复核）

- PR/issue/分支检索（visual judge / self heal / 自愈 / repair / L5 / critique / verdict，
  limit 200/300）：无同线既有实现。最近邻 #1258（AC-03 adaptive symbology，OPEN）、
  #1173（V6 visual observation，headless 代理来源）。
- 三大断点逐一确认（细节见 `docs/dev/ac-09-closure-recon.md` §5）；
  **勘正**：`visual_evaluator.py` 实际在 `app/services/gis_harness/`（01/02 线禁改域），
  桩不碰；生产实现按 §8 可改区新建于 `app/lib/harness/visual_evaluator.py`。
- `create_thematic_map` 在 master 已被 dispatch authoring 覆盖（任务书描述过时）——
  真实断口收敛为模板 symbology/heatmap 与图层样式 command 族（recon §1 矩阵）。
- ADR watermark：master 最高 0147；本线占用 **ADR-0158**（0148–0157 为兄弟线预留）。

## 变更摘要

- **P1 评审覆盖面**：触发从「结果含 fingerprint」扩为「产生了地图变更」——
  `MAP_CHANGE_COMMANDS` 命令族白名单 + 结构化 `result_indicates_map_change()`，
  legacy pipeline 与 Pi 桥同判定；fingerprint 路径语义不变。模板 symbology /
  thematic 分支把已提交的 lifecycle 世代证据回填结果（#722 断裂口修复，
  恢复完整证据阶梯）；无世代标签的 command 路径进入评审后诚实 `not_evaluated`
  （评审覆盖 ≠ 伪造收敛）。
- **P2 视觉裁判**：`app/lib/harness/visual_evaluator.py`（gis_harness 桩不碰）——
  5 维度结构化 VisualCritique、白名单消毒、单次调用无重试 + 按证据状态记忆化
  （每份状态至多 1 次 VLM 外呼）、截图来自既有头照设施（runtime_dir/map.png）或
  测试 fixture、内置 VLM callable 显式 env 开启。**严格 fail-closed**：未配置/无 key/
  无截图/超时/抛错/输出不合法 ⇒ `not_evaluated` + 机器可读 reason，绝不伪造 pass。
- **P3 L5 落地**：`derive_goal_satisfaction` —— L4 锚点通过 ∧ 无视觉 error ⇒ pass；
  L4 通过 ∧ 有 error ⇒ fail；其余诚实 `not_evaluated`（含 reason）。visual 证据在
  L4 状态计算之后追加（结构性隔离），**不得单独判 L4/L5 PASS**（显式断言）。
  MapSpecValidityTier 天花板维持 ADR-0060 不变（ADR-0158 D3）。
- **P4 动作注册表**：`app/lib/cartography/selfheal_actions.py` —— 10 种动作类型
  （4 投影恢复 + rotate_palette / clamp_layout / adjust_classification /
  clip_value_domain / adjust_labels / switch_map_type），风险三级
  （auto_safe < 语义级需 `CARTO_SELFHEAL_EXPLICIT=1` < explicit_only 永仅建议），
  risk 升序 + expected_effect 降序 + 确定性并列；双执行面（runtime 同代 patch /
  desired_state lifecycle 呈现提交）；03 线 `SymbologyDecision.rejected[]` 接口 +
  fixture 驱动（#1258 未合入）。
- **P5 修复-重评-回退**：质量快照（确定性优先）逐动作一次性判定；有害 patch 回退
  （同代 before/desired 互换）；未改善呈现提交回退（重提交变更前呈现 + 跨代
  inherited_tried 防色带轮换循环）；耗尽 ⇒ `repair_exhausted` 全量留档；
  `MAX_RUNTIME_REPAIR_ITERATIONS = 2` 不变。呈现提交在会话锁释放后执行
  （apply_mutation 自带锁，持锁重入死锁）；提交世代登记进 harness mutation 台账
  （防跨代 superseded 断裂）。
- **P6 兼容**：首个修复尝试与既有 `plan_runtime_repairs` 行为逐字节一致；
  repeated/superseded/iteration-limit 终止语义保持；`[CARTOGRAPHY_VERDICT]` 增量
  字段（action_name/improved、visual 摘要、selfheal_suggestions），三态 token 不变。
- **P8 默认态**：视觉裁判默认关闭（零 VLM 调用）；record-only（视觉结论只落证据，
  不改三态）；阻断模式切换条件写入 ADR-0158（归 10 线 ratchet）。

## 覆盖矩阵前后对照

| 路径 | before | after |
|---|---|---|
| GIS 分析结果（authoring seam） | ✓ | ✓（不变） |
| create_thematic_map（choropleth/lisa） | ✓ | ✓（不变） |
| heatmap_raster 结果 | ✓ | ✓（不变） |
| apply_template composite | ✓ | ✓（不变） |
| apply_template symbology single/categorical | **✗** | **✓**（回填世代证据，完整阶梯） |
| apply_template thematic choropleth | **✗** | **✓**（同上） |
| apply_template thematic heatmap（add_native_heatmap） | **✗** | **✓ 进入评审**（诚实 not_evaluated，#722 residual 披露） |
| layer_manager 样式/可见性/过滤/排序/删除 | **✗** | **✓ 进入评审**（同上） |
| 相机/chrome/导出（fly_to 等） | ✗ | ✗（设计如此，不触发） |

## 自愈成功率表（测试实证）

| 动作 | 风险 | 执行面 | 成功样本 |
|---|---|---|---|
| restore_visibility / reapply_opacity / refresh_legend / restore_style_projection | auto_safe | runtime patch | 既有 union patch 行为保持（worse→回退、equal→repeated 语义钉死） |
| rotate_palette（换色带，legend+paint 同步） | auto_safe | lifecycle 呈现提交 | e2e：色可分失败 → 提交 → 新世代收敛 **passed** |
| clamp_layout（改版面钳制） | auto_safe | lifecycle 呈现提交 | e2e：越界放置 → 钳制提交 ✓ |
| adjust_classification / clip_value_domain / adjust_labels | 语义级 | 建议面（授权 + 03 线 rejected[] recipe 通道） | 构造器 + 建议披露测试；默认不自动执行（设计） |
| switch_map_type（换图型） | explicit_only | 永仅建议 | 建议披露测试 |

回退链：有害 patch → 同代回退 ✓；未改善提交 → 回退 ✓；耗尽 → `repair_exhausted`
（repeated / iteration-limit / selfheal_actions_exhausted 三种终止均有测试）。

## 交付台账

见 `docs/dev/ac-09-ledger.md`（任务 → 文件 → 测试 → 证据 逐行 + §5 验收清单对照）。

## 本地门禁证据（串行执行；仓库无 pytest-xdist，`-n 2` 不可用）

- `pytest tests/cartography -q`：**1185 passed, 2 skipped**（含新增 28 项）
- `pytest tests/unit -q -m "not heavy and not real_services and not perf"`：见下方门禁记录
- `ruff check <全部变更文件>`：**All checks passed**（0 告警）
- 未触碰：`.github/workflows/**`、`frontend/**`、`migrations/**`、
  `app/services/gis_harness/**`、`app/lib/cartography/{symbology,visualization_plan,classify,palettes}.py`（grep 验证）
- 分支基于最新 master（1fd4b035）rebase，无冲突

## 风险与回滚

- 全部新行为 env 门控或增量字段。回滚面 = `record-only` 默认态本身：关闭
  `CARTO_VISUAL_JUDGE*` / `CARTO_SELFHEAL_EXPLICIT` 即回到纯确定性闭环；评审触发
  回退仅需还原 `result_indicates_map_change` 单点（fingerprint 路径从未改变）。
- 呈现提交走既有 lifecycle（锁 guard、确定性复审、revision 守卫、checkpoint），
  无新持久化格式、**零迁移**。

## 协调点

- 03 线（#1258 OPEN）：`SymbologyDecision.rejected[]` 消费接口已按
  `cartographic_profile.symbology_decision.rejected` 形状定义，fixture 驱动；合入后无损对接。
- 04 线 lifecycle pre-commit：本线评审触发不新增 lifecycle 钩子，无冲突（§8：04 让位于本线）。
- 10 线：视觉裁判事实产出（`visual_evidence` + `VISUAL_*` 检查行）+ 阻断切换条件
  （ADR-0158）已备，ratchet 基线由 10 线定义。
