# G38 DEDUP_MATRIX — 与既有实现的关系判定

判定词：`present`（已存在，只做兼容回归）/ `partial`（存在但不满足本域验收，做薄增量）/ `missing`（本任务新建）/ `owned-by-other`（他人域名下，仅消费）/ `blocked`（外部阻塞）。
每项判定必须追到生产调用链；证据列 file:line（base 1276692737）。

| # | 能力 | 判定 | 证据 | 本任务动作 |
|---|---|---|---|---|
| 1 | 空间连接算法（gpd.sjoin，inner/left/right，谓词，CRS 对齐；全内存无预算） | present ✅ | spatial_analyzer.py:447-484 | 不作 G38 执行内核（无预算/无 ref）；旧接口保留 |
| 2 | `spatial_join` 工具（preflight + ref_offload 由 dispatcher 铸 ref） | present ✅ | advanced_spatial.py:2300-2356；dispatcher ref_offload = tool_dispatch_service.py:1373-1402 | 不重造；G38 新工具面走有界内核 |
| 3 | 属性哈希连接 + 双侧 null 键排除（`attribute_join_local`，`__right__` 嵌套，页序稳定） | present ✅ | federation.py:306-354,357-379 | G38 直接复用该内核（预算+流式） |
| 4 | 点面首命中连接（STRtree+精确谓词，最早输入索引确定性） | present ✅ | federation.py:228-303（within=contains/equals:280-285；first-hit:288-289） | 不当「全部匹配」用；G38 多命中策略显式化 |
| 5 | 多源 federation 工具（CHAIN_TOOL_ROW_CAP=200 + `_payload_notice` 披露） | present ✅ | data_fabric_tools.py:855-950,952-1158（cap:1030,1129,1151） | 不升级为全量成果；G38 产出真实派生载体 |
| 6 | geocompute 空间/属性连接节点（REGISTRY 分派、HARD_NODE_ROW_CAP=500k、MATERIALIZE 出口） | present ✅ | geocompute/ops.py:419-464,467-499,793-805；plan.py/graph.py | G38 不新建 DAG；计划编译到本域执行器+复用内核 |
| 7 | 查询控制台单源查询 + materialize | present ✅ | query-console.tsx:58-244 | 保留；G38 独立作者入口 |
| 8 | SourceSnapshot（4 类 ref：session_ref/fabric_parquet/project_artifact/table_rows；verify_unchanged 批读） | present ✅ | source_resolver.py:244-349,408-505,575-645 | 直接复用为双源快照基础 |
| 9 | durable job 基础（`submit_async_job`/`run_async_job`/AsyncJobHandle/fencing/幂等键） | present ✅ | data_operations/runner.py:221-247,285-372,375-477；jobs/store.py（AnalysisTask 表） | JobKind 加 `data_join`（lifecycle.py:46-59，String(20) 无 CHECK 纯加性） |
| 10 | 服务端稳定身份（wfid_for_index/wfid_for_created） | present ✅ | feature_edit/identity.py:28-44 | 派生对象身份复用 wfid_for_created 语义 |
| 11 | G24 权威选择句柄（bbox/where/ids≤200；TTL600；消费时重鉴权） | present ✅ | selection_handles.py:56-59,323-472 | 选择集只认 handle/scope |
| 12 | G18 交付（staging→原子发布、manifest/receipt、writers、DataObject CAS、下载鉴权+回读 oracle） | present ✅ | data_delivery/store.py:33-157,writers.py,service.py:823,1015-1205 | 成果载体 + 导出接续 |
| 13 | G33 专题表达 | present ✅ | thematic_authoring/ | 结果上图后的专题接续 |
| 14 | agent 面属性关联工具 | missing ✅（agent 1 复核：advanced_spatial.py 无 attribute join；仅 federation/geocompute 面） | — | WP10 新工具面（走 attribute_join_local 内核） |
| 15 | 关联计划（版本化 JoinPlan、CAS、digest） | missing | — | 本任务 WP02 |
| 16 | 双源组合指纹/冻结范围 | missing | — | WP03 |
| 17 | 全量键/基数/未匹配诊断（预检） | missing | — | WP04 |
| 18 | 字段映射/别名/冲突策略/输出 schema 合同 | missing | — | WP05 |
| 19 | 多命中策略（唯一/全部/确定性择一/聚合） | missing | federation 只有首命中（federation.py:288-289） | WP06 |
| 20 | 执行阶段预算（input/candidate/output/内存） | partial | federation StreamingBudget + geocompute ResourceBudget 各自为政；SpatialAnalyzer 无预算 | WP07 在本域计划内统一（上限取更严者） |
| 21 | 派生成果身份 + lineage 关系表 | missing | — | WP08（复用 wfid_for_created 语义 + ArtifactLineage） |
| 22 | 本域 job 幂等/恢复/保留 | missing | — | WP09（复用 G23 submit_async_job 范式；TASK_TYPE=`data_join_execute`） |
| 23 | describe/plan/preview/execute/inspect/cancel/list REST + Pi 工具 | missing | — | WP10 |
| 24 | 作者工作台 UI（诊断卡、异步执行、结果接续） | missing | — | WP11 |
| 25 | J01–J28 验收证据 | missing | — | WP12（既有 oracle 参考 tests/unit/gis/test_algo_conformance_v2.py:200,344） |
| 26 | 迁移槽位 | — | 下一槽 `0107_*`，down_revision=`0106_lifecycle_policy_org_unique`；模型登记 app/models/__init__.py | WP02 落 DB 用 |
| 27 | 外部数据库写回 / 任意 URL 远端连接 | blocked-by-design | — | 明确不做 |
| 28 | G19 字段表达式 / G10 refresh / G09 批分析 / G21 OD / 地名消歧 | owned-by-other | — | 不做 |

## 复核状态

- [x] base SHA 无漂移（origin/master = 1276692737）
- [x] open PR=0、open issue=0（#2065/#2066/#2070 已修；PR review 通道抽样 #2089/#2094/#2100/#2110/#2116 均空）
- [x] 后端 file:line 逐项复核（research agent A 报告，2026-11-14；判定已回填 ✅）
- [ ] 前端/工具接线惯例复核（research agent B 报告后回填）
