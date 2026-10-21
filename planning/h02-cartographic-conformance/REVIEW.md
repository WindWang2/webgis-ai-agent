# H02 REVIEW — 独立评审记录

## Round 1（Wave 9，2026-10-20）

- Reviewer：独立 subagent（general-purpose，与实现者无关；自行读码 + 实际复现，117 次工具调用）。
- 结论：**FIX-FIRST** → 清偿后复验通过。
- 计数：P0 ×0 / P1 ×2 / P2 ×4 / P3 ×5。

### 逐条清偿（commit c99ec307）

- P1-1 导出路由 manifest 分支无属主校验（跨租户 spec 读取） → 补 `verify_session_owner`（与 `_record_lineage` 同款）；验证：map_api 路由测试绿。
- P1-2 h02-scenarios ACK 测试缺 `mapIdle`（typecheck 红） → 补齐；验证：`pnpm run typecheck` exit 0。
- P2-1 nodata 空串语义 Python 孤例 → Python 改为仅 null/missing（与 TS/live 三端一致）。
- P2-2 manifest time 块任意键透传 → 键白名单（as_of/current/type/period/start/end/label/note_type）。
- P2-3 Python 孪生未水合层静默丢 → 补 `layer_ref_unavailable` 诊断 + EMITTER_REGISTRY 双端登记。
- P2-4 空 spec 整体冒充 pass → layer_visibility 空图层 not_evaluated。
- P3 ×5（_digest set 确定性、perf 公共 API、planner output_target、manifest 计算卸载线程、title_truncated 发射点归属） → 全部修复。

### 红线核查（reviewer 结论）

单源权威 ✓（有 P3 保留项已清偿）/ user-wins ✓ / 状态词表复用 ✓ / 无泄漏 ✗→✓（time 白名单修复后）/ 不削弱测试不改他属文件 ✓ / 不阻断导出主路径 ✓。

### 关键复现（reviewer 实跑）

- 指纹变更影响面：render_work corpus/projection/supply/envelope 61 passed（golden 内嵌 stub 指纹不受键表扩展影响）。
- planner 旧行为逐字节保持：63 passed。
- 前端编译/桥接/parity：786 passed + 1 expected fail。
- 全 lane：2339 passed / 19 skipped / 3 xfailed。

## Round 2（清偿后复验）

- 169 backend + 37 frontend 定向 + map_api/planner/pipeline 41 全绿；typecheck exit 0。
- Oracle 双验证：`pytest -m cartography` 连续两遍 2339 passed / 19 skipped / 3 xfailed（103.00s / 108.13s）—— 两遍一致。
