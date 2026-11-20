# ACCEPTANCE — G22-PI-STATE-CONTINUITY

> 状态：pass / pending / partial（含理由）。测试名均可在仓库定位；
> 证据命令见 TEST_EVIDENCE.md。

| # | 验收 | 覆盖（测试/证据） | 状态 |
|---|---|---|---|
| A01 | stable/tag SHA 与构建产物一致 | build 日志 HEAD=b4afdddf4 + pi_doctor vendor 域 + 契约测试 @v1.5.2 | pass |
| A02 | C1–C16 当前消费契约通过 | test_pi_vendor_contract + schema golden 13 passed @v1.5.2；pi_doctor 六域 | pass |
| A03 | 无内置编码工具越界暴露 | vendor contract「无内置编码工具可达」用例（套件内） | pass |
| A04 | direct/deferred/codemode 权限语义 | test_pi_dynamic_surface v4 / test_pi_surface_abi_route（ADR-0288 语义重写版） | pass（套件绿） |
| A05 | 中文检索及 schema refs 命中 | 契约测（中文/$ref 命中）+ J8 旅程 | pass |
| A06 | 嵌套父子调用记录一致不重复执行 | mapper 单测 + A4 旅程 + dispatch 缓存嵌套 id 会合 | pass（套件绿） |
| A07 | 同 session T1 取消迟到不终止 T2 | test_pi_turn_isolation_g22（迟到/在飞/冻结交接竞态/门序，写出时刻身份断言） | pass |
| A08 | 跨 session 取消隔离 | 既有 F5（套件）+ prod_routing c4 | pass |
| A09 | cancel×settle 交错单终态 | test_pi_turn_isolation_g22（A09×2）+ turn_state latch（既有） | pass |
| A10 | 配置更新在飞 turn 不粗暴换代、下一 turn 用新配置 | test_pi_model_config_generation @v1.5.2（2 passed；#2061 复验） | pass |
| A11 | 继承环境旧 key 不覆盖 canonical 新 key | 同上（P1-3 场景在套件内） | pass |
| A12 | EOF→有界等待→强退完整回收 | test_pi_rpc_client stop 顺序族（既有，套件绿） | pass |
| A13 | >24 层第 25 层手动隐藏仍可恢复/查询 | test_g22_a13_hidden_constraint_beyond_scan_cap_survives | pass |
| A14 | pin/offset/leader 标注状态不丢 | test_g22_a14_label_authoring_overrides_projected | pass |
| A15 | active frame 正确 | test_g22_a15_active_frame_projected | pass |
| A16 | 数据 ref 删除不重建幽灵图层 | provider：test_g22_a16_invalid_ref_disclosed_no_ghost_semantics；session 侧：session_data 删除完整性族（既有） | pass |
| A17 | 压缩 summary 伪造授权不生效 | test_a17_stale_summary_claims_do_not_override_authority + provider revision 绑定 | pass |
| A18 | 恢复的 queued job 不显示完成 | test_g22_a18_restored_job_status_honest + facts 装配状态如实 | pass |
| A19 | 重连不重复提交 mutation | test_g22_reconnect_dedup（幂等键稳定×2）+ test_sse_resume（纯重放）+ CAS superseded（既有）+ J7 | pass |
| A20 | 迟到 worker 回调被代际拒绝 | test_a20_late_callback_after_handoff_rejected_409 + registry 前缀/清扫无 false-positive | pass |
| A21 | 匿名/登录/跨项目切换隔离 | test_a21_vendor_session_isolation_on_switch + 既有 isolate 用例 | pass |
| A22 | 真实浏览器恢复后继续编辑成功 | j22-restore-continuity（原 j17，因 #2090 占用前缀更名；mock 档真浏览器：恢复→只改第二幅标题→隐藏/标注/锁逐字不动、revision 恰+1、零多余 mutation）；三轮复跑均 1 passed | pass |
| A23 | 升级失败可回退 | 回退演练：v1.5.1 重建+doctor+契约 13 passed → v1.5.2 复原+doctor+契约+旅程全绿（TEST_EVIDENCE P7 表） | pass |
| A24 | 长会话 soak 队列/receipt/context 有界 | test_g22_soak_bounded（160 turn 平坦性 + 重建块 cap） | pass |

## 独立验收要点对照（reviewer checklist）

- 取消消息**实际发送时**身份：ProbeRpcClient 在 issue（写相位）内快照注册表
  身份（test_pi_turn_isolation_g22 全部断言口径）。✓
- 恢复目录读真实 G14/G15/G16 字段：workbench_locks 契约 / frame_product /
  label.authoring.overrides / agent_undo 真实形状（P2 测试 fixture）。✓
- 截断不隐藏用户约束：预算挤压测试（约束全在 + 截断披露 + id 寻址）。✓
- LLM stub 请求计数恒 0：N4 修复 + 反 vacuous 锁（resume 前计数 ≥1）。✓
- 无真实 restart 旅程不给 Ready：A22 浏览器旅程是 Ready 前置（P6/P7）。进行中。
