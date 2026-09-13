# GIS Situation / World Model — Milestone Ledger

每个 milestone：目标 / 改动文件 / 契约 / 测试 / 证据 / 兼容 / 回滚 / 未解决项。

## M0 — Phase 0 勘察与基线（完成）

- 目标：执行时基线对账、防重复、调用链 before 图。
- 产物：`docs/dev/situation-world-model-recon.md`（生产调用链 before +
  重叠矩阵 + D1-D5 重复事实源）、本 ledger、decisions。
- 基线 SHA：`580b33e9`。
- 测试：无代码改动；`git diff origin/master...HEAD` 仅 3 份 docs/dev 文档。
- 关键发现：
  - D2：legacy `build_map_state_summary` 读 `map_state["selected_feature"|
    "focus_layer_id"|"user_location"]` 顶层键，#811 WS 白名单后**无写方**
    （活通道是 `_cartographic_context_observation`）——Situation 编译器从
    活通道取数，修复该失联（legacy 路径本身不改）。
  - Subagent A 因账户速率限制失败；勘察由主 Agent 完成（不影响质量，
    记录在案）。

## M1 — Contract v1 + Compiler + Projection（S1/S2/S5 核心）

- 目标：GISSituation 严格契约（显式 unknown）、单一编译器（有界并行、
  descriptor-first、partial 降级）、有界确定性投影。
- 文件：`app/services/gis_situation/{__init__,facts,contract,compiler,projection}.py`
- 契约：`SituationIdentity` 复合 revision；`SitFact{value,status,source,
  observed_at,revision,ref,confidence}`；`GISSituation` 十 context；
  `compile_situation(session_id, ...)`；`render_situation_for_context()`。
- 测试：contract 序列化/unknown/确定性；compiler 部分源不可用降级/无
  payload 物化/单次 map_state 读；投影 byte cap/changed-first/omitted 留痕。
- 回滚：新包，无侵入。

## M2 — Diff / invalidation + snapshot 持久化（S3）

- 文件：`app/services/gis_situation/diff.py` + compiler 挂持久守卫。
- 契约：`diff_situation(before, after) -> SituationDelta`（分组 changes/
  stale/regressed）；`SituationIdentity` 字典序单调；`_situation_snapshot`
  仅前进写入。

## M3 — Frontend interaction 观察契约（S4）

- 文件：`app/services/gis_situation/observation.py` + API route +
  compiler 读取交互环。
- 契约：`POST /sessions/{id}/situation/interactions`；环 32；内容 hash
  去重 + client_generation 单调。

## M4 — 生产接线（Pi turn context）

- 文件：`app/api/routes/chat.py` 两处 env_block 构造 →
  `build_situation_turn_context`（flag `GIS_SITUATION_CONTEXT`，默认开，
  fail-open 回落 `_build_environment_turn_context`）。
- 验收：Pi 每轮获得结构化有界情境；失败不阻断 turn；turn marker 仍最后。

## M5 — Query API + Consistency（S6/S7）

- 文件：`app/services/gis_situation/{queries,consistency}.py`。
- 契约：role/visible-layer/scope/locks/delivery/constraints 纯查询；
  inconsistency 检测（desired vs observed、死选中、stale viewport、
  失败 mutation）。

## M6 — 性能基准 + Inspector + 文档（S8/S9）

- 文件：`tests/perf/`（合成 10/100/1000 层）、`app/services/gis_situation/
  inspector.py`（CLI/JSON）、`docs/gis-harness.md` 增补、ADR-0180。
- 指标：compile latency、projection bytes、no O(features)。

## M7 — 独立 review + 修复 + PR

- 四轴复核（Spec/Architecture/Reliability/Perf-Security）；P0/P1 全修；
  rebase 最新 master；`gh pr create`（不 merge）。
