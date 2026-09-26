# F09 — Trace / Replay Oracle v3：独立 Review 记录与清偿

- Reviewer：Subagent C（独立深审，非实现者自评）
- 审查范围：`9e1ad229..7cce90d0`（8 WP 实现 + 文档）
- 清偿 commit：本文件所在 commit（review-fix）
- 设计/勘察：`f09-trace-replay-oracle-v3-{design,recon}.md`；ADR-0214

## Review 结论概要

方向与机制获独立确认：D1 消毒中立化干净、D3「录制即期望 + 校准可失败」
有真实牙齿（tamper→red / decision-drift→red 测试钉死）、兼容面
（ctor 缺省 / additive 字段 / digest 稳定性 / committed baseline 形状）
全部有测试且实测全绿。Review 发现 **3×P1 + 6×P2**，全部在本 commit
清偿（P2-4 并入 P1-1 修复面）。

## P1 清偿

### P1-1 T4 沙箱隔离破口：MapSpec 模块级单例未替换
- **发现**：`_dispatch_sandbox` 只换了 `app.services.session_data` 的单例；
  `mapspec/store.py`、`mapspec/lifecycle_engine.py`、`mapspec_store.py`
  在 **import 时**绑定真实单例 —— Redis 部署下 lifecycle 会写真实后端，
  证伪 ADR「零真实外部副作用」。
- **修复**（`receipt.py`）：沙箱逐一交换 4 个模块的
  `session_data_manager` 属性（session_data / mapspec.store /
  lifecycle_engine / mapspec_store），`__exit__` 对称恢复；
  `_MemorySessionStore` 补齐 lifecycle map-state 面（set_map_state /
  get_state_field / set_map_state_fields / 指纹读写；`commit_mapspec_state`
  刻意缺席走生产 fallback 语义）；全部 dict 有界。`__enter__` 部分失败
  即全量回滚（顺带清偿 P2-4）。

### P1-2 shrink 非 ddmin：无粒度退化 + removed 收据幻影
- **发现**：恒定二分在「删任一半即绿」的 oracle 上停在非最小复现
  （3 op、红=a∧b 同存 → 终态仍 3 op）；同轮内前块采纳后未重算后块索引
  → no-op 候选被误采纳、收据出现终态仍存活的元素。
- **修复**（`shrink.py`）：`_shrink_dimension` 实现完整 ddmin——粗块
  （len/2）→ 一轮全败后粒度减半 → 单元素；采纳即以新序列重启；
  `removed` 收据记录**元素身份**（call_id / op 名），不再记易失位索引。
  回归测试：reviewer 的两个实测场景钉死（`test_granularity_degradation_
  reaches_minimal` / `test_removed_receipt_records_identity_not_index`）。

### P1-3 D7 落地偏差：基线无投影 + `--diff` 误导 delta
- **发现**：design 原文要求 write_baseline 携带结构化投影；实现投影在
  report —— `--diff` 若对接基线文件，缺席侧逐字段 None 会被 `_drill_
  projection` 报成 gate 翻转（系统性垃圾 delta）。
- **修复**：采用「文档 + 防御」路线（避免 140 场景 committed baseline
  再生成 churn）：`diff_payloads` 对任一侧缺 projection 的场景标注
  `projection_absent=True` 且不产逐项下钻；design/ADR D7 收敛为最终
  形态（投影随 report 走）；CLI `--diff` help 明确输入是完整 report。
  回归测试 `test_projection_absent_is_flagged_not_fabricated`。

## P2 清偿

- **P2-1 flag 语义与生产 parser 漂移**：`_flag_value` 改为逐闸对齐
  （`off_is_0` = GOVERNOR/REUSE/GUARDRAILS 的 `!= "0"`；`truthy` =
  capability bind 词表；`on_values` = HARNESS_REPLAY_RECORD 的
  1/true/True；`nonempty_on` = CARTO_VISUAL_JUDGE）；回归测试钉死
  `SPATIAL_GUARDRAILS=false` 是开、`HARNESS_REPLAY_RECORD=TRUE` 是关。
- **P2-2 error_code pin 量纲错配 + 收据游标错位**：链记录捕获折叠
  `code`（`_tool_calls_from_chain`）；pin 只钉 code（不再钉自由文本
  error_msg）；`ScenarioOp.error_code` 传递到 T4 fake provider（同 code
  重建错误）；provider 收据按 (tool, 规范化参数) 精确匹配（bind 拒绝
  不消费收据），参数不可归一化回退出现序。
- **P2-3 receipt_repeat 不进回填**：`_candidate_expect` 对首 op 补
  `receipt_repeat == "repeated"` pin（校准自然裁不可复现者）。
- **P2-5 文档同步**：design D3/D6/D7 与 ADR 收敛到最终诚实实现面
  （不造 gate per-check 期望的理由、T4 覆盖面限定 ok/error/repeat、
  D7 最终形态）。
- **P2-6 永真测试**：删除测试内自算 ratio 的同义反复测试，改为经
  `build_trace` 断言 `plan_cost_delta.ratio`（产品代码参与）。

## P3 备注处置

- replayer 模块 docstring 的「receipt 级不做」陈旧声明 → 更新为 T4 已实装。
- `_MemorySessionStore._aliases` 无上限 → 加界（128，FIFO）。
- `_BUDGETS_MEMO` 永不失效 → mtime 失效缓存。
- diff drill 不含 score → gate projection 增 `score`，score-only 漂移
  可定位（`test_score_only_drift_is_visible`）。
- 保留项（不修，理由）：legacy 录制缝以 AST/文本静态断言（运行时 e2e
  需要完整 LLM mock 面，成本/价值不匹配；env 总闸默认关使 e2e 无增量
  信号）；calibrate residual 只记录不处置（残留会在 bench 翻红，防呆
  语义已足）；`--shrink` 收敛红 exit 0（help 已声明，与 CI 门语义区分）。

## master 已知失败（与本分支无关，干净基线可复现）

`tests/unit/gis_harness/test_capability_graph_v8.py::
TestReviewAFixes::test_ra3_cross_scope_model_no_false_duplicate`
在**文件级顺序运行**下失败（scope 模型注册面：期望 2 个节点，实得
1 个 global-builtin；单测隔离运行通过）。已在干净基线
`9e1ad229`（stash 本分支全部改动）复现 —— order-dependent 测试污染，
属 master 既有 flake，与本线改动无关。

## 清偿后验证

- `tests/harness_replay/`（含新增 8 文件）+ `tests/unit/test_redaction_fuzz.py`：
  **169 passed**（离线、串行、cartography marker）。
- 邻域回归：decision provenance / gis_trace v3 / dispatch bind / governor
  dispatch / capability graph（除上述 master 既有 flake）/ engine
  structural / tool pipeline 全绿。
- ruff：本线新增/修改文件 0 finding（`scripts/replay_bench.py` 的既有
  finding 为 master 基线原有，未引入新项）。
