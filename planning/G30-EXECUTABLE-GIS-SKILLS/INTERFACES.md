# G30 INTERFACES — 新增/变更接口清单

## 新增（additive）

### 领域模块 `app/services/gis_harness/skill_jobs/`
- `contracts.py`：`PreparedSkillCall`（skilljob.prepared/v1）、`PreparedParam/PreparedBinding/BindingFacts/PreparedStep`、`SkillJobErrorCode`（typed 异常）、错误码词表 `SKILL_JOB_ERROR_CODES` + 运行期 `RUN_ERROR_CODES`
- `prepare.py`：`prepare_skill_job(...)` → `PrepareResult{prepared, inline_payloads}`（零业务副作用）；`extract_binding_facts(data)`
- `store.py`：`PreparedStore`（TTL 15min/LRU 64/会话绑定）；单例 `prepared_store`
- `runner.py`：`start_skill_job(prepared, inline_payloads, *, owner, wait_timeout_s, session_busy_check)`、`get_skill_job_view(job_id)`、`list_skill_job_views(session_id)`
- `presets.py`：`save_preset/list_presets/get_preset/delete_preset`（SkillJobPreset 表）
- `catalog_views.py`：`build_catalog()`、`gis_skill_entry(skill)`（runnable/guidance_only/deprecated/unavailable 分型）
- `selection.py`：`resolve_user_skill_selection(skill_name)`、`selection_notice_text(selection)`

### 工具面（Pi deferred 动态面自动可发现；webgis_execute 同管线）
- `gis_skill_job_prepare`（tier2，side_effect=state_mutation——选择证据；业务零副作用）
- `gis_skill_job_start`（tier2，state_mutation）
- `gis_skill_job_status`（tier2，cacheable_read）

### API（/api/v1/skill-jobs，owner-scoped）
- `GET /catalog`、`GET /skills/{id}`
- `POST /prepare`、`POST /start`（wait_timeout_s 可选；busy 409）
- `GET /runs?session_id=`、`GET /runs/{job_id}`、`POST /runs/{job_id}/cancel`
- `GET|POST /presets`、`GET|PUT|DELETE /presets/{id}`、`POST /presets/{id}/prepare`

### DB
- 表 `skill_job_presets`（迁移 `g3001_skill_job_presets`，down_revision=0104_map_briefs）
- `JobKind.skill_job`（String(20) 列无 CHECK，纯加性）

### 契约（SkillContract schema v2，additive 可选字段）
- `parameters: List[SkillParameterSpec]`（typed 参数：type/enum/range/unit/field_ref/required_if）
- `role_bindings: List[SkillRoleBindingSpec]`（执行面绑定约束）
- `execution: Optional[SkillExecutionSpec]`（step→tool argument_map；role:/param:/const: 闭词表，嵌套叶子）
- 三个核心技能 YAML 升 v1.1.0 并补薄配置

### 显式选择链
- `SelectionFacts.explicit_skill_id`（新事实字段）
- `SkillPolicy._decide_explicit`（显式裁决：eligibility/弃用/隔离照常）
- `HotpathTurnContext.explicit_skill` + `set_explicit_skill/peek_explicit_skill`
- `bind_skill_guidance_at_plan_seam(..., explicit_skill_id=)`
- chat.py `skill_name`：三分支解析 + SSE 披露（替换“忽略”文案）

## 变更（既有文件最小接线）
- `app/tools/__init__.py`：_TOOL_MODULES + skill_job_tools
- `app/tools/registry.py`：gis_skill_job_prepare 的 bindings 加入 ref 透明解引用 skip_keys（同 webgis_map_product 先例）
- `app/api/routes/chat.py`：skill_name 通知块（E1 语义升级）
- `app/main.py`：skill_job_routes 挂载
- `app/services/jobs/lifecycle.py`：JobKind.skill_job
- `app/models/db_model.py`：SkillJobPreset + uuid import
- `app/services/gis_harness/tool_map_intent.py`：plan seam 读取显式选择
- **既有缺陷修复**（披露）：`app/tools/scale_portrayal_tools.py` memory_class "small"→"light"（非法值导致 G27 两工具注册失败——master 既有，`git show HEAD` 可证）；`app/lib/gis/capabilities/platform.py` 补种 cartography_scale_portrayal_{inspect,apply}（上项修复暴露的悬空声明，G22 #2093 同款先例）

## 前端
- `frontend/lib/api/skill-jobs.ts`（typed client + SkillJobApiError）
- `frontend/components/settings/skill-workbench.tsx`（工作台；挂载于 settings-panel skills tab，原 SkillsHub 保留在下）
