# G03-GEODATA-READINESS — 决策记录

| ID | 日期 | 决策 | 依据 |
|---|---|---|---|
| D1 | 2026-11-07 | 资格词表：目标验收词表（ready/needs_mapping/…）通过映射文档对齐既有五态（eligible/transform_required/degraded/blocked/unknown），不新增枚举 | 唯一事实源；`data_qualification.py:31-33` 已被 compiler/planner/semantic_tools 消费 |
| D2 | 2026-11-07 | tool dispatch 资格闸为薄对接：语义 store 无记录或 stale/unknown → 阻断 + 可解释原因；受 feature flag 控制 | 断链 2；共享接缝最小化波及 |
| D3 | 2026-11-07 | explorer→语义链桥接：validate_stage 末端一跳调 IngestPipeline（rows→FC），builder 增表格/行集入口 | 断链 1；不动五段链 |
| D4 | 2026-11-07 | 远程 GeoJSON URL adapter 挂 data_fabric registry 新 alias `geojson_url`，复用 limits/security | 断链 3；#767 移除的是无实现空 alias，本次补真实实现 |
| D5 | 2026-11-07 | CSV+坐标在 upload→parser 车道转 FC 后进 IngestPipeline（复用既有 CSV 解析），descriptor 由既有铸造入口产出 | 断链 7 |
| D6 | 2026-11-07 | bbox-地名一致性：preflight 增可选 task scope（place/bbox），不一致 → typed gap 码（新码进既有 gap 词表）；城市切换 → role binding 结果携带 scope 指纹参与 staleness | 断链 5/6；不建第二状态权威 |
