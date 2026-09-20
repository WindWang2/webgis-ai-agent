# Design — 方向 5：Map Verification → Critique → Repair 闭环收口（W-A…W-D）

ADR 关联：ADR-0081（finalizer）/ ADR-0088（runtime repair）/ ADR-0119（V6 统一
findings W6–W11）/ ADR-0134（V7 验收闭环）/ ADR-0183（goal satisfaction）。
本设计**不新开体系**：四个既有环（quality_loop / runtime_repair / finalizer /
repair_planner）是唯一执行与停止条件权威，本方向把 finding 契约补全并让
制图/视觉验证层进入同一词汇表。

## Problem Statement

闭环的「修」半边与「完成」半边已收敛，但「findings」半边仍分裂：
UnifiedFinding 缺 V1 要求的 id / 类别轴 / ownership 标志 / recurrence 指纹；
制图 review 的 fail 规则与视觉评估发现从不进入统一投影 → repair planner
看不见制图层阻断；finalizer 环内修复决策不看 recurrence（只靠轮数上限）；
visual seam（W9）无生产调用方。结果：跨域 finding 无法统一计数、去重、
追 recurrence，「finding vocabulary 统一」这条 DoD 未达成。

## Current Architecture

见 recon 文档 §1（四环 + 完成语义 + 触发点）。

## Ownership / Authority

- MapSpec desired state：mapspec_store / lifecycle_engine（唯一真相）；
- finding 词表：各 domain 原地保留（红线），UnifiedFinding 只投影；
- 修复执行：quality_loop / runtime_repair / finalizer repairs（三通道不变，
  planner 只分类）；
- user-wins：lifecycle_engine 统一 guard + repairs 的 one-shot/user_removed
  语义不变；投影层的 `user_owned` 只声明、不裁决。

## Canonical Data Contracts（新增/扩展，全部 additive）

```python
# 新词表（封闭）
FINDING_CLASSES = (
    "semantic",          # 意图/目标/验收覆盖
    "gis_correctness",   # CRS/单位/几何/字段/统计
    "cartographic",      # 图型/分类/色带/图例/样式/比例
    "visual",            # 视觉软评估（恒 degradation_only）
    "runtime_display",   # 观测/runtime 缺口（挂载/可见性/组件）
    "export",            # 导出件/一致性（degradation_only）
)

# UnifiedFinding 新增字段（4 个，全部有默认值 → 旧构造零漂移）
finding_id: str = ""              # domain|code|entity 的稳定短 id（sha1[:12]）
finding_class: str = "runtime_display"  # ⊆ FINDING_CLASSES，单一推导点
user_owned: bool = False          # 受影响实体处于用户锁/override 语义
recurrence_fingerprint: str = ""  # domain|code|entity 规范哈希（跨轮追同因）
```

- `derive_finding_class(domain, code, scope) -> str`：表驱动单一推导点；
  未知组合保守归 `runtime_display`（不猜）。
- `from_cartographic_check(check) -> UnifiedFinding`：`CartographyCheck.to_dict()`
  形状 → domain=`semantic_check`、class=`cartographic`（GIS 语义类规则 →
  `gis_correctness`）、fail → severity=error、`repairability` → repair_class
  （auto_safe → suggested_fix.operation ∈ 既有 6 op 白名单映射为
  REPAIR_CLASSES 词；not_repairable → 空）、`degradation_only=False`、
  blocks_completion 按 `_blocks` 通用规则。
- `from_visual_finding`：复用 visual_evaluator 白名单产物（domain=visual、
  class=visual、degradation_only=True 恒成立）。

## State Transitions

不动任何既有状态机。新增披露面：

- `MapCompletionResult.loop_stop: str = ""`（`"" | "no_progress"`）：环内
  修复后 findings 指纹集合与上轮完全一致且本轮确实应用过修复 → 置
  `no_progress` 并提前终止（诚实 needs_repair，不再空转最后一轮）。
- `unified_findings` 序列化键随 finding 扩展（bounded dict 追加 4 键）。

## Integration Seams

1. `collect_unified_findings(result=…, runtime_block=…, render_diagnostics=…,
   cartographic_review=None, visual_findings=None)`：新 kw-only 参数，缺席
   = 零漂移。
2. `plan_repairs_for_chapter`：从 `map_state._cartographic_review` 读取
   制图评审（已由 cartography_runtime 落库）传入 collector —— 制图 blocking
   规则自此进入分类/账本面（executor=quality_loop，不新通道）。
3. `run_map_finalization`：环内 per-finding recurrence 记录
   （`(code,target)` → applied repair），第二轮同 finding 同修复可申请 →
   跳过该修复（不再对抗），全量 no-progress → `loop_stop="no_progress"`。
4. visual seam 生产接线（G4）：
   - 新模块函数 `assemble_visual_snapshot(mapspec, observation, findings) -> dict`：
     有界投影（observation 摘要 + deterministic findings + bounded MapSpec
     元数据），**不含**截图字节/大 payload（ref/摘要纪律）；
   - finalizer 尾段：`should_run_visual_evaluation("finalization")` 且
     evaluator 配置时执行，findings 并入统一披露（degradation_only；
     披露 severity 封顶 warning + `visual_` 码命名空间；唯一裁决效应 =
     READY → READY_WITH_WARNINGS 降档）；error 级软发现经 plan_repairs
     入 deferred，warning 级纯披露不产生 plan 动作；无配置 → 零行为
     变化（m1 语义保留）；
   - `visual_repair` 触发点：repair_plan 中存在 visual 类 deferred 动作被
     用户批准执行后的下一次 finalization 自然复验（复用既有 trigger 白名单，
     不新建循环）。

## Failure Semantics

- 投影器对畸形输入（非 dict、缺码）返回 None / 跳过 —— 不猜不抛；
- visual 评估失败/缺席 → 空列表（既有语义）；
- `loop_stop` 判定是纯函数，任何异常不改变 result.status（披露面 only）；
- 制图 check 的 `not_evaluated` → severity=info、不阻断（诚实缺席）。

## Idempotency / Replay

- 幂等门三钥匙（revision / rows fp / render seq）不变；投影与 loop_stop
  是验证的派生面，随重验重建；
- W11 账本语义不变（epoch 推进即重置）；本方向不写新账本。

## Security / Permission

- 投影只搬运既有有界 evidence（quality_loop 已做 credential-safe 投影）；
- `user_owned` 声明来源于 lifecycle guard 的锁集/override 集，不在投影层
  放大权限；
- visual snapshot 组装无 I/O、无网络、不含 ref payload 明文。

## Resource / Cost

- 全部纯函数 / O(findings)；cartographic_review checks ≤ 24 上界复用既有
  report 容量；visual 评估只在白名单触发点执行（默认关）；
- 新增字段使 finding dict 增 ~120B/条，MAX_FINDINGS=24 有界。

## Observability

- `_emit_finalization_chain` 的 REPAIR 段追加 `loop_stop`；
- unified findings 序列化即披露（repair_plan 快照/测试/日志消费同一形状）。

## Backward Compatibility

- UnifiedFinding 新字段全默认值；`to_dict` 旧键不动（追加键）；
- `collect_unified_findings` 新参数 kw-only 且默认 None；
- `loop_stop` 是新披露键；STATUS/VERDICT 词表冻结不变；
- 旧测试零改动应全绿（唯一例外：断言 dict 全形状的测试若有，需 additive 更新）。

## Migration Plan

无 schema 迁移；map_state/chapter 全为 additive 键。

## Rollback / Feature Flag

- visual 接线天然 flag 化（`GIS_VISUAL_EVALUATOR` 未配置 = 关）；
- 其余为纯派生投影，回滚 = revert 提交（无持久化形状依赖）。

## Acceptance Matrix（测试 → 契约）

| 契约 | 测试 |
|---|---|
| finding_id / recurrence_fingerprint 稳定且不同 finding 不同 | test_unified_findings_v7.py |
| finding_class 推导表封闭、未知归 runtime_display | 同上 |
| cartographic fail 规则入投影（semantic_check 域、blocks 面正确） | 同上 |
| visual findings 经 collector 保 degradation_only | 同上 |
| plan_repairs_for_chapter 收 `_cartographic_review` 且 exhausted 走 abort | test_repair_planner_v6 增补 |
| finalizer 同 finding 同修复复现 → 不再对抗 + loop_stop=no_progress | test_map_completion 增补 |
| finalizer 正常两轮收敛路径不受影响（回归） | 既有 test_map_completion 全绿 |
| visual seam：无配置零行为变化；有配置 findings 入披露 | test_visual_observation_v6 增补 |
| 诚实缺席：畸形 check / 缺码不投影不抛 | test_unified_findings_v7.py |
