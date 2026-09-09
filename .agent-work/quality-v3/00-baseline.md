# 00-baseline — Quality V3 审计基线

- 日期：2026-09-10
- base SHA：`8a33e3a53f0fa53873cace853758445192722dca`（origin/master，分支与上游一致）
- worktree：`../webgis-ai-agent-quality-v3`（分支 `feat/quality-v3-integration-sre`）
- 并发方向（git worktree list）：cartography-v6 / contextual-cartographic-harness-v6 / data-fabric-v7 / extensions-v3 / geocompute-v7 / lakehouse-v7 / science-v5 / workbench-v6 / workflow-v5 / harness-v6 —— 共 10 个并行 Epic + 本方向。
- Open PRs：#1173–#1178（6 个并行 Epic）。最近合并：#1172（quality V2）。

## 1. 现有质量平台（V1+V2）真实构成

| 组件 | 事实源 | 闸/产物 |
|---|---|---|
| Quality Manifest | `app/lib/quality/manifest.py` | `docs/quality/quality-manifest.json` + MD（字节一致） |
| findings 棘轮 | `docs/quality/findings-baseline.json` + `waivers.json` | `tests/quality/test_findings_ratchet_gate.py` |
| 行为化发现 | `app/lib/quality/behavioral.py`（AST 扫 dispatch 调用点） | TOOL_UNTESTED 判定 |
| API/WS/SSE 契约 | `app/lib/quality/api_compat.py` | 快照 `tests/quality/snapshots/realtime-contract.json`，breaking 分类 |
| Contract Drift | `app/lib/quality/drift.py` | `docs/quality/contract-drift-report.json`，BLOCKER/MAJOR=0 |
| 生成物依赖图 | `app/lib/quality/artifact_graph.py`（`DECLARED` 元组为单一事实源，`app/lib/quality/artifact_graph.py:105` 起） | `scripts/check_generated_staleness.py` + `docs/quality/generated-artifacts.json` |
| Chaos V2 | `tests/fixtures/chaos.py`（FAULTS 注册表，生产零引用结构锁） | `docs/quality/certifications/CHAOS_FAULT_REGISTRY.md` 字节闸 |
| Runner | `scripts/quality_runner.py`（lanes: quick/backend/frontend/science/cartography/data/security/quality/perf + changed/full-local profile） | `.agent-work/quality-v2/runner-report.*` |
| 安全矩阵 | `scripts/gen_security_manifest.py` + `tests/quality/test_security_regression.py` | control→test 无孤儿行 |
| real_services 标记 | `pytest.ini` `real_services` marker（REAL_SERVICES=1 才武装） | `tests/real_services_celery_app.py` 已存在 |
| 告警一致性 | `deploy/alerts-rules.json` ↔ `app/core/auth_metrics.py` | `tests/test_alerts_metrics_consistency.py` |

## 2. 关键审计回答（证据）

1. **生产入口**：`app/main.py`（FastAPI，`/api/v1/health` 于 `app/main.py:415`，router 挂载 `:490`）；工具入口 `app/tools/`；geocompute `app/services/geocompute/`。
2. **唯一事实源**：registries（tools/algorithms/capabilities）+ `app/lib/quality/manifest.py` 单编译器；生成物全部是投影。
3. **第二事实源风险点**：`docs/quality/*` 生成物（已有字节闸保护）；`deploy/alerts-rules.json` 与指标名（已有一致性测试，扩展指标时必须同步）。
4. **调用链**：HTTP/ws → chat → harness/workflow → dispatch（tools）→ geocompute run → artifact registry → render/export。关联主干：`app/lib/runtime/context.py`（RuntimeContext，ContextVar 传播 request/session/turn/run/project）。
5. **Known limitations 现状**：ADR-0118（quality-v2）列出的 KNOWN-GAP 项在 `docs/quality/QUALITY_MANIFEST.md` 中逐条披露；#1110–#1113 已 closed（其他分支修复）。
6. **同域并行 PR**：#1172（quality V2，已合并）是直接前驱；无其他分支在做 V3 integration authority。
7. **测试证明力**：tests/quality 全部为行为/字节闸；`tests/unit/**` 为域单测；contract-only 的认证行在 manifest 中显式标注（诚实披露原则）。
8. **mock-only 路径**：real_services 标记的测试默认 skip（CI 专车道）；本 Epic 补本地可跑的 opt-in 车道。
9. **资源复杂度**：959 个测试文件；quality AST 扫描 O(tests)；本 Epic 新增工具必须全部 fingerprint-cached（见 16 资源纪律）。
10. **失败/恢复语义**：jobs（`app/services/jobs/`：worker/store/cancellation/lifecycle）；chaos V2 覆盖 storage/cache/pipeline/lock。
11. **多租户边界**：session/owner 校验散见各 service（artifact_registry 等，V2 已收口 SEC-KG-01/02）。
12. **冲突热点（与其他 10 个 worktree）**：`CHANGELOG.md`（追加）、`docs/adr/`（**已发生 6×0118 撞号**——`docs/adr/0118-quality-reliability-security-platform-v2.md` vs `0118-cartographic-rendering-v5.md` 等）、`migrations/versions/`（新 Epic 各自追加 NNNN 前缀 revision）、`docs/quality/*` 生成物、`BENCHMARK_MANIFEST`。

## 3. 分级 findings

- **P0（本 Epic 直接解）**
  - P0-1 ADR 撞号已发生（6×ADR-0118，各分支独立分配 next），且 PR 标题已引用不存在的 ADR-0119 文件（#1174–#1177）→ 无 allocator。
  - P0-2 无机器可读 shared-file ownership：10 个并发分支对 migrations/ADR/CHANGELOG/generated 的协调纯靠约定。
  - P0-3 无跨分支冲突预检（migration revision/down_revision、registry ID、生成物所有权）——合并阶段才爆。
- **P1**
  - P1-1 `scripts/quality_runner.py:_changed_py_targets` 是目录前缀映射的粗粒度选择器，无 import graph，选择面过大/漏报并存。
  - P1-2 health 仅 db+LLM（`app/api/routes/health.py`），无 SRE 健康分类学（redis/object store/worker/queue lag/stuck job/export/provider/extension host）。
  - P1-3 release readiness 无诚实聚合（各闸分散；runner 报告在 gitignore 的 .agent-work 里，无发布证据工件）。
- **P2**
  - P2-1 生成物账本缺 generator version 与 content fingerprint 字段（重生成后 diff 无法归因是"输入变"还是"生成器变"）。
  - P2-2 chaos V2 为进程内；跨系统场景（ws 重连、重复事件、stale revision、worker 丢失、cancellation storm）未注册。
  - P2-3 trace 关联无跨进程/跨服务标准头（W3C traceparent）接缝。
- **P3**
  - P3-1 frontend 行为证据（map layer lifecycle / MapSpec reconcile）无聚合清单。
  - P3-2 性能历史趋势无账本（perf baselines 是点值无历史）。

## 4. 结论

V3 = 在不动各域事实源的前提下，新建 `app/lib/integration/`（开发者侧协调面）+ SRE/observability/chaos/release 扩展。全部新增为 additive；不动 `app/lib/quality/` 既有语义（artifact_graph 仅 additive 扩展字段）。
