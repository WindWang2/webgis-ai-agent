# G45 PLAN — 可审阅、可固定坐标操作的派生矢量重投影

## 模块地图（新增文件；共享文件仅加性接线）

```
app/services/coordinate_operations/
  __init__.py
  contracts.py      # typed 错误码/预算常量/错误类（G38 errors.py 同构）
  crs_contract.py   # WP03: CRS 描述/轴/单位/维度/epoch 合同（canonical 描述 + digest）
  candidates.py     # WP04: TransformerGroup 候选（available+unavailable+格网证据+AOI）
  plan_store.py     # WP05: 计划持久化/CAS revision/canonical digest/环境证据
  freeze.py         # WP02: 源资格 + 持久冻结副本（payload→canonical JSON / parquet→受验拷贝）
  preview.py        # WP06: 有界样本预览（before/after、AOI 适用性、非有限、oracle 支撑点）
  execute.py        # WP07/WP09: durable 执行体（批读冻结源→冻结操作变换→预算闸→staging）
  outputs.py        # WP08: 原生 GeoParquet 成果（target CRS）+ WGS84 显示副本 + 发布/收据
  service.py        # 编排：describe_source/list_crs/list_operations/plan CRUD+lock/preview/
                    #   execute/status/result/cancel + 严格发布门 + 恢复
app/schemas/coordinate_operations_schema.py
app/models/coordinate_operations.py     # coord_op_plans + coord_op_runs
app/api/routes/coordinate_operations.py # /coord-ops/**
app/tools/coordinate_operations_tools.py
migrations/versions/g45_coordinate_operations.py
frontend/lib/api/coordinate-operations.ts
frontend/lib/hooks/use-coordinate-operations.ts
frontend/components/sidebar/coordinate-operations/coordinate-operations-panel.tsx
frontend/messages/{zh-CN,en-US}/coordinateOperations.json
tests/coordinate_operations/            # 单元+域集成（oracle/预览/执行/取消/预算/权限）
tests/integration/test_pi_g45_coordinate_operations_journey.py
planning/G45-COORDINATE-OPERATIONS/*    # 本目录
```

## 核心不变式（评审与测试锚点）

1. **操作锁定**：执行只用 `Transformer.from_pipeline(冻结 PROJJSON)`；候选选择只接受
   服务器签发 candidate_id；环境（pyproj/PROJ/格网摘要）漂移 → `OPERATION_ENV_CHANGED`，
   绝不静默 fallback 后沿用旧 operation 摘要。
2. **轴序合同**：全链 always_xy；载体（GeoJSON/GeoParquet WKB）一律传统 GIS x/y；
   官方轴序仅展示。EPSG:4326 官方 lat-first 绝不触发二次交换（C02/C03 用非对称点裁决）。
3. **源冻结**：payload 源 → 确定性 canonical JSON 冻结件；parquet 源 → 拷贝+sha256 验证；
   执行只读冻结件；计划绑定 fingerprint，执行前复核（C25/C26）。
4. **all-or-nothing**：任一非空几何变换失败/非有限 → run failed，不发布完整成果；
   空/null 几何独立计数保留（C18）。
5. **严格发布门**：发布前持久取消/claim/attempt 查询失败 → 不发布（可恢复阻断），
   不继承 runner ensure_not_cancelled 的 fail-open（C39/C40）。
6. **有界回执**：Pi/预览只回坐标对 ≤8、行数、条件、授权 refs；不回全量坐标/格网路径。
7. **Z 政策**：默认 preserve_unconverted（逐值保真 + 回执明示）；M/4th/垂向/compound/
   动态 datum/epoch → typed unsupported（C14/C16）。
8. **身份**：`_wfid` 保留；派生 id = `<src_id>` 保留 + lineage 记录 op digest（不重编号）。

## 关键 API（HTTP /coord-ops，Pi 工具同合同）

- POST /sessions/{sid}/describe-source → 源资格 + CRS 证据 + 几何/字段摘要
- GET  /crs/lookup?q= → 目标 CRS 查询（名称/代码/描述/轴/单位/范围）
- POST /sessions/{sid}/operations → 候选列表（available+unavailable+格网+AOI 依据）
- POST/PATCH/GET/DELETE /sessions/{sid}/plans[...] → 计划 CRUD（revision CAS）
- POST /sessions/{sid}/plans/{pid}/lock → 锁定 candidate（冻结定义+环境）
- POST /sessions/{sid}/plans/{pid}/preview → 有界预览
- POST /sessions/{sid}/plans/{pid}/execute → durable run（幂等键：owner+源指纹+plan
  digest+operation digest+输出政策）
- GET  /sessions/{sid}/runs[/{rid}[/results]] · POST .../cancel · POST .../display-ref

## 里程碑

- M1 后端域包+模型+迁移+路由+工具（WP01-05、11 部分）+ 单元测试（含 oracle）
- M2 执行链+成果+显示副本+取消/恢复（WP06-09）+ 域集成测试
- M3 Pi 旅程（真实子进程）+ 前端 UI + 浏览器旅程（WP10/11）
- M4 生成物刷新 + 场景台账 C01-C40 + 独立评审 + 修复复审 + PR

## 资源预算（默认）

max_rows=200_000；max_total_vertices=5_000_000；max_single_geom_vertices=200_000；
max_output_bytes=512MiB；preview rows ≤50、坐标对 ≤8；display ref ≤10k 行。
（不高于 G18 现行上限；更严部署配置服从更严值。）
