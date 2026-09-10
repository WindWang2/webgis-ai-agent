# 02-plan — 实施波次（合并为 16 个 wave，覆盖 Epic 10 波 1–30）

每 wave：实现 → targeted tests → ruff → 03-progress 更新 → 独立 commit。

| Wave | 内容 | 覆盖 Epic 波 |
|---|---|---|
| W1 | 基线审计 + 架构冻结 + Subagent-A 挑战修订 | 1 |
| W2 | ownership.json + ownership.py + parity/disjoint 校验 + .gitignore 修订 + check_integration_preflight.py（挂 quick lane） | 2,3,4 |
| W3 | migrations_coord.py（ScriptDirectory）+ allocate_migration.py + 碰撞检测 | 5,6 |
| W4 | adr.py + allocate_adr.py + watermark | 7 |
| W5 | artifact_graph additive 扩展（content fingerprint/scope/generator version + find_hand_edits）| 4 |
| W6 | manifest.py + gen_integration_manifest.py | 8 |
| W7 | impact.py（import graph + 完备性选择器 + UNCOVERED）| 11,12 |
| W8 | merge_sim.py + simulate_merge.py（checked/unknown/not_checked + union 跨分支签名冲突）| 9,10,13 |
| W9 | runner 集成（quick preflight + integration profile + impact profile）| 14 |
| W10 | trace_context.py + ASGI 中间件 + RuntimeContext trace 字段 + events envelope + tests | 18,19 |
| W11 | SRE：/api/v1/status/detailed（鉴权）+ sre_metrics + alerts + grafana + 一致性 fixture | 20,21,22 |
| W12 | real lanes：Postgres/PostGIS + Redis + 多进程 harness（scripts/integration_harness.py）| 15,16,17 |
| W13 | chaos V3（R5 重靶后 fault 集）+ registry 再生成 | 26 |
| W14 | frontend behavioral tests + frontend-behavior.json 生成物 | 23,24,25 |
| W15 | release readiness + 政策断言 + evidence + rollback + perf 账本 | 27,28,29 |
| W16 | 完成证明（两个模拟分支端到端冲突检测）+ docs/tooling UX + Review R1/R2 + rebase + PR | 30 |

## 测试矩阵（必须，来自 Epic §15）
- 同 migration revision 两分支 / 同 ADR 号 / 同 tool ID → W3/W4/W8 负例
- 生成物无输入变更被改 / stale 产物 → W5
- 公共 schema breaking / WS/SSE breaking → 复用 V2 api_compat 快照（W8 引用）
- migration up/down/up（SQLite）+ Postgres/SQLite 行为差异 → W12（PG opt-in）
- Redis 丢失恢复 / worker 丢失 / 多进程 trace 关联 / 前端重连 / flaky 归因 / selector 漏报护栏 / merge sim 兼容重叠正例 → W7/W8/W12/W13/W14

## 资源纪律
- quick preflight < 10s；import graph 首建 < 60s 缓存命中 < 2s；merge sim 2 分支 < 30s。
- 全部新测试无网络、无大数据；real lane opt-in。
