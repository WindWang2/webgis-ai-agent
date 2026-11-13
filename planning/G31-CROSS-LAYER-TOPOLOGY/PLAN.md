# G31-CROSS-LAYER-TOPOLOGY — PLAN

目标：跨图层拓扑质量工作台——用户声明多数据集业务规则（覆盖/排除、跨层包含、跨层禁重叠、线端点连接），在权威快照上运行，得到稳定身份的 finding 列表 + witness 定位，支持合法例外、经 G14 的修复预览/提交、同规则复验；REST + @tool + 前端工作台全链交付。

## 架构（调用链）

```
UI (topology workbench panel) / @tool (webgis_topology_*)
  → app/api/routes/topology_workbench.py  /  app/tools/topology_workbench_tools.py
    → app/services/topology_workbench/service.py   （REST 与工具共用编排面）
      → ruleset.py     （规则集校验/digest/持久版本）
      → snapshots.py   （源角色绑定 → resolve_source(session_ref|project_artifact) + scope 解析（G24 句柄提交时消费）→ 快照指纹/revision）
      → geometry_core.py（计算 CRS 选择（crs_safety.recommend_metric_crs + typed 阻断）、几何预检、度量）
      → evaluators.py  （5 类规则纯函数 → findings（witness、locator、度量、阈值、方法））
      → findings.py    （finding key（规则 digest + 快照内自然身份 + witness 分量 digest）、witness → BlobStore、落库）
      → runner.py      （预算/取消/完整性；runner 经 jobs.py celery task 挂 durable job）
      → exceptions_svc.py（例外记录 + stale 判定）
      → repair.py      （候选修复 → feature_edit build_and_store_plan（G14 预览）；dataset 级走既有 new-ref）
      → recheck.py     （同规则+新版本重跑 → finding-key diff → resolved 判定）
```

## 数据模型（migration 0105）

- `topology_rulesets`：id/project_id/session_id/name/current_version/rules(JSON)/digest/created_by/时间戳
- `topology_runs`：id/ruleset_id/ruleset_digest/status/bindings(JSON)/rule_results(JSON 有界)/completeness(JSON)/findings_total/job_id/project_id/session_id/created_by/started_at/finished_at
- `topology_findings`：id/run_id/rule_id/rule_type/finding_key/severity/status/metric(JSON)/locators(JSON)/witness_key/witness_sha256/bbox/geometry_type/created_at/resolved_by_run_id（(run_id, finding_key) 唯一）
- `topology_exceptions`：id/ruleset_id/finding_key/anchor(JSON)/reason/created_by/created_at/revoked_at/status

## 规则词表（封闭）

| type | 语义 | 角色 |
|---|---|---|
| `polygons_no_overlap` | 同源面集合两两内部重叠面积 > overlap_area_threshold_m² → finding（每连通分量一条；含嵌套） | checked(面) |
| `area_coverage` | AOI(面集或bbox) − union(coverage) − union(exclusions) 的连通分量 > gap_area_threshold_m² → 缺口 finding | coverage(面,须全量)、aoi、exclusions(可选,面) |
| `objects_covered_by_target` | checked 对象（点/线/面）被 target 面并集覆盖（covered/within 边界政策可选）；越界部分 > 阈值 → finding | checked(任意)、target(面,须全量) |
| `cross_layer_no_overlap` | 两个面集合间重叠分量 > overlap_area_threshold_m² | source_a(面)、source_b(面) |
| `line_endpoints_connected` | 线端点到连接对象（点集/线集可选允许）距离 ≤ tolerance_m；合法出口由 exit_zones 或规则声明豁免；超阈值 → finding（witness=端点→最近候选连线） | checked(线)、connectors(点/线,须全量)、exit_zones(可选,面) |

- severity: info/warn/error；tolerance 建模分离：`tolerance_m`（线性）/`overlap_area_threshold_m²`/`gap_area_threshold_m²`，禁止一数两用
- scope: 角色可带 scope（bbox/where/ids，G24 句柄提交时解析）；**须全量角色**（coverage/target/connectors/aoi 数据集）带 scope → typed 拒绝 `scope_not_allowed_for_complete_role`（T12）
- 计算语义：computation CRS = recommend_metric_crs(联合范围)；范围 >30°经/20°纬、跨反经线、|lat|>83.5 → typed blocked（T08）；无效几何/空几何/null 前置剔除并计数披露（T09），绝不 buffer(0) 洗白

## finding 身份

`finding_key = sha256(ruleset_digest | rule_id | role→locator 自然身份排序 | witness 分量 canonical digest | witness_part_index)`
- locator：`_wfid` → FEATURE_ID_KEYS id → `idx:<i>@<snapshot_fingerprint>`（仅快照内有效）
- 同对要素多片不连通交叠 → 每分量一条（不互相覆盖）
- 同快照重跑/分页换序 → key 稳定（T16）

## 运行状态与结论分离

- run.status: pending/running/completed/failed/cancelled（作业层）
- 每规则 rule_result: applicability(applicable/blocked)、input_completeness(full/partial)、computation_completeness(full/partial/budget_exceeded)、outcome(violations_found/no_violations/unknown)
- 全部 skipped/blocked 不得显示绿色通过；invalid 剔除计数 + 未知范围披露

## 预算

- 单角色快照 ≤50k 要素；候选对 ≤200k；finding ≤5000/规则（超出→partial+计数）；单几何顶点 ≤100k；witness blob ≤8MB；每规则 wall-clock 120s；批间查取消；GEOS 原生调用不可中断 → 文档化取消延迟，任务状态 cancelling→cancelled 不发布迟到结果

## 验证矩阵（映射任务书 T01–T25）

- 单元（evaluators/几何核心/fixtures）：T01–T11、T17 语义
- API/持久化：T12–T16、T18–T21、T23
- 权限：T19（resolve_source 鉴权 + created_by 过滤 + 会话所有权）
- 工具/Pi：T24（TestClient + ToolDispatch + 真实 Pi 子进程旅程）
- 兼容回归：T25（quality-panel 前端测试、feature_edit 套件、quality 引擎套件）

## 工作包顺序

WP01 契约+fixtures → WP02 规则集 → WP03 快照 → WP04 预检 → WP05/06 求值器 → WP07 运行/落库/jobs → REST → 工具 → WP08/09 前端+例外 → WP10/11 修复/复验/导出 → WP12 验收（性能曲线 + Pi 旅程 + 文档）→ 独立 review → PR
