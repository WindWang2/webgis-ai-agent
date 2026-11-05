# PR/Issue 重叠矩阵（2026-11-06 核查）

## Open PRs（1 个）

| PR | 分支 | 文件面 | 与 Track A 交集 | 处置 |
|---|---|---|---|---|
| #1965 | zcode/review-g3dv2-fixes | frontend/map scene3d、terrain_ingest、upload/layer 路由、cartography quality_loop、golden corpus | **零交集**（无 vendor/pi、无 app/services/chat/pi_*、无 agent_pi_bridge、无 index.mjs） | 只读避让；其 `tests/cartography/golden/mutation_parity_corpus.golden.json` 不触碰 |

## 近期已合并（边界确认）

- #1876–#1880（SpatialSemanticContract/OGC-COG/时态/多尺度/Harness 恢复）、#1868–#1875、#1968–#1970（真旅程取消与部署修复）：Track B/C/D/E + CI/部署域；本任务不改其文件。#1968 的「真旅程取消」与 A2 取消验证主题相邻但实现面在 CI/journey 侧 —— 仅语义对齐（取消不复活），不改其文件。
- #1835/#1836/#1867（g3dv2）：前端 3D，无交集。

## 开放 Issues（Review-R2 维护线，只读风险输入）

| Issue | 主题 | 与本任务关系 |
|---|---|---|
| #1916 (S2b) | late 回调仍累加 no-progress 并可硬停新 turn（agent_pi_bridge.py） | 我改 bridge 附近时**不引入同类行为**；不修（维护线） |
| #1931 (S52) | pi_post_dispatch never-raise 边界 | 不修；升级不改其语义 |
| #1930 (S51) | model_routing 回退枚举 | 不修 |
| #1924 (S10) | index.mjs 相关 Medium | 与 M1 改动同文件不同段；避免回归其指控行为 |

## 共享高冲突区（MapSpec/ToolDispatchService/map-panel/chat.py）

本任务全部**只读**；dispatch_tool 回调路径不改行为（仅新增契约测试观测）。

## 结论

无需改道/缩减；按 M1–M7 推进，M1 的 index.mjs 是唯一与开放 issue 相邻的文件，改动保持最小段位并加锁定测试。
