# PROGRESS — 实施记录

## 里程碑与提交

| 里程碑 | commit | 内容 |
|---|---|---|
| M1 | `aa974220` | contracts / durable ledger + cursor / watch store / migration 0092 / flags（41 tests） |
| M2–M6 | `5edae0f1` | watch 引擎 / service+worker / situation 投影 / mission bridge / invalidation bridge / governor gate / adapters E1–E4（84 tests） |
| M7 | `21f8e697` | REST+SSE+webhook+replay API / portfolio 读模型 / main.py 接线 / E1–E4 落位 / turn context [空间事件] 块 / openapi 快照 |
| M8 | `a2c2a8a6` | API 集成测试 + 故障注入 + portfolio factory seam |
| M9 | （本轮） | 独立对抗审查 5×P1 + 9×P2 红测→修复→回归（见 review/ 产物） |

## 最终测试面

- `tests/unit/spatial_events/`：94 passed（含 e2e invalidation、cooldown 重试、sync dispatch、租户劫持红测）
- `tests/integration/spatial_events/`：20 passed（API/租户/webhook/SSE/replay/portfolio）
- 相邻回归：mission_runtime + governor + jobs + workflow_runtime + cross-tenant + evidence_claim/hotpath + situation turn = **656 passed**
- ruff（app/ + tests/）：clean；`git diff --check origin/master...HEAD`：clean
- alembic 0092 up/down：sqlite 实测通过（含 claimed_at 列）

## 关键设计落点（与 DECISIONS.md 差异）

- D7 修正：Situation 投影面 = `situation_projection`（session ring + `projected_facts` API）
  + `turn_context` 的 flag-gated `[空间事件]` 附加块（ring 空/flag 关时字节等价），
  **未**改 gis_situation compiler 扇出（更低风险、同等语义）。
- invalidation bridge 从"subject_type 种子"修正为"ref ↔ bound_ref/output_ref 精确节点种子"
  （P1-2 修复，见 review 产物）。
