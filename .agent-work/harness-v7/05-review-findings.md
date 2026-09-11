# Harness V7 自行全局 Review（Round 0 —— 主 agent 自审）

日期：2026-09-10。范围：8 个 commit（0b077dbc..6b45f606），新增 8 模块 +
4 触点文件 + 8 测试套件。Review 维度按 /goal 清单逐项核对。

## 发现与修复（本 commit 内已修）

1. **[MAJOR] cancelled/budget_exhausted → 自动重规划对抗用户取消**
   - `_finalizer_continuation`（pipeline.py）：abort 裁决一律调
     `request_replan` —— 用户取消任务会被置 replan_pending。
   - 修复：不可恢复类（cancelled/budget_exhausted）不路由 replan。
2. **[MINOR] `derive_runtime_phase` 未用 `stored` 形参** —— 投机参数，
   移除（callers 同步更新）。
3. **[MINOR] continuation 出口零直测** —— 补 `_finalizer_continuation`
   单测（预算内 remediate_and_retry；全预算尽 abort + replan 置 pending）。

## 逐维度核对结论

- **正确性**：阶段派生 10 级优先序由测试穷举（committed>finalizing>
  aborted>replanning>repairing>observing>critiquing>recomputing>executing>
  plan_ready）；转移表 37 边良构测试钉死；表外组合 DERIVED_OUTSIDE_TABLE
  可观测。
- **并发/异步**：全部持久化走 session_lock_registry + 锁内重读 +
  goal/rows/块漂移守卫（map_product persist 同款）；recovery_state 读改
  写沿用 V6「调用方持锁」契约并如实披露后写胜。
- **错误处理**：所有增值面 try/except + debug/warning 留痕，绝不阻断
  turn；分类失败退空态/中性（capability index 段缺席跳过；reliability
  中性缺省）。
- **状态恢复**：删块重建等价测试（state machine/context layers/plan
  runtime 三面）；anchor 携带九域摘要；recovery 预算跨重启持久（V6）+
  replan 记账同面。
- **性能**：capability index 进程级缓存；state machine/context checkpoint
  gate 指纹幂等；runtime_state 触发 2 次计划读（advance + derive）为有界
  常量开销（LRU 内存 store）；corpus 2316 展开仅构建期，评测走 200 stride
  抽样（<1s 实测）。
- **资源泄漏**：无新线程/文件句柄；环形裁剪（transitions≤16/history≤8/
  rollback≤4/delegations≤8/findings≤12）。
- **API 兼容**：全部 additive 键（runtime_state/plan_runtime/delegations/
  _context_layers/_final_display_ack/intent_acceptance/continuation/
  display_confirmed/context_digest）；旧读者忽略；kill switch 三枚
  （GIS_RUNTIME_STATE_MACHINE / GIS_CAPABILITY_RETRIEVAL_V7 /
  GIS_HARNESS_DELEGATION）+ display 模式 env。V6 检索评测门复测通过。
- **数据一致性**：intent_verified 诚实收紧（observation 缺席不再自证
 晋级 semantically_correct）为有意行为修正，测试钉死；
  `commit_runtime_context` 成品 revision 漂移自动失效旧标记。
- **GIS/CRS 语义**：critique/descriptor 均消费既有 CRS 词表（crs_class、
  CARTO_* 阈值、组件类型词表），零新建第三词表。
- **UI 状态同步**：前端零改动；SSE payload additive 键；[GIS Runtime]/
  [GIS PlanRuntime]/[GIS Delegation] 单行投影沿用既有 projection 通道。
- **可访问性**：不涉及（无前端改动）。
- **测试缺口（如实披露，不阻塞）**：
  - display_confirmed 的 SSE/read_stored 集成路径未单独断言（单测覆盖
    is_display_confirmed 两模式）；
  - delegation 真实 SubagentDispatcher 链路需 ChatEngine —— 机制用 fake
    dispatcher 覆盖，驱动点默认关；
  - scenario corpus p@1 下限 0.30 为结构自洽门，非检索质量上限承诺。
- **安全边界**：无新外部输入面；role fail-closed（未知角色拒绝 spawn）；
  delegation/task/findings 全有界截断；ack 键仅显式 API 写入。
- **日志/可观测**：persist 失败 warning、增值面失败 debug 留痕；转移/
  验收/裁决全部入 map_product 投影面。

## 结论

Self-review 通过（3 项发现全部当场修复）。进入 Subagent B 独立 review。
