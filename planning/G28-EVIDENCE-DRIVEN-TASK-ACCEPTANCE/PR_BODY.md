# G28 — 面向真实 GIS 结果的任务验收与有限修复闭环

分支：`zcode/g28-evidence-driven-task-acceptance-20261111T153846Z`
BASE：`b8f2a6ecc`（origin/master @2026-11-11，含 #2084/#2085/#2086）
HEAD：见最新 commit。规划目录：`planning/G28-EVIDENCE-DRIVEN-TASK-ACCEPTANCE/`。

## 功能增量（全部加性；单一 evaluator 不动）

1. **权威回执证据面（P1）**：`goal_satisfaction/receipts.py` —— G14 矢量编辑
   journal、G19 字段计算 ComputeReceipt、G17 导入 CommitReceipt、G18 交付
   DeliveryReceipt（走公开 get_delivery_job 授权 + session 过滤）、G20 栅格
   MapSpec 源对账、G10 刷新回执（project 上下文在场时）的只读收集与有界
   证据投影。owner/scope 过滤（A17）、每族有界、kill switch
   `GIS_GOAL_RECEIPT_ADAPTERS=0`、失败开路（单族失败 → 诚实缺席）。
2. **科学语义与完整性闸（P2）**：`semantics.py` —— population_partial /
   density_without_denominator / rate_without_timebase / crs_unverified /
   artifact_checksum_mismatch / scope_subset / snapshot_not_live cap 码
   （只降级只披露；unknown = 无码，绝不虚构）。
3. **evaluator 加性规则（P1/P2/P3）**：frame 逐帧闸（A13/A14，含 summary↔行
   一致性 fail-closed 重查）、回执语义闸（绑定量 hard cap → FAILED，其余 →
   PARTIAL）、交付路径绑定（A16：数据交付需求 ↔ delivery 回执，地图出版
   回执不背书；身份终结匹配 fail-closed）。
4. **finalizer 最小接线（P3）**：`completion/pipeline.py` 锁外预取回执快照 +
   frame 终验（G15 verify_frame_product —— 闭合「frame_product 键生产从不
   写入」缺口）；`capability_receipts`/`frame_product`/`goal_repair_plan`
   additive 块键（缺席省略零漂移）；评估消费锁外快照（A20 迟到结果不改
   当前裁决）。G15 frame_product 终验块随终验持久化（此前只在合成语料出现）。
5. **完成协议与 UI（P4）**：`bounded_payload` 加 `requirements` 逐需求行
   （id/kind/verdict/缺失码/建议动作）；前端 `GoalSatisfactionCard`
   （真实浏览器渲染同一持久化裁决 —— 「计算完成、地图等待显示」与
   「任务失败」同屏可分）；task_complete SSE 接线（INV-2 守卫复用）+ zh/en i18n。
6. **有限修复计划（P5）**：`repair_actions.py` —— 失败规则 → 注册动作
   （只读/写入分类 + 既有 CAS 路径名 + budget_class + expected_new_evidence）；
   停止条件优先（user-wins/锁/权限/数据缺失/unsupported/预算耗尽 → blocker
   无动作）；执行仍走既有 mutation/CAS/loop_budget，本模块纯披露。
7. **可回放语料（P6）**：`_family_receipts` 新族 —— 12 个假成功反例
   （全部 must_not_pass）+ 3 个合法无地图任务 + P4 旗舰叙事案例；frame 块由
   真实 `verify_frame_product` 产出。总案例 118。
8. **Pi 闭环与浏览器旅程（P7）**：`test_pi_stub_llm_journeys` 驱动自播种
   S1 合成标注 fixture（48 合成学校 + 合成行政区 shp，走真实 ingest/
   GCJ→WGS/GPKG）+ JG28 旅程（intent→真实 POI dispatch→产品组装）锁定
   「次要行 pending → 终验诚实 pending、不落块」（queued/pending ≠ complete
   的旅程级证明）；`j17-goal-satisfaction` 浏览器旅程（真实 Chromium +
   生产契约 SSE → 完成状态卡渲染同一裁决）。

## 排除项（不做）

- 不造第二 evaluator/第二完成 Boolean；不动 READY 族/L5 visual 权威。
- 不接管 R1 issues；不重写全局 budget；不用视觉 LLM 当裁判。
- 不升级 Pi vendor（G22 所有）；本分支在现有 pin（dd30101cf）验证。
- G22–G27 未合入能力 → unsupported/not_observed（诚实缺席，不写占位回执）。

## 证据

- `goal_satisfaction` 全套件 64 passed（v1 27 + wiring 8 + corpus 4[118 案例]
  + finalizer wiring 17 + repair 9）
- finalizer 邻接回归 95 passed（map_completion/state_machine/unified_completeness）
- 全量 gis_harness 套件失败 triage：51 失败/错误与干净 BASE **逐项一致**
  （comm 零差异）→ 零引入
- 前端 vitest 10 passed + `tsc --noEmit` 干净 + registry 一致性 15 passed
- Pi 旅程：`PI_JOURNEY_E2E=1` 1 passed (58.5s)（本地构建 vendor dd30101cf）
- 浏览器：j17 1 passed (3.7s)（真实 Chromium + next dev）
- 独立评审：APPROVED；20+ 独立伪造输入全部拒绝说完成；P1 前缀伪造 +
  frame summary 伪造两方向已在 787cc62ec 修复并复验 fail-closed
  （详见 planning/.../REVIEW.md）

## 兼容/回退

- 全部新键 additive（缺席省略）；`evaluate_goal_satisfaction(receipts=None)`
  默认 None = 旧调用零漂移；旧块/旧回执缺字段 = unknown（不假 pass 不假 fail）。
- 语义变更一处：csv/geojson 等数据交付格式的 export 意图现要求 delivery
  回执（A16）—— 地图导出回执在生产从不产出这些格式（评审已核实两处
  map-export 生产者），语料 oracle 已按语义更新并注明理由。
- 回退开关：`GIS_GOAL_RECEIPT_ADAPTERS=0` 关闭回执收集，证据面回旧形状。

## 外部依赖 / 未测项

- 实模式浏览器 E2E（nightly 基建）未在本 worktree 跑（mock 档 j17 真实浏览器
  + Pi 旅程真实后端两腿组合覆盖）。
- 交付 DB 收集器仅单元级路径覆盖（kill switch/空会话/fake store）—— 无
  live DB 的环境限制，授权语义经复审对照 `_job_authorize` 逐分支核实。
- 已知限制清单见 `planning/.../ACCEPTANCE.md`（5 项，均有理由）。

## 性能/资源

- 预取在会话锁外（与 awf_runs 同点）；每族回执有界（≤4/8/8/4/8/2）；
  语料 118 案例评估 <10s；pytest 单进程（无 xdist）、vitest functional、
  playwright workers=1；重任务（vendor build/全量 pytest/E2E）持宿主级
  OS 文件锁（worktree 外 `.host-lock/hostlock.py`）。
