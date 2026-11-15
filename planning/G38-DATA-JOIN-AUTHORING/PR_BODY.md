# G38 数据关联与空间富集作者工作台

## 用户问题

用户有两个数据源（如行政区边界图层 + 人口表，或监测点 + 区域面），需要把它们关联成一个**完整的派生数据成果**并接续制图/交付。既有能力各有缺口：`spatial_join` 工具全内存无预算无身份/lineage；federation 查询是首命中/截断的查询面（200 行上下文帽）不是成果产物；geocompute 有节点但无作者语义（键变换/基数政策/字段冲突/诊断）。用户无法审阅基数与未匹配、无法固定输入版本复算、拿不到可对账的完整成果。

## 行为增量（本 PR）

两源绑定 → 键/空间关系与基数策略 → **全量诊断**（双侧计数/重复键/空键/未匹配 + 有界样本；违规 typed 阻断）→ 冻结输入执行（指纹复核，durable job）→ **完整派生成果**（GeoJSON/rows 终态包 + sha256 回读 oracle + DataObject CAS + ref:data-join 台账 + 可回读收据）→ 结果分页（全量计数口径）/ 加入地图（≤10k 权威渲染 ref）/ G18 导出接续。

语义合同（可人工推导 oracle 锁定）：null/missing 双侧永不匹配（null≠null，先于任何内核）；类型分族 `"001"≠"1"≠1≠True≠""`（数值 1==1.0 与 federation 对齐）；显式键变换（trim/casefold/zfill）计入 digest；基数声明制（1:1/N:1/1:N，many-to-many 乘积分配前 typed 拒绝）；空间 `within`（严格内部）与 `within_or_boundary`（边界包含）两谓词、洞排除、未声明/偏移 CRS typed 阻断、异 CRS 点侧变换留证；多命中 require_unique/expand_all/pick_by_field（同值并列拒绝）/aggregate（闭合聚合集，null 不当 0，valid_count 分母）；字段映射冲突与保留字段（`_wfid`/lineage 列）typed 拒绝；身份：1:1/N:1 保留左 `_wfid`，1:N 服务端派生身份（wfid_for_created）+ 左右父 locator lineage。

## 复用与范围（不重造）

执行活性/取消/fencing = G23 durable runner（JobKind.data_join 纯加性）；源解析/授权/快照 = G18 source_resolver（工具平面经受信 session 回退，跨会话仍拒绝）；内容 = DATA_DIR/data-join 终态包 + DataObject CAS（单一可写副本）；身份原语 = G14；选择集 = G24 句柄（>10k 由其闸拒绝，本域不静默截断）；federation/geocompute 内核零改动（差分测试对拍 attribute_join_local）。不做：外部库写回、任意 URL 连接、多步 ETL、线面空间、G19 表达式、nearest/缓冲。

## 实际验证

- 后端 61 项单测：J01–J14/J17/J19–J28 人工推导 oracle + federation 内核差分 + 幂等/恢复/越权 + 性能曲线与取消收敛。
- **真实 Pi 旅程**（PI_JOURNEY_E2E=1，1 passed）：stub LLM → 生产 uvicorn → 真实 vendor Pi 子进程 → `/pi-tools` → `webgis_data_join_*` 全链 —— 1100 匹配行成果完整（非 200 行截断，J15）、收据/输出文件/抽值/CRS/身份核验、G18 真实交付下载回读一致（J25）。
- 前端 vitest 4 项（诊断如实披露/全量计数/渲染 ref 上图/纯表诚实禁用）+ tsc + eslint 0 警告。
- 迁移 0107 领号（.alloc.json 段位）+ `alembic upgrade head` 干净库实测；openapi/realtime/quality 派生工件按仓库流程再生成。
- 性能：属性 50k×50k（默认预算上限）≈1.6s/130MB 峰值；空间 2000 点×40 面 0.11s；取消 ≤ 看门狗周期收敛。

## review / fix / rereview

独立 reviewer（未参与实现）全量 diff 审查 → 修复 → 同 reviewer 复审通过。记录见 `planning/G38-DATA-JOIN-AUTHORING/REVIEW.md`。

## 兼容 / 迁移 / 回退

共享文件全部最小加性（tools 注册表 1 行、JobKind 1 枚举、capability 1 描述符、registry skip_keys 白名单 2 工具、路由挂载、i18n、.alloc.json 段位）。新表 migration 0107（down_revision=0106，照抄 0105 create_all 共存 guard；downgrade 反序删两表）。回退 = revert PR + downgrade 0107；无既有行为变更。

## 最新并行 PR 兼容（#2117–#2120）

执行时 master 未漂移（base=1276692737）。并行兄弟任务 G36/G37/G39/G41 与本 PR
的共享面仅 `app/services/jobs/lifecycle.py`（各自加性 JobKind 枚举成员）与
`app/main.py`（各自加性路由挂载）—— 语义零冲突；若合并顺序产生上下文行冲突，
解决方式为保留双方加性行（各 1-2 行）。无共享服务/模型/迁移文件。

## 未测项与已知边界

- master 既有失败 5 项（干净 base 复现，与本分支无关，清单见 TEST_EVIDENCE）。
- 渲染投影上限 10k：更大输出走结果分页/导出（诚实 typed 披露）；pyarrow 不可用环境下权威载体为 GeoJSON/rows-JSON（类型保真；GeoParquet 为可用时的加性投影）。
- 执行为 API 进程内 asyncio runner（G18/G23 同款范式）；生产 Celery worker 不承载本域 job。
