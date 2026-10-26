# G3D04 · INDEPENDENT_REVIEW

两轮独立评审（两个不同的全新 reviewer 上下文，均未参与实现；结束后工作区
干净）。完整报告在会话记录中，此处为结论与清偿账。

## Round 1（全面七维度：GIS 科学/契约 ABI/资源取消/安全/UX/并发/回归）

**结论：可合并（修复 P1 后）** —— 0 P0 / 4 P1 / 8 P2。

| 级别 | 问题 | 清偿（commit 6af9195c5） |
|---|---|---|
| P1-1 | ghost dem 绑定路径决策输出面错报实际应用档（Pi 看到 2.5d 地图落 2d） | applied_mode 对账块（mode/respected/applied_mode/相机推荐全报实际档）+ SCENE_MODE_TERRAIN_OMITTED 注记 + 破坏性实验验证有效的锚测试 |
| P1-2 | staleness user_overrides 期望 dict、真实投影是 list → display_stale 生产形状下永不触发 | list 迭代 + dict 兼容回退；测试改钉真实形状 |
| P1-3 | patch 校验 exaggeration 下界含 0（契约 gt=0）→ 毒状态（load 侧 model_validate 失败→requirement 静默丢弃） | `_v_opt_float_exclusive_lo` + 毒状态拒绝锚 |
| P1-4 | DECISIONS D4 声明的 degrade_reason IR 披露无写入方（死字段） | `_record_degrade_reason`（agent 记账/无降级清除/文档缺席 skipped）+ 移出 scene_mode 锁 + f21-02 验收断言 |
| P2×6 | center 校验/center 偏离一致性/viewport 等值同步放行/分析绑定不回填/义务 capability 真实 id/证据负路径锚与文案 | 全部清偿（同 commit） |

评审同时实证：方位角→bearing 语义正确、ReDoS 无风险（对抗样本 0.011s）、
digest 存量零漂移、SetSceneIntent taxonomy 消费方全覆盖、raster_path 经
validate_data_path、G02 棘轮 237→240 合法。

## Round 2（修复验证 + requirement_ir 语义/多轮状态机/相机组合/lab 污染/summarize 消费者）

**结论：需再修（N-1/N-2 落在 user-wins/诚实披露红线上）** —— 验证 R1 四
项 P1 修复全部正确（含一次破坏性实验：还原 P1-1 修复 → 锚测试红）后新增：

| 级别 | 问题 | 清偿（commit 79629e0a4） |
|---|---|---|
| N-1 (P1) | genesis 播种 scene 锁后，用户对夸张/地形/挤出/相机轴的**新表达**在编辑轮被 `_locked` 守卫静默丢弃（0 patch 0 error，IR 与用户意愿漂移） | 撤 scene cue 分支的 `_locked` 守卫（cue 是用户话语投影，锁只挡 agent）；维度换锁 remove+add；四组反证锚 |
| N-2 (P1) | degrade_reason 披露闭环"降级→用户重表达→再降级"后永久断裂（user actor 清除抢占 field_provenance → agent 永久 PatchConflict） | 清除改走 system actor（系统记账不抢用户所有权语义）；跨循环记账锚 |
| N-3 (P2) | summarize locks[:8] 截断 → 8 把 layer 锁后 scene_camera 锁不可见 → fly_camera 守卫降级 | fly_camera 直读 requirement 文档全量锁；8 锁挤压反证锚 |
| P3×5 | 决策注记 applied_mode 不同步/OMITTED 截断/夸张词间容错/op_id revna 复用/staleness 死参数 | 全部清偿（同 commit） |

R2 同时实证：多轮"3D→超替→再 3D"状态机收敛正确（锁跨超替携带、revision
单调）；相机三重守卫无矛盾路径（sync 放行只写等于用户 viewport 的值）；
acceptance 存量 21 场景 pin 逐字节不变（仅 count 21→47 结构性变化）；
summarize scene 块零消费者破坏。

## 终态

- 两轮 P0：0；P1：4+2 全部清偿且有反证锚；P2/P3 建议项全部清偿。
- 残余已知边界（非评审阻断项，RISKS.md 披露）：scene 面 ACK 属 G3D02、
  DEM 源注册通道属 G3D01/G3D02、LOS/skyline 算法属 G3D03、orchestrator
  为计划编译器（执行器由 harness 既有状态机承担）。
- 全部验证绿：scene3d 112 单测 + gis_harness 2685 + acceptance 47 场景 +
  conformance/账本/lint/mypy。
