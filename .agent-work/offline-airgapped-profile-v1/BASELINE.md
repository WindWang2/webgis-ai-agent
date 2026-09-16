# BASELINE — offline-airgapped-profile-v1

- 执行时间：2026-09-17
- `origin/master`：`faa453a8935101378c23eb6694a42c3616d9c670`（Hot-path Convergence #1329）
- worktree：`../webgis-wt-offline-profile-v1`
- branch：`platform/offline-airgapped-profile-v1`（自 origin/master 创建）
- 与任务书快照核对：一致。open PR 比快照新增 #1351–#1356（data/eval/frontend/rs/harness/cartography 六个并行方向），均不触碰本分支 ownership 面（offline profile / egress / preflight / catalog / frontend tile providers）。#1335（claim/tenant/mission fail-closed）与 #1336（GeoAI platform 11）维持快照描述。

## Open PR 一览（执行时）

| PR | 方向 | 与本分支交集 |
|---|---|---|
| #1335 | harness claim/mission fail-closed | 无 |
| #1336 | GeoAI promptable platform 11 | 无（embedding-cache/frontend 热区不触碰） |
| #1351 | data 空间质量 harmonization | data_fabric 相邻，但只动 quality 面不动 security/egress |
| #1352 | eval benchmark factory | 无 |
| #1353 | frontend ops cockpit | frontend 相邻（新组件，不改 providers.ts） |
| #1354 | remote sensing temporal cube | 无 |
| #1355 | harness event-driven ops | 无 |
| #1356 | cartography multiscale scene | 无 |

## 本分支 Ownership 声明

拥有：`app/core/egress.py`（新）、`app/core/network_dependency.py`（新）、`app/core/config.py`（增量设置）、`app/core/network.py`（守卫接线）、`app/services/data_fabric/security.py`（守卫接线）、`app/services/chat/llm_client.py`（守卫接线）、`app/api/routes/health.py`（network_policy 组件）、`manage.py`（preflight/catalog/sbom 子命令）、`frontend/lib/providers.ts`（local provider + profile 过滤）、`.env.example`、`tests/conftest.py::_ENV_BASELINE`、`docs/DEPLOYMENT-offline.md`、ADR-0197。

禁止触碰：`modelops/geoai/**`、`frontend/components/geoai/**`、vendor/pi、模型权重/字体发行、云模式默认行为。
