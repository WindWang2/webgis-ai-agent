# PARALLEL_OWNERSHIP — 本分支所有权与热文件矩阵

## 本分支拥有（新增，均为新文件除非注明）

```
app/services/spatial_events/            # 新模块（contracts/ledger/service/watch/worker/
                                         # situation_projection/mission_bridge/invalidation_bridge/
                                         # governor_gate/adapters/portfolio/webhook）
app/models/spatial_events.py            # 新表 models
migrations/versions/0092_spatial_events.py
app/api/routes/spatial_events.py        # REST + SSE diagnostics
app/api/routes/portfolio.py             # portfolio 读模型 API
tests/unit/spatial_events/**            # targeted tests
tests/integration/spatial_events/**
.agent-work/event-driven-spatial-ops-v1/**
review/EVENT_DRIVEN_SPATIAL_OPERATIONS_CONTROL_PLANE_REVIEW.md
```

## 对既有文件的**加法式**触碰（≤10 行/文件，全部 fail-open hook 或注册点）

| 文件 | 触碰 | 与并行 PR 关系 |
|---|---|---|
| `app/main.py` | router 注册 2 行 + lifespan worker 启停 ~6 行 | #1336 也 +2 行（不同区域）；无语义冲突 |
| `tests/conftest.py` | `_ENV_BASELINE` 追加本分支 env 键 | #1336 也 +4 行（不同区域） |
| `app/services/gis_world_state/mutation.py` | E1 hook ~4 行（bus.publish 同区之后） | 无并行 PR 触及 |
| `app/services/artifact_revisions.py` | E2 hook ~4 行（record_revision 尾部） | 无并行 PR 触及 |
| `app/services/jobs/worker.py` | E3 hook ~4 行（finish_job 尾部） | 无并行 PR 触及 |
| `app/services/map_product_service.py` | E4 hook ~4 行（record_version 成功后） | 无并行 PR 触及 |
| `app/services/gis_situation/compiler.py` | 第 6 源 `_spatial_event_facts`（flag-gated，ring 空时字节等价） | 无并行 PR 触及 |
| `tests/quality/snapshots/openapi.json` | API_SNAPSHOT_UPDATE=1 刷新 | 约定要求 additive 变更刷新 |

## 明确禁改（#1335 热区 / 任务书排除）

- `app/services/mission_runtime/{service,store}.py`、`hotpath_convergence/*`、
  `evidence_claim/{census,grounding,verify}.py`、`gis_harness/completion/pipeline.py`（#1335）
- `modelops/*`、`geoai/*`、`frontend/components/geoai/*`、`frontend/lib/i18n/messages.ts`（#1336）
- 第二套 Mission/ExecutionGraph/ArtifactRegistry/MapSpec/SkillLibrary/EvidenceGraph/Data Fabric
- 通用 Kafka/RabbitMQ 平台、遥感算法本体、StoryMap 重写

## 依赖方向

```
spatial_events ──调用──► mission_runtime(公开API) / workflow_runtime(apply_changes)
              ──调用──► evidence_claim(freshness) / governor(背压) / collab(不依赖)
              ──被调用──► mutation/artifact_revisions/jobs/map_product (fail-open hooks)
```
无任何既有模块 import spatial_events 除 hooks 与 main.py 注册点 → 单向依赖，kill-switch 干净。
