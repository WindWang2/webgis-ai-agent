# TEST_MATRIX — Track A（map_operation.v1 + display-confirmation seam）

后端 56 条（tests/unit/gis_harness/map_operations/）+ 前端 18 条 + 回归套件。
运行命令：`python -m pytest tests/unit/gis_harness/map_operations/ -q`；
`pnpm -C frontend exec vitest run --maxWorkers=2 <paths>`。

| 类别 | 用例（文件::test） | 验证点 |
|---|---|---|
| 契约 | test_contracts.py ×18 | 词表闭合/extra 拒绝/paint·symbol·component_patch 白名单/有界（8 效果、id 长度）/敏感分级/schema_version 钉扎 |
| 旗舰 F-A1 | test_service.py::test_fa1_multi_effect_single_operation | 3 效果单操作 committed、durable 落盘（layout.visibility/circle-radius 4→6/view.center）、fly_to+receipt 命令、revision 前进 |
| 旗舰 F-A1' | test_service.py::test_preview_is_zero_mutation | before/after 投影、零 mutation、preview 不进环 |
| 旗舰 F-A2 | test_service.py::test_fa2_user_preemption_superseded | 用户先提交后 agent CAS 基线失效 → superseded/noop，presentation_owner=user 不被洗白 |
| 旗舰 F-A2' | test_service.py::test_fa2_user_hidden_layer_agent_refused | user-wins：效果级 skipped(user_wins) + 其余效果不受牵连 |
| 幂等 | test_service.py::test_idempotent_replay_same_operation_id | 重放 replayed、零新 revision、环内单条 |
| 恢复 | test_service.py::test_receipt_ring_survives_and_bounded | 18 次操作环 FIFO ≤16、旧条淘汰 |
| 指代 | test_service.py::test_focused_layer_anaphora / test_ambiguous_name_returns_clarification_no_mutation / test_last_result_anaphora_from_provenance | focused=IR selection；歧义→候选+零 mutation；last_result=环→provenance |
| 敏感 | test_service.py::test_sensitive_remove_requires_owner_confirm / test_sensitive_remove_with_user_ack_commits | agent 停在待确认（层仍在）；user_ack+origin=user 真删除 |
| noop/移动/缺失 | test_noop / test_move_layer_to_top / test_missing_layer_rejected / test_viewport_persists_and_commands / test_symbol_layout_bucket_label_size | 同值 noop、层序 top、typed layer_not_found、view 持久+fly_to、label_size 落 layout 桶 |
| 路由 | test_routes.py ×8 | 回执环 GET/单条 404/owner 批准流（409 重复）/非 pending 409 |
| 旗舰 F-A3 | test_routes.py::test_fa3_required_pending_then_confirm | required 无 ack→pending；旧代次 409 stale_ack；最新代次 ack→confirmed+force finalizer；auto 零行为变化；无裁决 409；早于裁决 409 stale_verdict |
| 旗舰 F-A5 | test_routes.py::test_foreign_session_404 | 5 个端点跨会话统一 404（require_owned_session） |
| 工具 | test_tool.py ×6 | 注册+args_model、dispatch preview 零突变、commit 回执命令、幂等重放、敏感中止、clarification、typed error |
| 性能 | test_perf_and_recovery.py::test_perf_preview_commit ×3 | 10/100/1000 层：preview 1/2/15ms、commit 76/49/164ms（宽松上界 5s/30s） |
| 载荷 | test_perf_and_recovery.py::test_perf_large_spec_not_in_preview_payload | preview 无 mapspec、JSON <20KB |
| 恢复 F-A4' | test_perf_and_recovery.py::test_multi_turn_receipt_ring_recovery | 6 轮环重建、revision 链单调、replayed 零新 revision、last_result 跨刷新锚定 |
| 负例 | test_perf_and_recovery.py::test_ack_without_observation_rejected 等 ×3 | 无观察 409、空环、9 效果拒绝 |
| 前端 bus | receipt-bus.test.ts ×7 | 形状拒绝/跨会话丢弃/去重/FIFO4/会话切换清空/就地更新/订阅 |
| 前端命令 | mapOperationsCommands.test.ts ×4 | catalogue 注册（#535 门）/投递成功/跨会话 failed typed/validator |
| 前端卡 | map-harness-cards.test.tsx ×5 | committed+user_wins 渲染、批准回填（POST commit URL+body 断言）、required pending 显示+ack 最新代次、stale 409 如实报错卡不消失、auto 不显示 |

## 回归证据（改动面）

- interaction_ir 88、auth_required 22、api_compatibility 12（openapi 快照再生 +533 行）、
  intent_acceptance_v7 / plan_compiler_corpus 20、map_completion+settle+pi_post_dispatch 104。
- 前端：components/map 393/393、lib/{map-commands,mapspec,interaction,render-protocol} 816。
- M14 全量数字见 EVIDENCE.md。

## 明确未覆盖（诚实披露）

- Playwright 端口级 e2e（本环境无运行中前后端栈）—— 以 registry/route 级
  真实链路测试替代（无 mock LLM 依赖：工具直调）。
- 图层 filter 效果（#1809 独立修复面）；3D scene 效果（g3dv2 系列所有）。
