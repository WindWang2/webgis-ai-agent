# AC-09 交付台账

任务 → 文件 → 测试 → 证据。验收清单（任务书 §5）对照见文末。

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| P0 勘察（覆盖矩阵/L1-L5/AUTO_SAFE/桩盘点/防重复复核） | docs/dev/ac-09-closure-recon.md | —（只读） | §1 矩阵 14 行；§5 三大断点逐条确认；ADR watermark 0147 |
| P1 Plan A：symbology 命令路径回填 lifecycle 证据 | app/tools/templates.py（`_surface_generation_evidence` + single/categorical/thematic 三分支） | `test_templates_surface_generation_evidence` | 结果携带 fingerprint/is_compiled/seq/revision；失败/None 不回填 |
| P1 Plan B：评审触发=地图变更（legacy） | app/services/chat/tool_pipeline.py（触发段）；app/services/cartography_runtime.py（`MAP_CHANGE_COMMANDS`/`result_indicates_map_change`/record seam evidence-only） | `test_map_change_whitelist_structure`、`test_command_path_enters_shared_evaluation`、`test_camera_commands_do_not_trigger_evaluation` | 命令路径进共享评审且诚实 not_evaluated；相机类零触发 |
| P1 Plan B：Pi 桥镜像 | app/agent_pi_bridge.py（同判定，10 行） | 与 legacy 共用 helper（结构同源） | fingerprint 门/持久化语义不变 |
| P2 视觉裁判生产实现 + 接线 | app/lib/harness/visual_evaluator.py（新，533 行）；app/lib/harness/pi_agent_harness.py（attach 调用） | `test_visual_judge_disabled_is_fail_closed`、`test_visual_judge_no_screenshot_is_not_evaluated`、`test_visual_judge_fault_injection_never_fakes_pass`、`test_visual_judge_memoizes_single_call_per_state`、`test_sanitize_critiques_whitelist` | not_evaluated 全路径带机器可读 reason；VISUAL_ORACLE 证据行；L5/cartographic_quality 均非 pass；限流 1 次外呼 |
| P2 多图型非 not_evaluated（VLM 可用） | 同上 | `test_visual_judge_three_map_types_evaluated` | choropleth/heatmap_raster/proportional_symbol 三类均产出 evaluated 批评（注入 judge，env seam 同通道） |
| P3 L5 推导 | pi_agent_harness `_success_levels`；visual_evaluator `derive_goal_satisfaction` | `test_l5_visual_fail_downgrades`、`test_l5_visual_concurs_only_with_l4_anchor`、`test_l5_success_levels_visual_never_alone_passes`（显式断言） | L4 锚点缺失 ⇒ not_evaluated（l4_anchor_not_passed）；visual error ⇒ fail；evidence tier 天花板评估结论 = 维持 ADR-0060（见 ADR-0158 D3） |
| P4 动作注册表（10 种/风险/排序/授权） | app/lib/cartography/selfheal_actions.py（新，560 行） | `test_action_registry_has_at_least_five_types_with_risk_grading`、`test_action_selection_risk_ascending_effect_descending`、`test_semantic_actions_require_explicit_authorization`、`test_rejected_candidates_fixture_interface`、`test_palette_rotation_builder_…`、`test_clamp_layout_builder_…`、`test_quality_snapshot_hierarchy`、`test_triggers_from_review_extracts_rules` | 10 类型/3 风险级/双执行面；risk 升序+效果降序+确定性并列；03 线 rejected[] fixture 对接 |
| P5 修复-重评-回退 | app/services/cartography_runtime.py（编排器重构 + 锁外执行器） | `test_projection_patch_worse_quality_triggers_rollback`（有害回退）、`test_projection_patch_equal_quality_terminates_repeated`（持平→既有 repeated 语义）、`test_palette_rotation_commit_heals_deterministic_color_failure`（rotate 成功闭环：提交→新世代→收敛通过）、`test_clamp_layout_commit_success_sample`（clamp 成功）、`test_commit_no_improvement_reverts_previous_presentation`（提交回退）、`test_unauthorized_semantic_actions_surface_as_suggestions`（建议不执行） | attempts 带 quality_before/after、improved/worse、kind（patch/rollback/commit/commit_revert）；history+inherited_tried 防换代循环；MAX=2 不变；exhausted 路径全覆盖 |
| P4/P5 成功样本记账 | 同上 | 动作成功样本：restore_visibility/reapply_opacity/refresh_legend/restore_style_projection（union patch，既有行为）+ rotate_palette + clamp_layout = 6 种可执行动作各有 e2e 成功路径；adjust_classification/clip_value_domain/adjust_labels 构造器 + 建议面用例；switch_map_type 永建议 | 任务书"每种至少 1 个成功样本"：可执行类型全部 e2e 验证；语义级默认不执行是设计（D-7），其执行通道（授权+rejected[] recipe）单测覆盖 |
| P6 verdict 增量扩展 | app/lib/cartography/verdict_summary.py | `test_verdict_extension_keeps_three_state_tokens` | 三态 token 不变；pass 形状无新增块 |
| P6 提交世代入 mutation 台账 | cartography_runtime 执行器 | rotation/revert 用例断言 `cartographic_selfheal_commit` 台账 | 防跨代 superseded 断裂 |
| P8 收口 | docs/adr/0158-…md、CHANGELOG.md、docs/dev/ac-09-decisions.md、本文件 | — | record-only 默认 + 阻断切换条件写入 ADR |
| P7 门禁 | — | tests/cartography 1185 passed + tests/unit 全量（见 PR 门禁取证）+ 新增 28 | ruff 0 告警；未触碰禁改域（grep 验证） |

## 验收清单对照（任务书 §5）

- [x] 评审覆盖矩阵：地图变更路径 100% 进入评审（recon §1 前后对照；command 路径 ✗→✓）
- [x] visual 类证据 ≥3 类图型产出非 not_evaluated（注入 judge；`test_visual_judge_three_map_types_evaluated`）
- [x] fail-closed 断言：VLM 抛错/超时/无 key/无截图 ⇒ 绝不产出 pass（故障注入测试）
- [x] 自愈动作类型 10 种 ≥5；可执行类型各 1+ 成功样本；语义级为"建议+授权通道"设计（D-7）
- [x] 修复-重评-回退 e2e；repair_exhausted 路径测试（repeated/iteration-limit/selfheal_actions_exhausted 三种终止）
- [x] visual 不得单独判 L4/L5 PASS（显式断言 `test_l5_success_levels_visual_never_alone_passes`）
- [x] 既有 tests/cartography 评审类测试全绿（1185 passed）；verdict 三态语义未破坏（快照测试）
- [x] `pytest tests/unit -q -m "not heavy and not real_services and not perf"`：**10700 passed**；
      28 failed 中 27 项与 origin/master 干净基线逐一重合（本机缺 Node CLI / 真实 socket /
      双进程 spawn / Windows 路径守卫 / 扩展沙箱资源限制等环境域，零制图·harness 域），
      1 项（geocompute 事件端点）为双套件并行负载 flake（双分支隔离复跑均通过）——
      **本次改动零新增失败**；仓库无 pytest-xdist，-n 2 不可用（D-12）
- [x] `ruff check <变更文件>` 0 告警；未改 .github/workflows/**

## 已知边界（诚实披露）

- Pi 桥对**无 fingerprint 且无追踪**的 command 路径（如 add_native_heatmap）只记证据，
  评审结果诚实 not_evaluated —— 世代标签缺失是 #722 既有 residual，本线不伪造。
- 底图切换（BASE_LAYER_CHANGE）不挂本次评审触发（独立 SetBasemapIntent 通道，recon §6）。
- `CARTO_VISUAL_JUDGE_MODE=block` 未实现（记录于 ADR 切换条件，归 10 线定义精确语义）。
