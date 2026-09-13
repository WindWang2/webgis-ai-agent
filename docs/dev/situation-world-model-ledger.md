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

## M7 — 独立 review + 修复 + PR（完成）

- Review（subagent B，四轴）：**P0 = 0**；P1 × 5、P2 × 9。
- P1 全部修复（详见 ADR-0180 §6）：
  - P1-1 投影 [制图] 节同轮三重注入 verdict → 删节（DC-3 落实）；
  - P1-2 注入块 ACTIVE_TOOLS marker 中和缺口 + 转义覆盖缺口 →
    attach_turn_context 统一中和 + 投影补 6 类字段 fence；
  - P1-3 pre-turn 快照序不入 revision → 幻影变更 → revision 增设
    `frontend_sequence` 分量（4 元组）；
  - P1-4 record_interaction/advance_snapshot 无锁 RMW → session 锁；
  - P1-5 is_3d 缺席被伪造 known(False) → 写端 None 保留。
- P2 修复 7 项：GET 剥离 situation 内部键（窄化：保留前端 restore 契约
  的 `_cartographic_*`）、单源 3s 超时、空 goal unknown、删死字段、
  决策日志勘误（DC-3/5/6）、宽松断言收紧、route seam 测试。
- 不修（有理由）：P2-5 plan 源失败归因受 `load_session_plan` 既有吞异常
  语义限制（不改共享函数，guard 留作防御，记 ADR §6）；P2-8 queries/
  consistency 业务消费方接入为后续项（v1 生产消费面 = turn 投影 +
  inspector，记 ADR §6）。
- 修复后：56 focused 全绿 + 回归（turn injection/ws/chat api/bridge
  pool 87 项）全绿 + ruff 全过；review 修复单独 commit。

## 最终 DoD 对照

- [x] 执行时最新 master（580b33e9）建独立 worktree/branch
- [x] PR/review/ADR/code 勘察落 recon（重叠矩阵 + before 调用链）
- [x] 无重复施工（复用 verdict 门/观察阶梯/provenance/v6 纪律；#1270 零交集）
- [x] 生产接线真实（chat.py 两个 Pi 分支 + ws handler，非孤儿模块）
- [x] 契约可序列化/可测试/可观测（schema 导出 + 漂移测试 + inspector）
- [x] fail-closed/fallback（kill-switch + fail-open 回落 + 源级降级）
- [x] scoped tests 全绿（56）
- [x] 跨模块回归（87 项）
- [x] master 预存失败归因（本次未发现 master 预存红；全部基线绿）
- [x] 资源受控（focused --no-cov 迭代；单进程测试；无 next build——纯后端改动）
- [x] 独立 review 完成，P0/P1 全修
- [x] 文档/ADR/ledger/生成物一致（schema 由 scripts 刷新）
- [ ] 独立 PR 已创建（下一步）
- [x] 未等待线上 CI；未自动合并
