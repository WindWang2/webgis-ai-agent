# Decisions — GIS Spatial Reasoning Memory v1（方向 9）

决策格式：背景 → 决策 → 理由/放弃的备选。编号 D1…Dn 持续追加。

## D1 新包 `app/services/gis_memory/`，不并入 cartography/project_memory

- 背景：ADR-0069 账本严格 project 作用域、4 种制图 kind、注入通道是 `[CARTOGRAPHY_MEMORY]`；
  方向 9 需要 session/project/user 三作用域、10 种 GIS kind、按情境检索 API。
- 决策：新建 `gis_memory` 包 + 新表；与 ADR-0069 的关系是**读写收敛**（见 D4/D6），不是合并。
- 理由：把 session 作用域塞进 project 表会破坏 ADR-0069 决策 1（查询恒带 project 谓词）的审计口径；
  反过来把 10 种 GIS kind 塞进 FACT_KINDS 会让制图账本的 closed vocab 失去语义密度。
- 放弃：扩展 FACT_KINDS（破坏既有 closed-vocab 评审纪律）；只做文本块不做 API（无法支撑 R6 生产复用）。

## D2 一张表 `gis_spatial_memories`（migration 0072）覆盖 session/project/user 作用域

- 背景：会话事实天然短命、项目/用户事实长命；两套存储（session 平面 + DB）会让
  supersession/GC/tenancy 出现两套语义。
- 决策：单一 SQLAlchemy 表 + `scope` 列（`session|project|user`）+ `scope_id`；
  session 行靠 TTL + GC 预算回收；org_id 列 + `scoped_query` 强制租户谓词。
- 理由：一个契约、一个 GC、一个 tenancy 面；memory_harvest 已确立 sync-DB + to_thread 模式。
- 放弃：session 记忆放 Redis/map_state（跨 worker 语义分叉，且 GC/supersession 要写第二遍）。

## D3 记录契约（`SpatialMemoryRecord`）

`id, kind(10), scope, scope_id, org_id, user_id?, subject, value(JSON≤2KB), refs(JSON ref-only),
evidence{source, method, turn_id?}, fingerprint(语义哈希), confidence[0,1], status(active|superseded|invalidated),
expires_at?, created_at, last_validated_at, version(int 单调), supersedes_id?, sensitive(bool),
invalidation_rule(ttl|dataset_version|manual|scope_gone)`

- 不记：raw prompt、CoT、完整 payload、credential（policy 拒绝 + sanitizer 兜底）。
- 序列化：contract.py dataclass + `to_bounded_dict()`；schema JSON 导出到
  `docs/dev/spatial-memory-contracts/gis-spatial-memory.schema.json` + 漂移测试守护（对齐 #1275 先例）。

## D4 写侧收敛：偏好走 ADR-0069，其余走新表

- 决策：`policy.py` 的 `MemoryWriteGate` 对 `user_cartographic_preference` 类意图
  **路由**到 `cartography.project_memory.record_fact(kind="preference")`（有 project 时），
  其余 kind 落新表；读侧（检索/投影）做 read-through 合并两者。
- 理由：任务红线「不得重新建一套 cartography preference DB」；且 ADR-0069 的
  conflict/expires/LRU 纪律已评审过，不复制。

## D5 写策略（R2）：evidence-gated，fail-closed

- 决策：写入必须同时满足 `evidence.source ∈ 生产事实源`（intent 解析、评审通过、
  dispatch 结果、pin store、provenance user 决策）、`confidence ≥ 阈值（默认 0.6）`、
  `scope + scope_id 显式`；模型自由文本不得直接成为记忆。
  失败/降级可记但**必须带 expires_at**（provider_failure 默认 7d，crs_resolution 默认 24h）。
- 理由：「记忆永远滞后证据一个身位」（ADR-0069 决策 2）的全仓纪律。

## D6 矛盾/取代（R3）：显式 supersede，绝无静默 merge

- 决策：同 `(org, scope, scope_id, kind, subject)` 已有 active 行且 fingerprint 不同 →
  新行 active、旧行 `status=superseded, supersedes_id` 链保留（可审计）；
  用户纠正（evidence.source=explicit_user_correction）直接赢；
  learned-vs-learned 冲突 → 高置信者胜，败者保留 superseded 链。
  检索/注入永远只出 active。
- 理由：与 ADR-0069 决策 3 同款但方向相反——那边是「不确定就挂起（conflicted）」，
  本表的多是**可被新证据直接推翻的世界事实**（place/dataset/CRS），挂起会把「成都→高新区」
  的正确纠正卡死；故以 supersede 链替代 conflicted 态，保留完整审计链。

## D7 检索（R4）：谓词过滤 + 有界 top-k + 理由

- 决策：`retrieve_memories(context)` 先走必过滤（org 租户、scope 集、active、未过期、
  dataset 版本兼容），再按 `subject 匹配 > kind 权重 > confidence > freshness` 打分，
  返回 ≤8 条，每条带 `reasons[]`（matched_subject/kind_weight/fresh/confidence）。
  绝不整库进 prompt。
- 理由：Zero Big Data in Context；[CARTOGRAPHY_MEMORY] 的有界注入纪律外推。

## D8 Pi 接线（R5/R6）：单通道追加块 + 工具内复用，narrow interface 对 situation

- 决策：
  1. `[GIS_MEMORY]` 有界块在 `_build_cartography_turn_context` 返回值处拼接
     （Pi 单注入通道纪律；顺序 verdict → cartography memory → gis memory）；
  2. `webgis_map_intent` 在 scope 解析失败时以记忆补全（走 `hint_applied` 披露，
     fresh 命中优先，置信度折扣 0.9×）；
  3. situation（#1275 未合并）→ `queries.py` 暴露 narrow
     `MemoryProjectionInput` protocol + fixture；不复制 SituationCompiler。
- 放弃：新增 attach_turn_context 参数（与 #1275/#1277 的签名演进冲突面最大）。

## D9 GC（R7）：harvest 位点同步扫，不引后台 worker

- 决策：turn 端 harvest 时顺带 `sweep`（过期失效 + scope 消失检查 + 每 scope 预算
  LRU 淘汰 + dataset_version 失效抽检）；预算 session≤80 / project≤400 / user≤200 每 org。
- 理由：与 harvest 同事务位点，最省；后台 worker 属 data-lifecycle 域，不越界。

## D10 ADR-0183 / 迁移 0072（手动分配 + watermark 推进）

- open PR #1274/#1275/#1277 已占 0180；#1276 占 0181；#1278 占 0182 → 本分支取 **0183**；
  `ownership.json.adr_watermark: 137 → 183`。
- 迁移按官方 allocator（单 head 校验通过）：`0072_gis_spatial_memories`，
  down=`0071_ads_acquisition_facts`；`migration_watermark: 48 → 72`；
  `.alloc.json` segments 增记本分支声明。rebase 后重跑 allocator 复核。

## D11 eval（R9）：wrong/stale reuse 一票否决式扣分

- 决策：语料 32 场景（覆盖任务列出的 7 类轨迹）× 记分
  `score = 3×useful_reuse − 4×wrong_reuse − 4×stale_reuse`，另报
  retrieval_precision / context_bytes_saved / tool_calls_saved。
  wrong/stale 权重高于 useful（任务要求「比低 recall 更严重」）。
