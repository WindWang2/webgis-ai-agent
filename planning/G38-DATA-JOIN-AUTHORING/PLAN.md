# G38 PLAN — 数据关联与空间富集作者工作台

BASE_SHA `1276692737` · Branch `zcode/g38-data-join-authoring-20261114T151651Z`
用户闭环：**两源绑定 → 键/空间关系与基数策略 → 全量诊断及有界预览 → 固定输入执行 → 完整派生表/矢量成果 → 地图/专题/导出接续**。旗舰任务 A：人口/设施表按行政区代码关联边界（表×矢量，缺码/重复码/类型冲突可见 → 完整新图层）；旗舰任务 B：监测点归属区域（点×面，谓词与多区域匹配策略可选 → 可追溯结果）。

## 架构（新增命名空间，防并行冲突；两份 research 报告的证据见 DEDUP_MATRIX）

```
UI data-join-panel ──REST /api/v1/data-join-authoring──┐
Pi tool (webgis_data_join_*) ──ToolRegistry dispatch───┤
                                                      ▼
                              app/services/data_join_authoring/service.py（单一编排面）
                                │ plan.py(契约/digest)  keys.py(类型化键/空值政策)
                                │ diagnostics.py(全量键/基数/未匹配)  executor.py(阶段预算执行)
                                │ outputs.py(字段映射/身份/载体发布)  recovery.py(孤儿收据恢复)
                                ▼
  G18 source_resolver(SourceSnapshot×2, verify_unchanged) · G23 submit_async_job/run_async_job(JobKind.data_join)
  feature_edit.identity(wfid) · vector_carrier(features_to_arrow/table_to_geoparquet) · lakehouse.publish_data_object(CAS)
  artifact_registry(ref:data-join/<run>) · mapspec apply_gis_mutation(UpsertLayerIntent, origin=user) · G18 交付/G33 专题（消费侧）
```

### 模块职责

- `contracts.py`：封闭词表（mode/keep_side/cardinality/multi_match/null_policy/predicate/transform/agg）、ERROR_CODES（fail-closed）、预算常量、`JOIN_PLAN_SCHEMA_VERSION=1`、`METHOD_VERSION=1`。
- `plan.py`：pydantic `JoinPlan`（extra=forbid）+ 规范化 + `normalized_digest`（覆盖左右源身份/版本/范围/键/变换/映射/策略/预算；显式变换计入 digest）。
- `keys.py`：类型化键规范化。默认 `nulls_never_match`（null/missing 双侧排除，null≠null）；`"001"≠"1"≠1≠True≠""`；数值 int/float 统一（1==1.0，与 federation `_hashable_key` 语义一致）；bool 独立于 number；字符串变换 trim/casefold/zfill 显式、进 digest。**进入任何内核前统一规范化，绕开 pandas null 互配陷阱。**
- `diagnostics.py`：全量预检（在行数帽内）：两侧总行数/有效键/空键/不同键/重复键计数、预计匹配对数、左/右未匹配数、有界异常样本（≤50/侧）、基数校验（1:1 双侧唯一、N:1 右侧唯一；违规 typed 阻断）；扫描受帽 → `basis="sampled"` 如实标注。大桶先算乘积估计，超帽 typed 拒绝（J08）。
- `executor.py`：阶段化执行（scan→index/candidates→pairs→policy→assemble→publish），每阶段真实计数 + `StreamingBudget` 式硬闸；属性路径 = 右侧哈希索引（键→右行序列表）+ 左侧流式探测（与 `attribute_join_local` 同语义：null 排除、左序稳定、右侧多命中按右输入序展开）；空间路径 = 右侧面 STRtree 候选 + 精确谓词（`within`=shapely contains：边界不命中、洞内不算；`within_or_boundary`=covers：边界命中、洞内不算），CRS 对齐复用声明帧（右→左工作 CRS，仅两帧已知时），未知投影/gcj02/bd09 → typed 阻断（复用 spatial_semantics preflight 语义）。取消检查在批粒度。
- `outputs.py`：字段映射（左/右字段、别名、冲突 typed 拒绝；`_wfid`/geometry/`_join_*` 保留字段不可被右表覆盖）；输出合同区分富集图层（左几何）与纯表；聚合列 count/matched_count/valid_count + 单位；身份：1:1 与 N:1 保留左 `_wfid`（无则 locator 披露），1:N 派生 `wfid_for_created(plan_seed=normalized_digest, op_index=左序, child_index=匹配序)` + `_join_left_wfid`/`_join_right_ref` lineage 列；输出顺序 = (左快照序, 匹配序) 确定性。载体：GeoParquet（矢量）/Parquet（表）写 `DATA_DIR/data-join/<run_id>/`（staging→sha256+回读 oracle→原子 rename→`publish_data_object`(CAS)→`register_artifact`(ref:data-join/<run_id>)）；渲染投影：≤`RENDER_REF_MAX_ROWS`(10k) 时另铸 session FC ref 供上图（有界投影，非权威副本）。
- `service.py`：describe/create/get/list/patch(CAS)/archive/preview/execute/inspect/result_page/cancel + 恢复钩子。授权：`require_owned_session`（HTTP）+ 注入 session（工具）；每源经 resolve_source 重新鉴权；选择集只认 G24 handle（经 materialize 落 session ref 后作为 session_ref 源输入，>10k 由 G24 自身 422 拒绝，本域不静默截断）。
- `models/data_join_authoring.py` + `migrations/versions/0107_data_join_authoring.py`（down_revision=`0106_lifecycle_policy_org_unique`，照抄 0105 create_all 共存 guard）：`data_join_plans`（uuid PK、session/project、revision CAS、plan JSON 有界、normalized_digest、status draft/archived）与 `data_join_runs`（plan FK+revision、job_id、status pending/running/completed/failed/cancelled、receipt JSON 有界、output ref/sha、幂等键、错误码）。
- 幂等键：`data_join_execute:{session}:{plan_id}:{normalized_digest}:{left_fingerprint}:{right_fingerprint}`（G23 compute_jobs 同款收敛：活跃复用等待、completed 收据短路但先验内容可读、失败释放重提）。恢复：`recovery.py` 扫 staging 残留（弃）与「内容已发布、run 行未终态」（receipt 可验 → 补登记；不可验 → 弃+run failed，绝不假 completed）。
- 路由 `app/api/routes/data_join_authoring.py`（prefix `/data-join-authoring`，挂 `app/main.py`）+ 工具 `app/tools/data_join_authoring_tools.py`（`webgis_data_join_describe/plan_upsert/preview/execute/status/result/cancel/list`，bounded receipt、错误 dict 不 raise、session 注入）。
- 前端：`frontend/lib/api/data-join-authoring.ts` + `frontend/components/sidebar/data-join/data-join-panel.tsx`（console 式面板：zustand `use-data-join.ts` + 命令 `tools.dataJoin` + 数据源页/查询控制台入口 + page.tsx 单例渲染）+ `frontend/messages/{zh-CN,en-US}/dataJoin.json`。结果页：分页表 + 加入地图（≤10k，经 mapspec user mutation UpsertLayerIntent source ref）+ 导出交付（G18 对话框）+ 去制专题（加图后跳 layer-style-panel）。

## 关键语义决策（评审锚点）

1. **空键**：仅 `nulls_never_match`（其它值 typed unsupported）；执行器不把 null 键交给任何 merge/sjoin 原语。J05 锁反例。
2. **基数**：声明制 one_to_one/many_to_one/one_to_many；many_to_many 不提供（typed unsupported），对数估计在分配前（J08）。
3. **多命中（空间）**：require_unique（任一左点 >1 命中 → typed 阻断）/ expand_all / pick_by_field（min/max；同值或空值 → needs_decision 阻断，绝不用输入顺序）/ aggregate（count/sum/mean/min/max 闭合集，null 不当 0，valid_count 分母披露）。属性多命中 = one_to_many 展开或 aggregate。
4. **身份**：见 outputs；发布前验输出 id 唯一 + 总数对账（J07/J18）。
5. **执行 vs 预览**：同一 normalized digest + 双源指纹；执行前 `verify_unchanged` 双源复核，漂移 → `stale_source`（J17）。
6. **预算**（初始，可配，取更严者生效）：源各 50k 行、预览样本 50/侧、候选对 1M、输出 100k 行、临时 256MiB、deadline 300s、字段 ≤256；渲染 ref 上限 10k（与 G24 材料化闸一致）。

## 工作包顺序（对应任务 WP01–WP12）

WP01 语义矩阵（DEDUP_MATRIX ✅）→ WP02 契约/模型/迁移 → WP03 双源快照/范围/授权 → WP04 键/诊断 → WP05 映射/输出模式 → WP06 空间策略 → WP07 执行/预算 → WP08 身份/发布 → WP09 job/恢复 → WP10 REST+工具+目录再生成 → WP11 前端 → WP12 J01–J28 + Pi 旅程 + 性能 + 定向回归 → 独立评审→修复→复审 → PR。

## 边界（不做）

外部数据库写回、任意 URL/路径远端连接、多步 ETL 编排、G19 任意表达式、G10 刷新、G09 批分析、G21 OD、地名消歧、跨项目权限、nearest/缓冲/所有权判断、线面/面面空间关联（本版 typed unsupported）、第二套 DAG/executor/ref store。

## 资源纪律

pytest 单进程（`/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest`）；vitest 1-2 workers；重阶段 flock `/home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock`；每任务独立端口/临时 DATA_DIR。
