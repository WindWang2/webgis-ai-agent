# G30 DEDUP_MATRIX — 逐项判定（present/partial/missing/owned-by-other/blocked）

判定基线：origin/master `ca7b3ec273f2b43336852951e2292a416b2238e6`。每项追到生产调用链。

| # | 能力面 | 判定 | 证据（生产调用链） | G30 增量 |
|---|---|---|---|---|
| 1 | 技能契约（version/roles/procedure/obligations/evidence） | **present** | contract.py:114-357；10 个 core YAML | 只做 additive 参数/执行映射字段（本任务薄配置），不重造 |
| 2 | 技能知识咨询工具（search/detail/replay/policy） | **present** | skill_library_tools.py 4 工具全注册（`app/tools/__init__.py:76`） | 不动；新增 prepare/start/status 为**新类别**（执行面），不覆盖知识面 |
| 3 | 选择/策略治理（policy tier/execute_guided 门槛） | **present** | policy.py:331-336；hotpath.py:92-137 | 复用；显式选择只消除"选哪个"歧义，不改 eligibility |
| 4 | plan seam 技能投影 + procedure_binding | **present** | tool_map_intent.py:243-347（C1/C2/C3，gis_chapter 持久化） | prepare 预览复用同一 binding 语义；run 记录引用 binding |
| 5 | **用户显式选择技能版本的执行闭环（prepare→start→run→结果复用）** | **missing** | 全链无调用点：evidence recorder step 事件零生产调用（ADR-0220 §1）；无 skill job 行；UI 无执行入口 | **本任务核心** |
| 6 | 有类型参数 schema + 数据角色绑定（field/unit/ref 校验） | **missing** | YAML 无 parameters；工具 arg 直接吃裸值 | 新增声明式薄配置 + prepare 期校验 |
| 7 | `chat.skill_name` 生效 | **missing（披露忽略）** | chat.py:1402-1416 | 解析→唯一/歧义→turn ctx→plan seam 生效 + 披露更新 |
| 8 | durable job 运行时 | **present** | jobs/store.py、data_operations/runner.py（G23） | JobKind += skill_job（加性）；复用 runner 范式 |
| 9 | SessionPlan 步骤真相 + apply_tool_result | **present** | session_plan.py:823 | run 的工具经统一 dispatch 自动喂它；不建第二步骤真相 |
| 10 | 完成证据/验收（map/report/file 分型） | **present（G28）** | goal_satisfaction/contracts.py + receipts | run 记录按结果类型挂接证据 refs；不建第二 verdict 引擎 |
| 11 | artifact/ref 铸造与复用 | **present** | tool_dispatch_service.py:1444+（ref:analysis-*）；G13 M5 reuse | 报告类结果存 session store + job result_ref；复用走既有 find_reusable_artifact 语义 |
| 12 | 技能工作台 UI | **missing**（skills-hub 只读 md 目录+管理员上传） | skills-hub.tsx:37-76；api/skills.ts 只 GET /chat/skills | 新工作台（目录类型化→绑定→参数→预览→启动→结果→复用）；保留上传权限面 |
| 13 | 参数预设（保存/复制/换数据重跑） | **missing** | 无表、无 API | 新 presets 表 + 迁移 + API（无既有模型可承载：预设是用户/项目可见性资产，非一次性 job 参数） |
| 14 | extension candidate 技能 | **present（candidate-only）** | pack_catalog.py:87-94；loader 无 overlay | 目录里如实显示"仅指导/不可运行"，不晋级 |
| 15 | md-skill 目录（app/skills/*.md + upload） | **present** | app/tools/skills.py；config.py:243-269 | 保持分离：目录投影标注资产类型（gis_skill vs md_guide），不按同名合并 |
| 16 | 取消/重试/幂等 | **present（jobs 层）** | jobs lifecycle/store；G23 语义矩阵 | skill job run 全部落其上；浏览器刷新→JobView 恢复 |
| 17 | Pi RPC/extension 底层 | **owned-by-other（G29）** | vendor/pi pin v1.5.2 | 不改 pin、不加 RPC；技能工具走既有注册面（deferred 自动可发现） |
| 18 | G31 质检规则引擎 / G32 变更提案 / G33 专题编辑 / G34 版式 / G35 媒体 | **owned-by-other** | D 表边界 | 不实现；技能执行只消费既有工具 |
| 19 | G09 参数化批量分析 / G21 OD 流图 | **owned-by-other（曾被分配）** | 无 PR 检出，不重占 | 不碰批量分析/OD 域 |
| 20 | 归纳 induced pack / shadow | **present** | promotion.py；policy _pick_shadow | 目录显示"草案/仅指导"，不自动晋级 |

## 三条示例旅程的工具链核查（WP01 前置）

| 技能 | 生产工具（capability 实测映射） | 参数/结果类型 | 状态 |
|---|---|---|---|
| validate_spatial_data | `audit_spatial_quality`（dataset_profiling_quality；project_tools.py:323，side_effect=deterministic_compute） | geojson→report dict（5 维审计+issue counts） | present，可直接执行 |
| point_distribution_analysis | QC：`audit_spatial_quality`；表达：`webgis_layer_upsert`（thematic_cartography；cartography_tools.py:403，MapSpec 图层+编译发布 runtime） | ref/inline→map layer + 视角 | present |
| choropleth_map_design | `create_thematic_map`（thematic_cartography；cartography.py:338，分类/图例/量纲检查/layer_meta） | geojson+field→map product + legend_spec | present |

结论：三个技能的执行目标工具全部真实注册；缺的是「版本选择→参数绑定→prepared call→统一派发→run 记录→结果消费」的领域层，不是算法。
