# G30 PLAN — 可执行 GIS 技能作业与版本化参数工作台

## 架构（最小加性，单一执行权威）

```
用户/Pi 选择技能版本（skill_name / catalog / preset）
        │
        ▼
prepare（零业务副作用，确定性）
  SkillJobPrepareService.prepare(skill_id, version?, params, bindings, session_ctx)
  ├── 资产解析：id+version 精确命中；digest=fingerprint；deprecated→披露替代；pack!=core→仅指导
  ├── 参数校验：typed spec（required/enum/range/unit/field_ref）× 工具 schema 词表
  ├── 数据绑定：role→ref 解析（session store 读），事实抽取（geometry/feature_count/fields/CRS）
  ├── 资格：capability availability + skill eligibility（复用 resolver/policy 既有谓词）
  └── 编译：procedure steps → 具体工具调用序列（YAML execution 声明 + capability_tool_map 核对）
        │  PreparedSkillCall {prepared_id, skill指纹, params(provenance), bindings, steps, preview, idempotency_key}
        ▼
start（真实执行；幂等键=内容 hash）
  SkillJobRunService.start(prepared)
  ├── durable job（JobKind=skill_job；analysis_tasks 行；create idempotent）
  ├── 步骤执行：每步经 ToolDispatchService.dispatch（同一 owner/scope/side-effect 闸）
  ├── start 时复核：asset digest 未漂移；输入 ref 仍可读且内容 digest 一致；权限仍在
  └── run 记录：result_summary=有界 run record（步骤分支/证据覆盖/产物 refs/降级披露）
        │
        ▼
结果消费（分型验收）
  report → session store ref + job result_ref + 可回读报告
  map   → MapSpec 图层（webgis_layer_upsert 编译发布）+ layer refs + 观察待验收（不伪称完成）
  预设  → presets 表（配置引用，不含数据/凭据）→ 重用=重新绑定+重新 prepare
```

红线：不建第二 planner/DAG/工具注册表/状态总线；SessionPlan 仍是步骤真相（工具经统一派发自动喂 apply_tool_result）；Pi 回调不开新 turn/不递归 prompt；UI 活跃会话 busy。

## 工作包落地顺序

1. **WP01**（分析，随 BASELINE/DEDUP 已完成）：三旅程工具链核查表 ↑。
2. **WP02-03** `skills/contract.py` additive：`SkillParameterSpec`/`SkillRoleBindingSpec`/`SkillExecutionSpec`（step→tool+argument_map，闭词表 role:/param:/const:）；3 个 YAML 补 parameters+execution，version 1.0.0→1.1.0。目录投影（`skill_jobs/catalog_views.py`）：runnable/guidance_only/deprecated/unavailable + 原因 + digest + 来源（core/induced/extension-candidate/md-guide 分型）。
3. **WP04-05** `skills/skill_jobs/prepare.py`：零副作用校验+编译+有界 preview（含 procedure_binding 式缺口披露）。Pi 侧未来投影复用现有 plan seam（不改 tool_map_intent 主链，仅新增 additive 入口）。
4. **WP06** `skills/skill_jobs/runner.py` + `app/tools/skill_job_tools.py`（gis_skill_job_prepare/start/status，tier 2）+ Pi 活跃回调绑定当前 turn（无递归）。
5. **WP07-08** durable job 接线（JobKind+=skill_job）+ `skill_job_presets` 表 + 迁移 + API。
6. **WP09** 前端 `frontend/components/skills/skill-workbench.tsx` + api client。
7. **WP10** chat.py skill_name 解析（唯一/歧义/未匹配三分支）+ turn ctx + plan seam 尊重显式选择 + 披露文案更新。
8. **WP11-12** 结果分型验收 + 三旅程（Pi 一条/UI 一条/preset 换数据一条）+ 治理回归（场景矩阵 26 条中可本地覆盖项）。

## 关键决策记录

- **prepared call 存进程内 LRU（TTL 15min，会话绑定，仿 G24 selection handles）**；durable 行在 start 时落全量快照（dispatch_spec）——刷新回看走 job 行，不依赖内存。
- **幂等键**：`skill_job:{owner_fingerprint}:{skill_id}:{version}:{sha256(params+bindings+digests)}`——双击/网络重试同键同行。
- **参数 provenance**：user|skill_default|preset|derived 四值；零值/None/未提供三态区分。
- **unit 校验**：闭词表（米/平方米/度/无量纲…→ canonical code），field_ref 校验真实字段身份（绑定数据 fields 事实）。
- **扩展 candidate/md-guide**：目录显示+detail 可读，prepare 一律 typed 拒绝（guidance_only）。
- **版本竞争**：prepare 记录 asset digest+input digests；start 复核漂移→SKILL_ASSET_DRIFT/INPUT_DRIFT typed 拒绝→重新 prepare。
- **权限撤销**：start 每步 dispatch 走既有闸（G13 正向事实拒绝），prepare 授权不缓存到执行。
- **map 完成语义**：map 旅程完成=图层落 MapSpec+runtime 编译回执（layer ref）；渲染观察仍按 G28 语义标 pending 观察义务，不伪称 render_observed。

## 测试面（TEST_MATRIX 摘要）

- 单元：prepare 校验矩阵（缺字段/错单位/过期 ref/枚举外值/权限不符/弃用版本/candidate 拒绝/digest 漂移）；幂等键稳定性；参数 provenance 三态。
- 集成：3 技能 prepare→start→job 行→result_summary→产物 ref 回读；取消/重试/双击；session plan 关联。
- Pi 旅程（真子进程）：tool_search 发现 gis_skill_job_* → prepare → start → 有界回执（LLM stub 驱动）。
- UI：工作台三旅程（Playwright 1 worker，本地服务）。
- 治理回归：场景矩阵 #4/5/6/7/8/9/10/11/13/14/15/19/21/22/23/24 本地可测项。
