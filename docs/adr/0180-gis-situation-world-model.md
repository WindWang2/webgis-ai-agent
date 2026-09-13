# ADR-0180: GIS Situation / World Model v1 —— 结构化会话情境

- 状态: Accepted
- 日期: 2026-09-13
- 线: `harness/gis-situation-world-model-v1`（方向 2）
- 关联: ADR-0072（GISWorldState 门面/读模型）、ADR-0076（SessionPlan）、
  ADR-0104（上下文策略/budget）、ADR-0119 D9（观察阶梯）、#811（WS 感知
  白名单）、#1073（revision CAS sidecar）、#388（冻结时钟 prefix-cache）

## 1. 背景与问题

Pi 每轮看到的"GIS 世界"是一组**无来源、无新鲜度、无 revision 语义**的
文本块：env block（`_build_environment_turn_context`，纯前端内嵌快照）、
V6 三层块、verdict 块。事实分散在 5+ 处副本（viewport 有 5 个副本；
legacy env summary 读取 #811 后已无写方的死键 selected_feature/
focus_layer_id/user_location），没有单一可 diff、可查询、可预算的
结构化情境。模型无法区分"确定事实 / 未知 / 已过期 / 源失败"，也无法
稳定感知"上一轮以来世界发生了什么"。

## 2. 决策

新建 `app/services/gis_situation/`（**不并入** `gis_world_state` —— 后者
是 mutation 门面 + pull 型快照工具，本包是 per-turn push 编译；两者共享
同一批权威 store，不建第二事实源）：

1. **契约**（`facts.py`/`contract.py`）：`SitFact{value, status∈
   {known,unknown,stale,unavailable}, source, observed_at, revision,
   ref, confidence}` + `GISSituation`（identity/revision + 十 context +
   evidence）。extra=forbid；**unknown 显式成事实，不猜 0/false**；
   绝无 payload（ref-only）。
2. **复合 revision 单调**（DC-5）：`(mutation_revision, observation_seq,
   interaction_seq)` 字典序；`diff_situation` 对倒退编译产出
   `regressed=True`；`advance_snapshot` 对倒退**拒收** —— 迟到的 SSE/
   前端观察不能把会话情境状态拉回去。
3. **SituationCompiler**（`compiler.py`）：唯一生产编译器。固定扇出
   `asyncio.gather`（5 源），**单次** `get_map_state` 全量读（PERF-08）；
   descriptor-first（O(1) 预计算描述符 ≤24 个，绝不 resolve payload）；
   partial source unavailable → 相关派生事实 `unavailable`（可归因）+
   `evidence.sources_unavailable`，编译不失败。
4. **前端交互观察**（`observation.py` + WS handler
   `situation_interaction`）：小型结构化观察帧（封闭 kind 词表 + payload
   白名单 + 512B 总预算 + 嵌套键数封顶）→ 内容寻重去重 +
   client_generation 单调 + 有界环（32）`_situation_interactions`。
   v1 前端继续走既有 per-turn 快照通道（已满足"下一轮感知"），WS 通道
   为增量接入预留（前端无通用感知 WS 客户端，不为本任务新建）。
5. **有界投影**（`projection.py`）：`render_situation_for_context`
   输出 `[GIS 情境]` 块，4096B hard cap（`_cap_text` UTF-8 边界截断 +
   truncated 留痕）；delta 变更事实带 `*` 标记 + 块首"本轮变更"行；
   条目超限记 `(+N omitted)`；核心维度 unknown 显式成行，可选维度全
   unknown 整节缺席；用户可控字段 `_xml_fence` 转义；确定性（固定节序、
   无 wall-clock —— `compiled_at` 用 #388 会话冻结时钟）。
6. **生产接线**（`chat.py` 两个 Pi 分支）：`environment_context` 位改为
   `build_situation_turn_context()`（compile → diff → advance → 投影）；
   `GIS_SITUATION_CONTEXT=0` / 任何异常 → 逐字节回落
   `_build_environment_turn_context` 原文（fail-open）。turn marker
   仍由 `bind_turn_prompt` 保持最后。**不新增注入通道**（与 v6/carto 块
   互斥补位：本投影接管 env_block 位，不重复 verdict/findings 行）。
7. **查询 API + 一致性**（`queries.py`/`consistency.py`）：业务代码经
   命名事实查询（role→ref、visible layers、scope、user locks、delivery、
   constraints），不再散落地读 map_state 键；一致性检查输出可序列化
   inconsistency（observed 落后 desired、选中/聚焦宿主层被移除、
   user_hidden 冲突= user wins、verdict 指纹失配、后台任务在途、源失败），
   `reconcile_fact_views` 只做状态级协调（死选中降级 stale），不做
   mutation 事务（方向 8）。
8. **观测**（`scripts/situation_inspect.py`）：CLI/JSON 双形态（facts/
   stale/omitted/conflicts/projection 预算）；`--dump-schema` 刷新
   `docs/dev/situation-contracts/gis-situation.schema.json`（契约测试
   守护漂移，scripts 是唯一改动口）。

## 3. 不做（防重复/边界）

- 不建持久化 Spatial Memory（方向 9）；不做 mutation 冲突完整事务
  （方向 8）；不重做 Data Fabric descriptor；不重写 Pi history memory。
- 不动 legacy ChatEngine 组装路径（回退路径保持原样）。
- 不新建 HTTP 观察端点（避免 OpenAPI/scope-matrix 生成物扰动；WS 通道
  同为服务端强制去重）。
- 不持久化 GISSituation 到 DB/迁移（`_situation_snapshot` 随会话态存储）。

## 4. 兼容与回滚

- 默认开启，kill-switch `GIS_SITUATION_CONTEXT=0` 即回 legacy env block；
  编译/投影任何异常同路径回落（turn 永不因情境失败而失败）。
- 新增 map_state 键（`_situation_snapshot`/`_situation_interactions`）
  均为下划线内部键，WS 快照白名单与 REST 写路由天然拒写（#811/#643
  语义不变）。
- 契约演进：`extra=forbid` + 快照 from_dict 失败按"无快照"处理（前向
  兼容，不阻断）。

## 5. 证据

- 47+5 项测试：契约/编译/降级/确定性/diff 单调/快照只前进/摄入去重/
  投影预算/查询/一致性/turn 集成/规模基准（10/100/1000 层）。
- 1000 层合成会话：compile < 5s（实际秒级下探），投影 ≤ 4096B，
  快照 < 64KB；10 万要素仅以计数出现。
