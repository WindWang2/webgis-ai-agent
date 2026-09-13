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

## M1 — Contract v1 + Compiler + Projection（S1/S2/S5 核心）— 完成

- 文件：`app/services/gis_situation/{__init__,facts,contract,compiler,projection}.py`
- 契约：`SitFact{value,status∈known|unknown|stale|unavailable,source,
  observed_at,revision,ref,confidence}`；`GISSituation`（identity 复合
  revision `(mutation,observation,interaction)` + 十 context + evidence），
  extra=forbid；`compile_situation()`；`render_situation_for_context()`。
- 语义落地：
  - unknown 显式成事实（不猜 0/false）；源级失败在编译出口统一传导为
    `unavailable`（`_mark_unavailable_backing`，前缀→权威源映射）；
  - 编译器固定扇出 5 源 + **单次 map_state 全量读**（测试断言 reads==1/轮）；
  - descriptor-first：`get_ref`（payload 口）被调用即失败测试钉住；
  - viewport 取数优先级 pre-turn 快照 > WS 键（D1 裁决）；
  - D2 失联修复：选中/聚焦/位置从活通道 `_cartographic_context_observation`
    读取（legacy 死键不再参与）。
- 投影：4096B hard cap（`_cap_text` 留痕）、delta 变更 `*` 标记 +
  "本轮变更"行、`(+N omitted)` 裁剪证据、unknown 显式成行（视口/图层）、
  可选节全 unknown 缺席、`_xml_fence` 转义、确定性（compiled_at 调用方
  注入 = #388 冻结时钟）。
- 测试：contract 8 项 + compile/diff 9 项（当时计数）。

## M2 — Diff / invalidation + snapshot 持久守卫（S3）— 完成

- 文件：`app/services/gis_situation/diff.py`
- 契约：`diff_situation` → `SituationDelta{changes(坐标分组),regressed,
  changed_coordinates(),by_context(),summaries()}`；`advance_snapshot`
  只前进（倒退拒收/同 revision 幂等/写失败 best-effort）；`load_snapshot`
  契约演进前向兼容（解析失败=无快照）。

## M3 — Frontend interaction 观察契约（S4）— 完成（服务端）

- 文件：`app/services/gis_situation/observation.py` +
  `ws_service.handle_situation_interaction`（WS 通道
  `situation_interaction`）。
- 契约：封闭 kind 词表（8 类）、payload 键白名单 + 嵌套键数封顶(8) +
  总 512B 预算、内容寻重（同 kind 连续同载荷=重复丢弃）、
  client_generation 单调（旧代拒收）、32 条有界环。
- v1 前端不新建 WS 客户端（无既有感知 WS 面）；下一轮感知由既有
  per-turn 快照通道满足；WS 通道为增量接入预留（决策 DC-6 补充）。

## M4 — 生产接线（Pi turn context）— 完成

- 文件：`app/api/routes/chat.py`（`_build_situation_env_block` + 两个
  Pi 分支调用点）、`app/services/gis_situation/turn_context.py`。
- 行为：compile → diff → advance → 投影；`GIS_SITUATION_CONTEXT=0`/
  任何异常 → 逐字节回落 `_build_environment_turn_context`；调用点位于
  `_record_frontend_cartographic_observation` 之后（编译即本轮最新快照）。

## M5 — Query API + Consistency（S6/S7）— 完成

- 文件：`app/services/gis_situation/{queries,consistency}.py`。
- 查询：role→ref / visible layers / geographic scope / selected feature /
  user locks（hidden+focus）/ delivery / constraints / pending / analysis。
- 一致性（只报告 + 状态级协调，不做事务）：observed_behind_desired、
  observation_missing、selected/focus source removed、user_hidden 冲突
  （user wins）、stale/missing viewport、verdict 指纹失配、后台任务在途、
  sources_unavailable；`reconcile_fact_views` 把死选中降级 stale（纯投影）。

## M6 — 性能基准 + Inspector + 文档（S8/S9）— 完成

- 基准（`tests/perf/test_gis_situation_scale.py`，合成 fixture）：
  - 10/100/1000 层 compile：全部 < 5s 预算（实测 ~秒级下探）；
  - 投影 bytes ≤ 4096 恒定（不随层线性增长）；
  - 10 万要素仅以计数出现（no O(features)）；
  - 1000 层快照持久化 < 64KB。
- Inspector：`scripts/situation_inspect.py`（人类/JSON 双形态 +
  `--dump-schema`）→ `docs/dev/situation-contracts/gis-situation.schema.json`
  （契约漂移测试守护）。
- 文档：ADR-0180；`docs/gis-harness.md` 增补「情境 ≠ 聊天历史」节；
  本 ledger。
- 测试总量：52 项 focused 全绿（contract 8 / compile-diff 10 /
  projection-queries-consistency 17 / observation-turn 12 / perf 5）。
- 回归：`test_cartography_turn_injection.py` + `test_chat_context_builder.py`
  (13) + `test_ws_service.py`+`test_ws_auth.py` (18) +
  `test_pi_bridge_pool_prod_routing.py`+`test_pi_bridge_compat.py` (40) +
  `test_chat_api.py` (8) 全绿。
- Lint：ruff 全量通过。

## M7 — 独立 review + 修复 + PR（进行中）

- 四轴复核（Spec/Architecture/Reliability/Perf-Security）；P0/P1 全修；
  rebase 最新 master；`gh pr create`（不 merge）。
