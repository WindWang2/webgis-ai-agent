# ADR 0286 — 数据资格判定链贯通（G03 Geodata Readiness）

- 状态：Proposed（随 zcode/goal-geodata-readiness-fabric-20261107 PR）
- 日期：2026-11-07 · 基线 master 585812ee4
- 关联：ADR-0265（GIS 数据集语义契约）· ADR-0266（Intent/Requirement IR）·
  ADR-0270（large data plane vNext）· ADR-0279（spatial semantic contract）·
  ADR-0171（ads-v1 声明式源）· ADR-0082（raster refs）
- 编号说明：adr watermark 285 → 286。

## 背景

G03 审计（`planning/G03-GEODATA-READINESS/BASELINE.md`）确认：ADR-0265 之后，
语义身份证、五态资格、16 类 fabric adapter、IngestPipeline 与生命周期失效权威
均已存在，但链路在**六个接缝断裂**——Agent 走裸分析工具时资格裁决不生效、
Explorer 探索产出无语义身份、远程 GeoJSON URL 无真实 adapter、preflight 无
任务范围比对、切城市不触发重资格、license 与维度级 provenance 不流入 ingest
正向路径。本 ADR 只**贯通既有底座**，不新增第二资格词表/第二状态权威。

## 决策

### D1 维度级 provenance 三态（GD-01）

`GISDatasetDescriptor` 增补 `dimension_provenance: Dict[str, str]`
（值 ⊆ `observed` / `inferred` / `unknown`；默认空 dict = 全 unknown，向后
兼容）。铸造入口按证据来源标注：实际扫描（geometry_type/feature_count/bbox）
= observed；规则推断（坐标域猜测 CRS）= inferred；无证据（未声明 CRS、
未提供 license）= unknown——未知保持未知，禁止虚构。字段级置信词表
（role_confidence 等）不变，本字段是维度级汇总，不是替代。

### D2 license/attribution 进入 ingest 契约（GD-01/GD-07）

`IngestPipeline.ingest(..., license=None, attribution=None)`：非空时透传
descriptor（`dimension_provenance.license=observed`）与 artifact metadata
（如实登记）。**不入 descriptor 指纹**（与 ADR-0265 对 license/attribution
的既有排除一致：补填许可不改变数据语义身份，不误判 stale——测试
`test_license_metadata_change_keeps_semantic_identity` 锁定）；license 的
**消费面**变化走 source 绑定透传与披露（H08 license_propagates）而非指纹。
upload 路由表单新增同名可选字段。空值 → unknown，不虚构。

### D3 Explorer→语义链桥接（GD-01，断链 1）

`validate_stage.bridge_geocoded_to_session` 在既有 payload 桥接后，把
geocode 成功行（`_lat/_lon` 有限值）转换 FC 经 IngestPipeline 铸语义身份：
- 返回值增补 `semantic_ref`（descriptor 锚定的 ref）；
- 全部行 geocode 失败 → 不铸 FC（诚实零要素），`semantic_ref` 缺席；
- 桥接失败不致命（沿用 #776 纪律）；
- 行→要素映射：properties 剥离 `_` 前缀 magic keys。

### D4 分发数据资格闸（GD-08，断链 2）

新模块 `app/services/dataset_semantics/dispatch_gate.py`：
`evaluate_dispatch_data_gate(session_id, tool_name, args, *, expected_fingerprints=None)`。

- 扫描工具参数中的 `ref:` 值（有界深度）；`ref:raster/`、`ref:profile-`、
  sentinel ref 豁免（能力投影，非数据集）；
- 语义记录 read：`ok` + 指纹一致 → **pass** 并附
  `qualification_context`（crs/feature_count/bbox 等有界事实，模型可见）；
  期望指纹不一致 → **blocked `DATA_QUALIFICATION_STALE`**；读取异常/损坏 →
  **blocked `DATA_QUALIFICATION_DESCRIPTOR_CORRUPT`**；
- 无语义记录 → 默认 **pass + disclosure `descriptor_missing`**；
  `DATA_QUALIFICATION_GATE_STRICT=1` 时升级为 blocked
  `DATA_QUALIFICATION_DESCRIPTOR_MISSING`；
- kill switch：`DATA_QUALIFICATION_GATE=0` 全放行（一键回滚）；
- 接线：`tool_dispatch_service.dispatch` 在 1.45 反幻觉网关之后、1.5 复用
  查找之前，BLOCK 时结构化错误早退（复用 1.45 模式，释放 dedup 槽位）；
  fail-open（闸门自身异常 = 放行 + 日志），闸门绝不改变既有失败语义。

### D5 远程 GeoJSON URL adapter（GD-02，断链 3）

`app/services/data_fabric/adapters/geojson_url_adapter.py` + registry
`geojson_url`（真实 adapter，非 demo；#767 移除的是无实现空 alias）：

- `probe`：受控 GET（`make_safe_session`，复用 SSRF/重定向/私网防线），
  Content-Type 必须声明白名单（geo+json/json），体必须 FC 形状，否则
  `SourceBadResponseError`（typed，不静默空结果）；
- 下载预算复用 `limits`（要素 ≥1000 截断、字节帽、页数帽）；`preview`
  截断时披露 `truncated`；
- URL 经 `DataFabricSecurity` SSRF 校验（私网/IMDS/重定向内网一律拒）。

### D6 preflight 任务范围（GD-05，断链 5/6）

- `preflight_multi_dataset(..., task_scope=None)`：`task_scope =
  {place?, bbox?}`；dataset bbox 与 scope bbox 不相交 → typed gap
  `PREFLIGHT_SCOPE_MISMATCH`（不新增 verdict 态）；未声明 scope → 不检查
  （向后兼容）；
- 新模块 `app/services/dataset_semantics/task_scope.py`：
  `scope_fingerprint(place, bbox)`（确定性哈希；place 与 bbox 是不同资格
  粒度）+ `evaluate_scope_staleness(recorded, current)`（scope 变化 →
  stale + 可解释 reason）。消费面：MapSpec 复用门与 mapspec 绑定记录
  `task_scope_fingerprint`，城市/视口切换后旧绑定判 stale 需重资格
  （不建第二状态权威——staleness 仍是 ref_lifecycle + 指纹对账）。

### D7 恶意 GeoJSON 守卫（GD-09，断链 9）

`app/services/data_ingest/geojson_guard.py`：IngestPipeline 在 detect 与
validate 之间插入 `validate_feature_collection(fc)`：

- 非有限坐标（NaN/±Inf；含 GeometryCollection 内层成员）→
  `INVALID_COORDINATES_NONFINITE`；
- 嵌套深度 > 64（属性树与坐标树共用）→ `STRUCTURE_TOO_DEEP`；
- 单属性字符串 > 256KB → `PROPERTY_STRING_OVERSIZED`；
- 病态宽结构烧穿预算 → `GUARD_BUDGET_EXCEEDED`（类型化拒绝，绝不静默
  跳过——评审 F1：预算耗尽静默 return 会把恶意值藏在垫料后绕过）；
- 坐标扫描（1M 步/要素 ≈33 万顶点，真实重几何不误拒）与属性扫描
  （20k 步/要素）独立预算；O(features) 单遍，5 万要素守卫 <1s；
- 拒绝 = 类型化 error_code + 无 ref 铸造（发生在 store 之前，无补偿
  回滚对象）。

## 迁移与回滚

- 全部改动 additive（descriptor 新字段默认空、ingest 新参数默认 None、
  preflight 新参数默认 None、dispatch 闸门默认开但可 env 关）；
- 语义 store 载荷是内容寻址 JSON：旧记录无 `dimension_provenance` 键 →
  读侧 `getattr` 默认空 dict，无迁移脚本；
- 回滚：`DATA_QUALIFICATION_GATE=0` 关闸；adapter alias 从 registry 移除
  即退回 unavailable；guard 抽出为独立模块可单独 revert。

## 影响

- 既有 29 个资格测试 + ingest/准备/预检测试必须全绿（characterization 固定）；
- 新增测试：`tests/data/test_g03_readiness_chain.py`（21 红转绿）+ 后续
  旅程/性能/安全演练文件；
- 文档：`docs/DATA_READINESS_CONTRACT.md`（验收词表 ↔ 五态映射）、
  `docs/SOURCE_QUALIFICATION_MATRIX.md`、`docs/INGEST_PERFORMANCE.md`。
