# EVIDENCE — Track A 验证证据（M13/M14/M15）

基线 origin/master `4b3629c90`（2026-10-31 复核未漂移，开放 PR 集合不变：
#1868-#1873）。分支 `zcode/gis-harness-map-operations-vnext`。

## 后端

| 门 | 命令 | 结果 |
|---|---|---|
| 新增单测 | `python -m pytest tests/unit/gis_harness/map_operations/ -q --no-cov` | **63 passed**（契约 18 / service 16 / routes 8 / tool 6 / perf+恢复 8 / 评审回归 7） |
| 目录级回归 | `python -m pytest tests/unit/gis_harness/ -q --no-cov` | **2765 passed, 3 skipped**（含 registry 交叉验证三连红修复后） |
| 兼容回归 | interaction_ir / api_auth_required / api_compatibility / completion+settle+pi_post_dispatch / map_completion / plan_compiler_corpus / intent_acceptance | **88 + 22 + 12 + 104 + 20 通过**（auto 模式披露零形状漂移） |
| ruff | `ruff check <changed .py>` | All checks passed |
| compileall | `python -m compileall <changed>` | OK |

## 前端

| 门 | 命令 | 结果 |
|---|---|---|
| 新增单测 | receipt-bus 7 + mapOperationsCommands 4 + map-harness-cards 5 | **16 passed** |
| maplibre mock 契约 | `test/maplibre-mock-surface.test.ts` | passed（i18n 键 opCard/finalAck 按先例白名单化） |
| 全量 | `pnpm -C frontend exec vitest run --maxWorkers=2` | 首轮 4676 pass + 1 fail（mock-surface，见上，已修）；**终轮 exit 0 全绿**（280s，494+ 文件） |
| typecheck | `pnpm -C frontend typecheck` | 0 error |
| lint | `pnpm -C frontend lint`（--max-warnings 0） | clean |

## 性能快照（M11，本地，宽松上界为防数量级回归）

| 层规模 | preview | commit（真实引擎事务链） |
|---|---|---|
| 10 | 1ms | 76ms |
| 100 | 2ms | 49ms |
| 1000 | 15ms | 164ms |

preview 载荷不含 mapspec（1000 层 JSON < 20KB 断言）；commit 回执含全量
mapspec 仅走 SSE 单次（与既有 mutation 工具先例一致），回执环存 slim 形态。

## 旗舰场景对应（F-A1..F-A5）

- **F-A1**：`test_fa1_multi_effect_single_operation`（隐藏热力图+学校点
  1.5×+武侯 bbox 视口，单操作 committed、durable 断言、fly_to+receipt 命令）。
- **F-A2**：`test_fa2_user_preemption_superseded`（用户抢先 → superseded/
  noop，presentation_owner=user 不洗白）+ `test_fa2_user_hidden_layer_
  agent_refused`（user_wins 跳过 + 其余效果不受牵连）。
- **F-A3**：`test_fa3_required_pending_then_confirm`（required 无 ack →
  pending；旧代次 409；最新代次 → confirmed + force finalizer）+
  `test_fa3_auto_mode_zero_behavior_change`。
- **F-A4**：`test_multi_turn_receipt_ring_recovery`（6 轮环重建/revision 链
  单调/replayed 零新 revision/跨刷新 last_result 锚定）+ 前端 D-4 恢复接线。
- **F-A5**：`test_foreign_session_404`（5 端点跨会话 404）+
  `test_ack_without_observation_rejected`（无观察 409 零假确认）。

## 生成物再生（非手写镜像）

- `tests/quality/snapshots/openapi.json`：`API_SNAPSHOT_UPDATE=1 pytest
  tests/quality/test_api_compatibility.py`（+533 行 = 5 新端点）。
- `docs/dev/endpoint-scope-matrix.csv`：`python scripts/generate_endpoint_
  scope_matrix.py`（+5 行）。

## 独立评审

见 INDEPENDENT_REVIEW.md：P0×1 / P1×3 全部关闭并附回归测试；P2 12 条
（5 修复 + 7 接受并记录理由）。评审人复跑 56 测试绿（修复前基线）。

## 已知限制（诚实披露）

1. Playwright 端口级 e2e 未运行（本环境无运行中的前后端栈）——以
   registry/route 级真实链路测试替代（工具直调无 LLM mock 依赖）。
2. 图层 filter 效果不在词表（MapSpec 编译器 filter 输出 = issue #1809
   独立修复面）；3D scene 效果归 g3dv2 系列。
3. 回执环 RMW 无锁（评审 B-2，接受：引擎 dedup 权威，损失限于审计面）。
4. symbol 效果经完整摄取管线（评审 E-3，接受：正确性无虞；中期优化点）。
