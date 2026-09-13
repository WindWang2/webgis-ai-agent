# GIS Situation / World Model — Decision Log

追加式决策记录。每条：背景 → 决策 → 理由 → 回滚面。

## DC-1 新包 `app/services/gis_situation/`，不并入 `gis_world_state`

- 背景：`gis_world_state`（ADR-0072）是 mutation 门面 + pull 型快照工具
  （`webgis_world_state`，agent 主动拉）。本任务是 per-turn push 编译 +
  diff + 预算投影 + 查询 API，生命周期与消费方不同。
- 决策：新建兄弟包；共享事实源（同一 map_state/mapspec/provenance 读取口），
  不复制存储、不建第二 registry。
- 理由：单一职责；避免把 `build_world_state`（工具面契约）与 turn context
  预算语义耦合；两者可各自演进。
- 回滚：包整体可摘除（接线点 flag 关闭即回 legacy env block）。

## DC-2 Pi 路径接线：接管 `env_block` 位，flag + fail-open fallback

- 背景：Pi turn 注入唯一通道是 `bind_turn_prompt` 的多个可选块；env_block
  位当前由 `_build_environment_turn_context(req.map_state)` 纯前端快照文本填充
  （无 source/freshness/revision 语义）。
- 决策：chat.py 两个 Pi 分支的 `environment_context` 构造改为
  `build_situation_turn_context(session_id, req.map_state, ...)`：
  内部先编译 GISSituation → 有界投影文本；**编译任何异常/flag 关闭 →
  回落 `_build_environment_turn_context` 原文本（逐字节兼容）**。
  turn marker 仍最后（复用 bind_turn_prompt 不变）。
- 理由：最小缝改（两处调用点），不新增注入通道（避免与 v6/carto 块双通道）；
  fail-open 满足"注入是增值上下文，绝不阻断 turn"的既有纪律。
- 回滚：`GIS_SITUATION_CONTEXT=0` 即回到 legacy env block。

## DC-3 与 V6 `[Map Situation]` 块的关系：结构化底层 + 摘要保持

- 背景：`v6_context_blocks.build_map_situation_block` 已是 3 行有界摘要
  （mapspec_rev/verdict/render/findings）；verdict 另有
  `[CARTOGRAPHY_VERDICT]` turn 侧注入。
- 决策：Situation 投影**不渲染 verdict/制图节**（review P1-1 落实：初版
  曾渲染 [制图] 节，与上述两通道构成同轮三重注入 —— 已删除；结构化
  verdict 事实保留在契约中，经 `queries.get_cartographic_constraints`
  消费）。投影覆盖 env_block 位原本缺失的维度：数据/时间/分析/交付/
  约束/交互/来源与新鲜度。V6 块保持原样；后续可让 V6 块从 Situation
  派生（非本任务）。
- 理由：避免双注同一事实（B/Q3 互斥先例）；byte 预算不翻倍。

## DC-4 事实契约：显式 unknown，不猜 0/false

- 决策：`SitFact` 泛型 `value: Optional[T]` + `status ∈ {known, unknown,
  stale, unavailable}`；unknown ≠ 缺席键 —— 编译器把"该维度无证据"编译为
  status=unknown 的事实而非省略（省略=模型自由发挥）。
  `confidence` 仅在来源提供校准置信时携带（intent 语义槽位），否则缺省。
- 理由：验收"stale/unknown 不被假装成确定事实"。

## DC-5 revision 单调性：复合 revision + 快照持久守卫

- 决策：`SituationIdentity.revision = (mutation_revision, observation_seq,
  interaction_seq)` 字典序单调；observation_seq 取 runtime 渲染观察与
  pre-turn 前端快照两条通道的**最大序号**（review P1-3 修订：纯交互变化
  也必须推进 revision，否则快照滞留导致 diff 每轮幻影重复）；
  `diff_situation` 拒绝 after < before（late SSE/前端观察不能倒退）；
  持久化 `_situation_snapshot` 仅当 after > stored（同 revision 保留先到，
  幂等；倒退拒收）。~~在投影标注 stale-read~~ → 实现为：倒退编译的 delta
  按无上一轮处理（不渲染幻影变更），快照保持前进态。
- 理由：验收"late event 不导致 revision 倒退"；与 `_cartographic_observation`
  的服务端盖章语义对齐（P9）。

## DC-6 S4 交互观察：WS 通道，内容寻重，generation 单调

- 决策（review P2-7 勘误：传输为 **WS perception handler**，非 REST ——
  避免 OpenAPI/scope-matrix 生成物扰动，且前端已有 WS 通道惯例）：
  `ws_service.PERCEPTION_HANDLERS["situation_interaction"]` 接受
  `{kind, payload(bounded), client_generation, observed_at}`；服务端：
  payload 规范化（封闭 kind 词表 + 键白名单 + 嵌套键数封顶 + 512B 总
  预算）→ 内容 hash 去重（同 kind 连续同载荷丢弃）→ generation 单调
  （旧代丢弃）→ 写入 `map_state["_situation_interactions"]` 环（上限 32，
  全程持 session 锁）。编译器把环投影为 InteractionContext。防抖在客户端
  （前端 v1 只上报已有节流事件；服务端去重兜底）。v1 前端继续走既有
  per-turn 快照通道（已满足"下一轮感知"），WS 帧为增量接入预留。
- 理由：不动既有两观察通道；不逐 mousemove 写整包；revision-aware。
- 回滚：handler 独立可摘；环键不存在时 InteractionContext=unknown。

## DC-7 预算与确定性

- 决策：投影 hard cap 常量（复用 `_cap_text` UTF-8 边界截断+留痕）：
  SITUATION_BLOCK_MAX_BYTES=4096（env_block 位历史尺度 ~1.5-3KB）；
  编译器确定性：同 store 输入同输出（compile_at 由调用方传入，来自
  session 冻结时钟 `_env_timestamp` 同策略），排序全部字典序/固定序；
  无 wall-clock/随机/LLM 进编译与投影。
- 理由：#388 KV-cache prefix 稳定性先例；V6 红线（确定性/有界）一致。

## DC-8 大数据纪律：descriptor-first、ref-only、有界并行

- 决策：编译器只读 `list_refs`（ref→alias）+ 最多 N=24 个 descriptor 定向读
  （`get_ref_descriptor`，惰性字段），绝不 resolve payload；mapspec layers/
  sources 摘要各自 cap 100（同 `build_world_state` 上限）；并行读取
  `asyncio.gather` 一次扇出（固定 ≤6 个 store 调用），无递归无循环读。
- 理由：Zero Big Data in Context；PERF-08 单次 map_state 读纪律；
  S8 规模基准（1000 层合成）验证 O(层) 而非 O(要素)。

## DC-9 不建持久化 situation 表/迁移

- 决策：situation 快照存 `map_state["_situation_snapshot"]`（会话态，
  随会话生命周期）；不新增 Alembic 表。
- 理由：会话级状态在会话存储已有成熟 TTL/清理语义；避免迁移面。

## DC-10 ADR-0180；文档落 docs/dev + docs/gis-harness.md 增补

- 决策：ADR 编号 0180（0160-0179 已占）；设计细节以
  `docs/dev/situation-world-model-*.md` + ADR 承载；`docs/gis-harness.md`
  增补"情境 ≠ 聊天历史"一节（S9）。
