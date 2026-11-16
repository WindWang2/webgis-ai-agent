# G45 INTERFACES — 对外契约速查

## HTTP（/api/v1/coord-ops，全部 require_owned_session 守卫）

| 方法/路径 | 用途 | 关键语义 |
|---|---|---|
| POST /sessions/{sid}/describe-source | 源资格卡 | CRS 证据（载体元数据）、行数、几何、canonical CRS 描述；typed：source_crs_missing/source_incomplete/source_unqualified/budget_exceeded |
| GET /crs/lookup?q= | CRS 描述 | 官方轴序（仅展示）/单位/维度裁决；compound/dynamic/垂向 → dimension_supported=false + 原因 |
| POST /sessions/{sid}/operations | 候选列表 | available+unavailable 各 ≤16（溢出计数）；环境证据；AOI 单 bbox（跨反经线拒绝） |
| POST/PATCH/GET/DELETE /sessions/{sid}/plans[...] | 计划 CRUD | revision CAS；实质编辑 → 回 draft + 清锁定/预览 |
| POST /sessions/{sid}/plans/{pid}/lock | 锁定候选 | 只收服务器 candidate_id；重算候选集匹配后冻结 PROJJSON+环境；成功后 status=locked, revision+1 |
| POST /sessions/{sid}/plans/{pid}/preview | 有界预览 | ≤50 行样本、≤8 坐标对、findings、accuracy 语义；不发布成果 |
| POST /sessions/{sid}/plans/{pid}/execute | durable 执行 | 幂等键=TASK_TYPE:sid:plan:digest:src_fp（世代键）；background 默认 true |
| GET /sessions/{sid}/runs[/{rid}[/results]] | 运行/分页 | 分页 ≤200/页；completed 才有 results |
| POST /sessions/{sid}/runs/{rid}/cancel | 协作取消 | job 行取消请求 + 看门狗；已终态幂等 |
| POST /sessions/{sid}/runs/{rid}/display-ref | 重铸显示副本 | WGS84 派生物；主成果独立 |
| POST /sessions/{sid}/recover | 孤儿收敛 | 崩溃窗口按权威文件/manifest 恢复 |

## Pi 工具（webgis_coordop_*，registry tier2 / dataset）

describe_source / crs_lookup / list_operations / plan_upsert / lock / preview /
execute / status(wait_max ≤60s) / result / cancel。source/plan 参数带
`ref_cursor: true` 声明（#1004 通道，防透明解引用破坏源锚身份）；session_id
由 dispatch 注入（模型不可指定）。

## 持久模型（migration g45_coordinate_operations，down=g40_…）

- coordinate_operation_plans：revision CAS、plan JSON ≤32KB、operation TEXT
  （冻结 PROJJSON+环境+candidate 摘要）、preview ≤16KB、CHECK 词表与 DDL 一致
- coordinate_operation_runs：job_id 关联 analysis_tasks（活性/取消/fencing 事实源）、
  idempotency_key 世代、receipt ≤16KB、output_ref

## 成果布局（DATA_DIR/coord-ops/）

- staging：source-frozen.parquet|json + output.geojson（权威，CRS 成员+
  schema_info.crs）+ output.parquet（GeoParquet 投影）+ manifest.json
- 终态：原子更名 + receipt.json（可回读才 completed）
- refs：ref:coord-ops/<run>（台账）、ref:fabric-parquet/<id>（原生交付载体）、
  ref:coord-ops-display-<…>（WGS84 显示副本）、ref:coord-ops-output-<…>（全量
  session FC ref，G18 session_ref 交付）
- DataObject kind=coordinate_operation（白名单已登记）；JobKind=coord_operations

## 与相邻域的边界

- G18 data_delivery：交付格式语义复用（GPKG 无 target_crs 不再选操作；
  GeoJSON 走本域 4326 副本）
- G23 data_operations runner：submit/run_async_job + AsyncJobHandle；本域
  `strict_publish_gate` 以 fail-closed 补发布门（C39/C40）
- G38 outputs 范式：staging→回读 oracle→原子发布→CAS→台账→收据同构复用
- G40（已合并）：无依赖；G47/G48：无交集
