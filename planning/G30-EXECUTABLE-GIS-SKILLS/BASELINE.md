# G30-EXECUTABLE-GIS-SKILLS — BASELINE

- 执行时间：2026-11-12（UTC）
- 目标仓库：WindWang2/webgis-ai-agent（`gh repo view` 验证 nameWithOwner/default=master）
- Base SHA：`ca7b3ec273f2b43336852951e2292a416b2238e6`（origin/master fetch 后 rev-parse）
- 分支：`zcode/g30-executable-gis-skills-20261112T144732Z`
- Worktree：`/home/kevin/projects/webgis/webgis-worktrees/g30-executable-gis-skills-20261112T144732Z`
- 主工作区 HEAD（只读参考）：`816705218`（落后，未动）
- Open PR：0（`gh pr list --state open` 为空 —— 与快照一致；非查询失败）
- Open issues：45，无一与 GIS 技能执行/工作台/版本化直接相关（最近：#2058–#2062 R1-agent）
- 远端分支：无 zcode/g30*–g35* 在飞（g31/g32/g33 未检出对应分支；g34/g35 有本地 worktree，源自 ca7b3ec27）

## 关键基线事实（复核自源码，引用相对 worktree）

1. **E1 复核**：`app/api/routes/chat.py:1402-1416` — Pi 通道显式披露忽略 `skill_name`（audit #818 后续）。`project_id` 经 `_build_cartography_turn_context` 生效，披露文案只对 skill_name。
2. **E2 复核**：`app/services/gis_harness/skills/contract.py:114-163` — SkillContract 含 version/deprecated/deprecated_by、input/output_roles、capability_requirements、procedure IR、quality_obligations、completion_evidence；`fingerprint()`（:335）canonical JSON SHA256（内容指纹，非 mtime）。
3. **E3 复核**：`app/tools/skill_library_tools.py` — 4 个知识咨询工具（search/detail/replay/policy）；search/policy `record_selection`/policy 证据（进程级 ring 256）。
4. **E4 复核**：`app/services/gis_harness/tool_map_intent.py:243-347` — plan seam 绑定 `bind_skill_guidance_at_plan_seam` → `bind_procedure_to_plan`（C1/C2/C3，violations 只披露不阻断）→ `procedure_binding` 随 plan payload 持久化进 gis_chapter。`procedure_binding.py:1-33` "绑定不执行"。
5. **E5 复核**：`policy.py:38-59`（mode/tier/TRUSTED_PACKS=("core",)/kill switch GIS_SKILL_POLICY）；`execute_guided` 语义 :331-336（prefer_execute+HIGH+core）。`loader.py:74` 只装 `library/core/*.yaml`；39 个 core skill。
6. **E6 复核**：`frontend/components/settings/skills-hub.tsx` — 只读目录（`GET /api/v1/chat/skills` → `list_md_skills()`，即 md-skill 系统）+ 管理员上传（`POST /api/v1/config/skills/upload`）。与 GIS SkillLibrary 是两套资产（同名不合并）。
7. **E7 复核**：`extensions_platform/manifest.py:281-315` SkillDeclaration 形状 + `pack_catalog.py:87-94` candidate 治理；装好的包技能**不进** SkillLibrary（loader 无 extensions 链路）。
8. **E8 复核**：`session_plan.py:119-155`（revision/turns/steps/decisions/event_seq/recovery）；`:823` `apply_tool_result` 从统一派发更新步骤（会话锁内持久化）。
9. **E9 复核**：三个核心 YAML 在 `library/core/`（data_preparation.yaml:5 / vector.yaml:6 / cartography.yaml:123），version 均 1.0.0，无参数 schema、无执行工具映射。
10. **能力→工具映射**（AlgorithmRegistry.capability_tool_map() 实测）：`dataset_profiling_quality → audit_spatial_quality, profile_dataset…`；`thematic_cartography → create_thematic_map, webgis_layer_upsert…`；`kde_density → kde_surface…`；`grid_binning → h3_binning…`。
11. **durable jobs**：`app/models/db_model.py:97` AnalysisTask（ADR-0052 统一 job 表；job_kind String(20) 无 CHECK，纯加性）；`app/services/jobs/lifecycle.py:46` JobKind 含 G23 增的 data_import/field_compute；G23 runner `app/services/data_operations/runner.py`（atomic claim/watchdog/fencing/two-phase cancel）。
12. **G28 证据面**：`app/services/gis_harness/goal_satisfaction/contracts.py:27-103`（RequirementKind/GoalVerdict/EvidenceKind；PASS 只认可 DETERMINISTIC/STRUCTURAL）。
13. **dispatch 权威**：`app/services/tool_dispatch_service.py:266`（dedup→situation merge→capability bind gate→availability recheck→IR/map-command gates）；artifact seam :1444+ 铸 `ref:analysis-*`。
14. **Pi 面**：`app/services/chat/pi_native_surface.py` — 注册工具自动进 deferred 动态面（tool_search 可发现、webgis_execute 同管线直呼）；`registered_surface_names` 是唯一分类真相。
15. **ADR-0220 记录的缺口（本轮直接 dedup 锚）**：`SkillEvidenceRecorder.record_step/record_skip` 生产零调用；replay 只吃调用方投影；`skill_guidance` 不持久化超出 turn ctx。

## Pi pin

- `.gitmodules` → `https://github.com/earendil-works/pi.git`；gitlink = `b4afdddf44e059c67ccf5097e5317d913521b470`（v1.5.2，G22 #2093 已对齐）。本任务**不改 vendor/pi**（G29 所有权）。

## 资源纪律

- pytest 单进程；vitest 1-2 workers；重阶段锁 `$(git rev-parse --path-format=absolute --git-common-dir)/webgis-heavy-stage.lock`（flock）。
- 端口/临时目录/DATA_DIR 独立；本地 LLM stub 驱动 Pi 旅程。
