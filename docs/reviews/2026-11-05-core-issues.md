# Core issue verification and repair — 2026-11-05

Baseline: `e0e97b462`. Integration is in progress. Every unresolved row keeps its aggregate open.

## High issues

Paths below are relative to `app/services/` unless another root is explicit. Shared regressions: `tests/unit/test_review_core_high_regressions.py`, `tests/unit/test_review_core_medium_regressions.py`.

| Issue | Status | Source | Validation evidence |
|---|---|---|---|
| #1775 | fixed | `capability_resolution.py` | `test_geographic_resolution_does_not_become_metres` |
| #1777 | fixed | `requirement_ir/{patch,service}.py` | `test_requirement_answer_cannot_persist_unreadable_document` |
| #1778 | fixed | `chat/execution_engine.py; schemas/chat_schema.py` | `test_client_perception_cannot_replace_server_ledgers` |
| #1779 | fixed | `cartography_runtime.py` | `test_targeted_cartography_state_read_persists_action` |
| #1780 | fixed | `spatial_meta_profiler.py; mapspec_store.py` | `test_source_profile_rejects_filesystem_path` |
| #1781 | fixed | `api/routes/chat.py; chat/context_assembler.py` | `test_chat_project_rejects_other_owner` |
| #1782 | fixed | `spatial_analyzer.py` | `query AST allowlist; focused tests1=70 passed` |
| #1783 | fixed | `tool_dispatch_gates.py` | `test_multi_slot_acquisition_cannot_hold_and_wait_deadlock` |
| #1840 | fixed | `project_runtime/quota.py` | `snapshot quota tests; focused tests1=70 passed` |
| #1841 | fixed | `api/routes/spatial_events.py` | `test_spatial_event_rejects_unowned_binding_before_ingest` |
| #1842 | fixed | `gis_memory/{proactive_retriever,associative_index}.py` | `test_proactive_spatial_memory.py tenant/scope isolation` |
| #1843 | fixed | `gis_context/entry.py; schemas/chat_schema.py` | `test_chat_request_cannot_supply_server_state` |
| #1844 | fixed | `data_quality/rule_functions.py` | `test_large_closed_polygon_is_not_reported_unclosed` |
| #1845 | fixed | `simulation/traffic.py` | `test_simulation_runtime.py input-order/one-edge-tick regressions` |
| #1846 | fixed | `simulation/tasks.py` | `test_simulation_runtime.py durable configuration identity` |
| #1847 | fixed | `gis_context/hotpath.py` | `gis_context/test_wiring_h09.py authoritative tenant` |
| #1892 | fixed | `api/routes/chat_session_views.py` | `chart/table authorization tests` |
| #1893 | fixed | `api/routes/geoai.py` | `297 API/auth/GeoAI tests; test_scope_roots_never_authorize_other_geoai_runs` |
| #1894 | fixed | `api/routes/lakehouse*.py` | `lakehouse session-route tests, focused tests1=70 passed` |
| #1895 | fixed | `extensions_platform/fabric_bridge.py` | `test_fabric_bridge_async.py slow-consumer 100-event completion` |
| #1896 | fixed | `extensions_platform/host.py` | `test_lifecycle_v3.py revocation rolls back tool registry` |
| #1897 | fixed | `extensions_platform/worker/client.py` | `extensions worker stream-window tests` |
| #1898 | fixed | `extensions_platform/signing.py` | `extensions signing trust/revocation tests` |
| #1899 | fixed | `collab/presence.py` | `test_collab_v6.py + compact JSON Lua compatibility` |

## Validation

- Latest final focused suite: **2,805 passed**, covering full extension platform, LLM client, GIS memory, collaboration/WS, high/medium regressions. `/tmp/webgis-core-final15.log`.
- API/cockpit/GC/cartography focused suite: **236 passed**. `/tmp/webgis-core-tests14.log`.
- Broad prior suite: **5,935 passed**, 12 failures; extension model-provider compatibility, raster fixture contexts, live identity fixtures and terrain warning disclosures corrected in later passes. `/tmp/webgis-core-finalwide.log`.
- Conformance/runtime corpus follow-up: **118 passed**, one mission owner/session regression corrected using `refs.active_session_ids` and then passed in final suite. `/tmp/webgis-core-tests13.log`.
- Earlier GIS/context broad suite: **3,235 passed**, subsequent context fixtures/monotonic revision corrections validated in a 599-test pass.
- `mypy`: 29 core source files passed before the latest batch; integration must rerun the type/debt gates.

## Aggregate findings

One row per actual finding anchor, including lettered Low IDs. `unresolved` requires further verification/implementation; its parent issue stays open. Cross-domain rows are reconciled by the root agent.

| Issue | Finding | Status | Source / original review anchor |
|---|---|---|---|
| #1814 | S1-019 | fixed | - [ ] S1-019 app/api/routes/project.py:1589-1617；app/services/data_lifecycle/quota.py:776-800 — orphans = execute_orphan_revision_cleanup(plan_orphan_revision_cleanup(db), db=db) —— plan_orphan_revision_cleanup |
| #1814 | S1-029 | fixed | - [ ] S1-029 app/api/routes/geocompute.py:151-177；app/schemas/geocompute_schema.py:52（nodes: list[ExecutionNodeIn] 无 max_length）、26/55（parameters/budget 任意 dict） — 路由为 async def 且 get_current_user_optional，同步执行 |
| #1814 | S1-032 | fixed | - [ ] S1-032 app/api/routes/lakehouse.py:190-220、353-363；app/schemas/lakehouse_schema.py:94（max_cells ≤ 8_000_000） — result["bands"] = {band: data.tolist() ...} 与 result["variables"] = {...tolist()} 在 async han |
| #1814 | S1-036 | fixed | - [ ] S1-036 app/api/routes/config.py:228-332 — 注释自认"等同 RCE；AST deny-list 只是纵深防御不是安全边界"。上传后 approve_runtime_skill 自动批准并 load_skills → exec_module；目标目录是源码树内的相对路径 app/skills，同名文件直接覆盖（仅拒绝 dunder），可替换随包发布的技能；审计只有一条 See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1814 | S1-038 | fixed | - [ ] S1-038 app/api/routes/geocompute.py:317-331（project_id=body.project_id, project_raw=body.project_id，无任何项目访问校验）；app/services/geocompute/cluster/store.py:131（project_key = hash_scope_key(project_raw, "p:")） |
| #1814 | S1-041 | fixed | - [ ] S1-041 app/api/routes/spatial_events.py:145-192（WatchBody 的 session_id/project_id/mission_project_id 无归属校验）、239-286（POST /events 的 session_id/project_id 无校验）；app/services/spatial_events/mission_bridge.py: |
| #1814 | S1-042 | fixed | - [ ] S1-042 app/api/routes/data_quality.py:374-396（autofix_dry_run，get_current_user_optional，未调用 _enforce_inline_caps，而 evaluate/apply/profile 都调用了）；222-259（submit_quality_report）；app/services/jobs/submit.py:7 |
| #1814 | S1-044 | fixed | - [ ] S1-044 app/api/routes/geoai.py:134-176（_roots_for_scope 在 session/project scope 下均附加 registry_root），使用点 :523-597 /geoai/artifact-geojson、:470-505 /geoai/preview、:396 /geoai/prompt-refine(candidates_path)  |
| #1814 | S1-046 | fixed | - [ ] S1-046 app/api/routes/health.py:135-160（readiness_check），:86-95（_check_celery 每次新建 Celery app 并 inspect().active() 广播，2s 超时），:72-80（每次新建 Redis 连接） — all_ready = db and llm and redis and celery。外部 LLM 供应商故 |
| #1814 | S1-050 | fixed | - [ ] S1-050 app/core/auth.py _load_live_user_state/_live_identity（读取 DB User.org_id 后只把 role 用于覆盖），get_current_user 返回 "org_id": payload.get("org_id")，get_current_user_optional 同理；_live_identity 在 kind == "mis |
| #1814 | S1-054 | fixed | - [ ] S1-054 app/models/db_model.py:423（GeoComputeClusterRun.reserved_bytes = Column(Integer)）、:577/:579（usage_rows、usage_units Integer，而 limit_rows/limit_bytes/usage_bytes 为 BigInteger）；DDL migrations/versions |
| #1814 | S1-056 | fixed | - [ ] S1-056 app/services/project_service.py:23-60 _caller_may_access_project、:335-351 update_project（get_project_with_auth 后直接 project.status = data.status）；app/api/routes/project.py:208-218（PUT /projects/{id} |
| #1814 | S1-058 | fixed | - [ ] S1-058 app/schemas/geocompute_schema.py:68-76（ClusterSubmitRequest.priority: int = 5，无约束）；app/api/routes/geocompute.py:328（priority=RunPriority.coerce(body.priority)，0/5/10 原样接受）；app/services/geocompute/c |
| #1814 | S1-061 | fixed | - [ ] S1-061 deploy/redis.conf（maxmemory 256mb、maxmemory-policy noeviction）；app/core/idempotency.py（MAX_BUFFER_BYTES = 50MB、RESPONSE_TTL_S = 24h，只要携带任意凭据加 Idempotency-Key 即缓存 2xx JSON 响应）；docker-compose.prod.ym |
| #1814 | S1-002 | fixed | - [ ] S1-002 app/core/config.py:113-114，被 451/464/475/499/541/553/718/733/745 共 9 处守卫，以及 app 下另外 13 处调用使用 — return self.ENV.lower() == "production" 修复：- 把 ENV 做成 Literal["development","test","staging","producti |
| #1814 | S1-003 | fixed | - [ ] S1-003 app/api/routes/auth.py:372-408 —  修复：- 用 UPDATE refresh_token_families SET current_jti=:new, rotated_at=now() WHERE family_id=:f AND user_id=:u AND current_jti=:old AND NOT reuse_detected，按 rowcoun |
| #1814 | S1-004 | fixed | - [ ] S1-004 - app/core/auth.py:445-461、528-529：_live_identity 在 DB 异常或用户行不存在时返回 None，于是 role = payload.get("role")。 — auth.py 的注释自己写明 "行不存在：沿用 claim"、"DB 不可用：沿用 claim"。 修复：- 所有基于角色的放宽判定统一改用 get_current_user_wi |
| #1814 | S1-005 | already_fixed | - [ ] S1-005 app/api/routes/security_admin.py:40-172（/admin/orgs/{org_id}/quota 的 GET/PUT、/orgs/{org_id}/audit、/audit），以及 data_lifecycle.py、mission_runtime.py 中 "admin → 全量" 的写法 — 只校验 require_admin 加 require_sc |
| #1814 | S1-006 | fixed | - [ ] S1-006 - app/api/routes/config.py:69-89，调用 app/services/chat/execution_engine.py:589-605 的 update_config，只写实例字段和进程内的 override。 — 架构 / 部署：运行期配置与技能只存在于单个进程或 pod 修复：- 把运行期配置持久化到 DB 或 Redis，用版本号加 pub/sub 失效各进 See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1814 | S1-007 | fixed | - [ ] S1-007 manage.py:7、80-117、530-532 —  修复：- 改成 getpass 交互输入，或者 --password-stdin / ADMIN_PASSWORD_FILE。 See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1814 | S1-008 | fixed | - [ ] S1-008 app/api/routes/config.py:121-150 —  修复：- 当 req.base_url 与当前 engine.base_url 的 origin 不同时，必须同时显式提供 req.api_key，不得回落到服务端的 key。 See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1814 | S6-002 | fixed | - [ ] S6-002 app/extensions_platform/worker/client.py:101（isolation_backend: str = "process"）、:259-272（_child_env） — _child_env 清洗了 env，cwd 也移到了 tmp，注释称这是为了防止 worker "读走宿主全部 secrets"。但在 process 后端下，worker 的 uid |
| #1814 | S6-003 | fixed | - [ ] S6-003 app/extensions_platform/worker/client.py:196、:327-351 — Popen 设了 stderr=subprocess.PIPE，但只有一个 stdout 读线程（_reader_loop）。stderr 只在崩溃诊断时由 _stderr_tail 非阻塞读一次。扩展用 logging/warnings 往 stderr 写满管道缓冲（Linux |
| #1814 | S6-004 | fixed | - [ ] S6-004 app/extensions_platform/broker.py:209-217、:267-273；app/services/data_fabric/security.py:110-112 — DataFabricSecurity.validate_url 解析 DNS 并拒绝私网地址，但之后 guarded_client 用的是普通 httpx.Client，会在连接时重新解析。如果主机 |
| #1815 | S2-002 | fixed | - [ ] S2-002 capability_resolution.py:341 与 completion/validators/semantics.py:29-40 — - semantics 把 EPSG:4490（CGCS2000）视为地理坐标，与 WGS84 渲染等价；capability_resolution 只认 "4326"/"crs84"/"geographic" 子串，所以 CGCS2000 数据 |
| #1815 | S2-003 | fixed | - [ ] S2-003 harness_kernel/runtime.py:110-131；配合 app/services/session_plan.py:551-572 — _save_if_fresh 先 load、比较 revision，再 save。docstring 明说它正是为"锁降级、跨 pod 写竞争"设计的，但"读-比-写"三步之间没有原子性。两个陈旧写者都读到 rev N 时，都能通过检查，后写 See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2-004 | fixed | - [ ] S2-004 gis_harness/loop_budget.py:41-80 — governor 缺席，或 retry_allowed 抛异常时，一律返回 (True, "governor_unavailable")；charge 失败时也被静默吞掉。结果是 governor 一旦故障，修复/重试回路就失去上限，可能放大 LLM 调用和工具调用的成本（成本型 DoS）。 修复：- 故障时退回到进程内的 See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2-005 | fixed | - [ ] S2-005 gis_harness/visual_observation/provider.py:337-380 — 每次评估都新建一个 daemon 线程运行 asyncio.run，超时后只放弃 join，线程继续运行（注释承认它依赖 httpx 自身超时来兜底）。VLM 慢或挂起时，每次调用都会多留一个线程和一个事件循环，没有并发上限。thread.join 本身也是阻塞调用；如果上游在事件循环中 |
| #1815 | S2-012 | fixed | - [ ] S2-012 gis_harness/planner.py:180-198 — _get_session_local() 返回的是 sessionmaker 工厂（见 cartography/intent_learning.py:82-87；app/core/database.py:73 为 SessionLocal = sessionmaker(bind=Engine)）。代码 with _get_se |
| #1815 | S2-013 | fixed | - [ ] S2-013 gis_harness/planner.py:63-83、180-198、1494-1498 — planner 是同步代码，却在 async 工具 webgis_map_product（tools.py:790）里被直接调用。recipe_affinity_weights_sync 和上面的记账都是同步 SQLAlchemy 查询或写入，会阻塞事件循环；DB 慢时整个 worker 上的所 See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2-014 | fixed | - [ ] S2-014 gis_harness/requirement_ir/service.py:73-87 — - session_writer_lock 是进程内的 asyncio.Lock，多 worker 或多 pod 部署时保护不了持久化 envelope 的读-改-写。 修复：改用与 harness_kernel 相同的 session_lock_registry（分布式锁）；淘汰时跳过 locked |
| #1815 | S2-015 | fixed | - [ ] S2-015 harness_kernel/settle.py:270-311、597-661 — settle_turn 的幂等完全依赖进程内的 _RECEIPTS 缓存和 _TURN_LOCKS。快照读取（status == running）和后续副作用都不在会话锁内。如果 legacy 与 pi 两个宿主，或两个 worker 对同一 turn 并发结算，双方都会看到 running：final g See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2b-001 | fixed | - [ ] S2b-001 skills/policy.py:239-257，以及 skills/resolver.py:205-217 — 只要 ranked 为空且目标不为空，resolver 一定会把 clarification 设成 NO_MATCH。所以 policy 里 elif rejected_codes: mode="fallback"; fallback="all_candidates_ineli |
| #1815 | S2b-002 | fixed | - [ ] S2b-002 skills/procedure_binding.py:663-714，以及 skills/procedure_coverage.py:693-711, 485-489 — decision 节点按 default 裁决（resolved_by="default"）时，非默认分支的步骤在步骤层被正确豁免：binding 用 default_covered，coverage 判成 skipp |
| #1815 | S2b-003 | fixed | - [ ] S2b-003 skills/replay.py:110-151, 193-196。调用方是 app/services/gis_skills/induction/sandbox_validator.py:211,238 — 1. 只要步骤没有 capability_refs，cap_traced 就恒为真。如果它也没有 evidence 要求，就直接判 covered，即使计划和证据里完全没有它的踪迹。  |
| #1815 | S2b-004 | fixed | - [ ] S2b-004 skills/promotion.py:106, 113, 136-188 — 1. security_sandbox_status 默认是 "unknown"，而只有 failed、quarantined、unsafe 会被拦。沙箱根本没跑，也能得到 candidate。 修复：- 晋升到 candidate 必须满足 security_sandbox_status == "passed |
| #1815 | S2b-005 | fixed | - [ ] S2b-005 skills/loader.py:167、skills/resolver.py:278-284、skills/hotpath.py:79-82、skills/planning_projection.py:118-121 — 生产单例 SkillLibrary(skills, compositions) 没有传 capability_exists，所以 resolver 的 CAPABILI |
| #1815 | S2b-006 | fixed | - [ ] S2b-006 recipe_packs/interpolation.py:32 等，一共 11 条 recipe。也就是 kriging、IDW、cv_compare、uncertainty、station_field、air_quality、point_pattern_quadrat/distance、voronoi、convex_hull、transit_station_distribution。 See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2b-007 | fixed | - [ ] S2b-007 recipe_packs/*、planner.py:1517-1560、recipes.py:756-766、plan_candidates.py:222-226 — 1. 149 条 pack recipe 里，有 69 条声明了元素级 RecipeFallback，自己却没有任何 eligibility 规则。它们的 reason_code 永远不会产生，例如 MISSING_DENO See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2c-002 | fixed | - [ ] S2c-002 - requirement_ir/clarify.py:88-96, 211-214 — 1. 问题文案示例是 "如 2019-2024"。用户照着答 "2019-2024" 后，整串写进 range_start，range_end 仍为空。因为 context_key 包含 start，变了以后就生成一个新的 open blocking 歧义，再问一次；第二次答完后 range_star |
| #1815 | S2c-003 | fixed | - [ ] S2c-003 requirement_ir/normalize.py:21-38；调用方 build.py:66（geometry_ref）和 build.py:250（AOI 变更判定） — while changed 循环会反复剥后缀，"州/县/区/市"会被连续剥掉。结果是： 修复：每个名字只剥一次最长后缀；剥完剩 ≤1 字时不剥。AOI 变更判定直接比较原名（或比较规范化名加 level），geo |
| #1815 | S2c-004 | fixed | - [ ] S2c-004 completion/contracts.py:485-495；调用方 completion/pipeline.py:546-550, 864-865、app/evaluation/anti_claim.py:310 — - derive_product_verdict 有一条规则：cartographic_review 里有 evidence_class=deterministic 的  |
| #1815 | S2c-005 | fixed | - [ ] S2c-005 completion/pipeline.py:370-376 → _maybe_run_visual_evaluation → visual_evaluator.py:134（raw = evaluator(snapshot)） — - run_map_finalization 是 async，但在事件循环线程里同步调用 GIS_VISUAL_EVALUATOR 配置的任意 callabl |
| #1815 | S2d-002 | fixed | - [ ] S2d-002 hotpath_convergence/mission_bind.py:41-66、pi_mission.py:46-54；调用方 api/routes/chat.py:563-583。 — - chat 路由从 map_state["_mission_binding"] 读 mission_id，这个键客户端可写（S2d-001）。只要 org_id 为空，或者存档里的 org 为空，就 |
| #1815 | S2d-003 | fixed | - [ ] S2d-003 hotpath_convergence/claim_ingest.py:96-110 与 evidence_claim/census.py:60-74、verify.py:65-85。 — - 统计声明的 value/unit/stat_type 取自 rec.metadata。 修复：- value 证明要么来自独立来源（重新读取 stats 表或产物字节的摘要），要么在 claim 上 See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2d-004 | fixed | - [ ] S2d-004 evidence_claim/claims.py:176、contracts.py:165-171、contradiction.py:75-97。 — - claim_from_rank_table 把 spatial_scope.spatial_name 设成胜出者自己的名字。 修复：rank 声明的 scope 应该用 reference_scope（group_by/level/ao |
| #1815 | S2d-005 | fixed | - [ ] S2d-005 evidence_claim/claims.py:165-181。 — 无论 claim_type 是什么，都按降序取第一名，并写成 comparator="highest"。所以 ClaimType.MINIMUM，或者"越低越好"的指标，得出的是错误主体。 修复：按 claim_type 决定排序方向和 comparator（MINIMUM 用升序加 lowest）。 |
| #1815 | S2d-006 | fixed | - [ ] S2d-006 representation_binding/requirement_to_mapspec.py:177-195、contracts.py:121-124；引擎 dedup 在 mapspec/lifecycle_engine.py:343-362（最近 64 个 mutation_id）。 — - binding_fingerprint 只由 doc_id、path、target、pay |
| #1815 | S2d-007 | fixed | - [ ] S2d-007 visual_observation/blob_refs.py:114-163、store.py:134-167。 — - add_blob_ref 和 release_blob_ref 都是先 get_blob(vref)，修改后再 put_blob，没有锁也没有 CAS。截图按内容寻址，相同 PNG 会在多个会话之间共享。 修复：用每个 sha 一把的文件锁（fcntl）或原子 ren See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2d-008 | fixed | - [ ] S2d-008 visual_observation/store.py:87-100 → rules.py:39-50 → lib/cartography/golden_diff.py:24-31。 — - 上传校验只看字节数（≤4 MiB）和 PNG magic，不检查 IHDR 宽高。 修复：在 validate_screenshot_bytes 里解析 IHDR，限制宽高（例如 ≤4096×4096 |
| #1815 | S2d-009 | fixed | - [ ] S2d-009 visual_observation/auto_repair.py:195-257。 — 预算的 load、check、apply、save 之间没有锁。同一会话的并发终验或重试可以各自通过检查，都提交修复，超出 MAX_AUTO_PER_REVISION=1。revision CAS 只能挡住同一 revision 下的第二笔，挡不住会话总量。加上 S2d-001，预算本身也能被客户端清 See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2d-010 | fixed | - [ ] S2d-010 interaction_ir/ingest.py:332-368、305-317。 — - load_ledger_state 读 3 个键，apply_event 在内存里改，persist_ledger_state 再分 3 次 set_map_state 写回。 修复：单键存储整个 LedgerState，并在锁内更新（或用 Redis WATCH/Lua）；ack 必须在 pers See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2e-001 | fixed | - [ ] S2e-001 tools.py:1621-1762（webgis_product_edit）；合并端 app/services/session_plan.py:895-915（merge_product_edit_result） — 工具先做一次 digest 预检，然后立即对 MapSpec 执行 remove_component / patch_component（不可逆），最后返回 success See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2e-002 | fixed | - [ ] S2e-002 - trace_store.py:448-545（persist_chain），由 harness_kernel/settle.py:461-473 中的 async run_settle_effects 直接同步调用 persist_turn_chain。 — 这些调用在事件循环线程上做 fcntl.flock(LOCK_EX)（阻塞，会等其它进程或线程）、整段 gzip 读写、JSON See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2e-003 | fixed | - [ ] S2e-003 recovery_ledger.py:152-188；消费方 failure_taxonomy.py:346-368 — _mutate 忽略了 _save_locked 的返回值，即使写盘失败也返回内存中 +1 后的 entries。下次调用又从盘上的旧值读起，所以 record_failure 在磁盘只读、配额满或权限错误时每次都返回 1。remediation_for(attempt See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2e-004 | already_fixed | - [ ] S2e-004 - durable_context.py:34,188-256：_recovery_state 保存 repair/replan/deepen 回路计数。 — app/services/chat/execution_engine.py:1253-1275（_persist_map_state）会把客户端请求里的 map_state 逐键 set_map_state 写入，只跳过 viewp See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2e-005 | fixed | - [ ] S2e-005 durable_context.py:210-256（update_recovery_state），record_source_fingerprints 同理（123-185） — load_recovery_state → 修改 → set_map_state 整键覆盖，中间不持会话锁。set_map_state 的内部锁只保护单次写，不覆盖整个读-改-写。并发的 repair 和 re See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1815 | S2e-006 | fixed | - [ ] S2e-006 delegation.py:292-295,371-374 — dispatcher = SubagentDispatcher(_registry_for(), session_id)，而 _registry_for() 返回的是 ToolRegistry() 新实例。app/tools/registry.py:476 的构造函数里 _tools = {}，不会注册任何工具。所以 dele See [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md). |
| #1816 | S3-001 | fixed | - [ ] S3-001 app/services/plan_mode.py:367-381, 1104-1127 — _confirmation_covers 只检查 approved is True、plan_id 一致，以及 tier-3 的 step id 集合是批准集合的子集。批准 artifact 不记录各 step 的 tool 名和参数摘要。破坏性步骤的参数要到执行时才解析（resolved_args See [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md). |
| #1816 | S3-002 | fixed | - [ ] S3-002 app/services/skill_creator.py:44-56 — create_skill 把 LLM 提供的 code 原样写成 .py。注释承认 AST 静态检查还是“Future”。现在唯一的调用方 app/tools/skills.py:473 传的是 quarantine 目录，有一层缓解；但函数默认写进可加载的 app/skills，任何新调用方忘了传 target_d |
| #1816 | S3-010 | fixed | - [ ] S3-010 chat/context_builder.py:201-231；chat/formatters.py:111-116；layer_schema.py:262/286 — 客户端 map_state 里的图层名、字段名等直接拼进 prompt，没有 fence 隔离。数值字段直接 :.2f 格式化，客户端传入非数值时整轮对话抛异常。 修复：统一用 _xml_fence 包裹，并对长度做限制；格 |
| #1816 | S3-011 | fixed | - [ ] S3-011 semantic_retrieval embedding_retriever；tool_semantic_retrieval._default_embed — TOOL_RETRIEVAL_EMBEDDING 默认为 1。SentenceTransformer 的加载和 encode 在 DynamicToolSurface.select 的 async turn 路径上同步执行：首次调用加 |
| #1816 | S3b-002 | fixed | - [ ] S3b-002 app/services/mapspec_to_svg.py:1803-1804；1913-1926 — - 单个图层渲染出异常时 except Exception: continue，没有任何 _emit_diag，整层直接消失。 修复：两处都要 _emit_diag("layer_render_failed"/"export_failed", type(exc).__name__)。外 |
| #1816 | S3b-003 | fixed | - [ ] S3b-003 app/services/mvt.py:565-577（调用处 780、921） — _orient_ring 让外环在 y 向下的瓦片坐标里 shoelace < 0。MVT 2.1 §4.3.4.4 要求外环在瓦片坐标里的测量面积为正。 修复：改成 exterior 时 s < 0 才反转（外环面积为正，洞为负）。再补一条用严格解码器解码、断言环方向的测试。 |
| #1816 | S3b-004 | fixed | - [ ] S3b-004 app/services/history_service_async.py:395, 419, 646-649 — 每个匿名会话创建时都拿到一个全新的 owner_token，而 _enforce_cap 是按 user_id IS NULL AND owner_token == 这个新 token 分桶的，所以这个桶永远只有 1 条。MAX_SESSIONS=1000 对匿名会话完全不起 |
| #1816 | S3b-005 | fixed | - [ ] S3b-005 app/services/distributed_lock.py:255-283, 298-301；session_lock 默认参数 519-525 — fail_on_degraded 和 fail_on_lost 默认都是 False。 修复：在配置了 Redis 的生产环境里默认 fail-closed，也就是 fail_on_degraded = redis_configured |
| #1816 | S3b-006 | fixed | - [ ] S3b-006 app/services/local_poi.py:566-573 — polygon 为 ref: 时会 from app.services.ref_cache import get_ref_data，但这个模块根本不存在（已用 ls 和 rg 核实）。ImportError 被裸 except 吞掉，于是任何 ref: 多边形都返回“polygon 需为 WGS84 GeoJSON P |
| #1816 | S3d-001 | fixed | - [ ] S3d-001 report_service.py:628-662（配合 mapspec_to_svg.py:170-208 — _compile_vector_svg_bounded 调 compile_mapspec_to_svg_detailed(mapspec, 300, width=…, height=…)，没有传 max_features 和 timeout_ms。于是 _resolve_ex See [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md). |
| #1816 | S3d-002 | fixed | - [ ] S3d-002 report_service.py:138-154, 178-190 — safe_url_fetcher 先用 DataFabricSecurity.validate_url 校验，再交给 weasyprint.default_url_fetcher（urllib）真正去取。urllib 会自动跟随 3xx 重定向，还会重新解析 DNS。所以攻击者用一个公网 URL 302 跳到 169 See [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md). |
| #1816 | S3d-003 | fixed | - [ ] S3d-003 org_quota.py:148-172, 237-265 — 并发和存储配额都是先 count/sum 再比较，没有加锁，也没有在同一事务里预留额度。N 个并发提交会同时读到 used < limit，全部通过。另外 storage_bytes/concurrent_tasks 在 DB 出错时返回 0，effective_limits 出错时用默认值，check_rate 在限流器异常 |
| #1816 | S3d-004 | fixed | - [ ] S3d-004 offline_preflight.py:209-218, 241-257, 260-311, 330-343 — run_preflight 是 async，但在事件循环上直接同步调用所有探针：subprocess.run(celery inspect ping) 最多阻塞 timeout+5=8 秒，httpx.Client 同步请求 LLM 最多 5 秒，还有同步的 Engine.c |
| #1816 | S3d-005 | fixed | - [ ] S3d-005 s3_blob_store.py:802-831 — build_s3_client_from_env 对运维配置的 WEBGIS_S3_ENDPOINT_URL 也套用 validate_url(allow_private=False)。自建 MinIO 通常在 docker 网络或内网（172.x/10.x），会被一律拒绝，抛 S3StoreUnavailable。结果是离线/内网部署 |
| #1816 | S3d-006 | fixed | - [ ] S3d-006 plan_mode.py:1143-1144, 1239-1248, 954 — 某个波次里的任务如果被外部取消（CancelledError），代码直接 continue，既不算成功也不算失败。之后仍会把它后继节点的 in_degree 减一。如果这一步没有后继，循环正常结束，计划被写成 completed，但那一步其实没有结果。下次 execute_plan 命中 status ==  |
| #1816 | S3e-002 | fixed | - [ ] S3e-002 spatial_repair_pipeline.py:1080-1099，规划端在 spatial_quality_gate.py:780-784 和 spatial_repair_pipeline.py:399-408。 — 规划器传入的 fields 形如 {"field","field_policy","outlier_ratio","suggested_clip"}，没有 lowe |
| #1816 | S3e-003 | fixed | - [ ] S3e-003 spatial_repair_pipeline.py:589-591 和 607-611，lineage 计数在 1162-1171。 — 只要某个 feature 不是 dict，或者 shape() 失败，都会直接 continue，不再加入 cleaned_features。不管用户选了什么操作都是这样，哪怕只选了 deduplicate。丢弃只写一行 log，不进 op_stats |
| #1816 | S3e-004 | fixed | - [ ] S3e-004 spatial_repair_pipeline.py:1001-1014。 — (a) "²".isdigit() 为 True，但 int("²") 会抛 ValueError。异常没有被捕获，整个修复失败（已复现：ValueError invalid literal for int() with base 10: '²'）。 修复：先用 str.isdecimal() 加 ASCII  |
| #1816 | S3e-005 | fixed | - [ ] S3e-005 session_data_protocol.py:21-52（_conversation_owner_kind），在 439-445、484-490、537-543 行被 get_ref_data* / get_ref_descriptor_authorized 调用。 — (1) with SessionLocal() as db: db.get(...) 是同步阻塞调用，直接在事件循环 |
| #1816 | S3e-006 | fixed | - [ ] S3e-006 session_data.py:358-433（async with self._lock 包住了 await asyncio.to_thread(ref_spill_store.spill, ...) 和 compute_descriptor）。 — _lock 是整个进程共用的一把锁，不按会话区分。淘汰时会把最多 8MB 的 JSON 落盘，描述符计算要扫描所有要素，这些都在锁里完成。 |
| #1816 | S3f-002 | fixed | - [ ] S3f-002 tool_dispatch_service.py:1214、1227（定义只在 719-725 — _parsed 只在 GIS_ANALYSIS_REUSE!=0 且工具属于 capability 工具时赋值。其余所有工具（OSM、POI、图层等）以及关闭复用开关时，执行到 1214 行的 if _parsed is not None 会抛 UnboundLocalError。这个异常被 |
| #1816 | S3f-003 | fixed | - [ ] S3f-003 workflow_engine.py:575-576、642-645；62-92 — 所有步骤共用同一个 step_executed_tools 集合，并沿用聊天侧的去重逻辑。DAG 中两个步骤如果是相同工具且参数相同（例如对两个输入做同一个预处理，或绑定后参数恰好一致），第二个步骤会拿到 status="repeated"。_dispatch_step_via_service 只把 "e |
| #1816 | S3f-004 | fixed | - [ ] S3f-004 workflow_engine.py:80-86 → tool_dispatch_service.py:1533-1534、1740-1741 — workflow 的每个步骤都用常量 "id": "wf-step" 作为 tool_call id，而 _author_display_result 用它生成 layer_id = "result-wf-step"（栅格是 raster-wf |
| #1816 | S3f-005 | fixed | - [ ] S3f-005 workflow_engine.py:1476-1488（对比 docstring 1442-1450 — rerun_from_step 检测到输入数据集指纹漂移后只记日志、写 disclosure，仍然复用上游种子步骤的旧输出，然后在新指纹下继续执行。docstring 写的是 “Reused steps still require fingerprint equality (same |
| #1816 | S3f-006 | fixed | - [ ] S3f-006 workflow_engine.py:772 — _execute 只捕获 Exception。asyncio.CancelledError（客户端断开或任务取消）和 OperationCancelled 这类 BaseException 会直接穿出去，而 run 行在 535 行已经以 status="running" 提交，并在各步骤间提交过。review #1744-4 修的正是“D |
| #1816 | S3f-007 | fixed | - [ ] S3f-007 subagent_roles.py:457-465、494-496；subagent.py:437-449 — token 预算闸 check_tokens() 读取的是 budget._total_tokens，但这个值只在子代理结束时通过 _sub_result → join_turn_evidence 汇入。运行期间它恒为 0，所以 max_total_tokens（light 档  |
| #1816 | S3f-008 | fixed | - [ ] S3f-008 ws_service.py:222-233、167-187、243-247 — handle_viewport_change 对 viewport 做了有限数值和形状校验（F-15），但 state_snapshot 白名单中的 "viewport" 和 "bearing"/"pitch"/"is_3d"/"base_layer" 都是原样写入 set_map_state，可以写入 NaN |
| #1816 | S3f-009 | fixed | - [ ] S3f-009 task_tracker.py:199-222、224-233 — 全局上限是 500 个任务。已完成的任务清完后如果仍然超限，会按插入顺序淘汰正在运行的任务，并点燃它们的取消 token。任何用户只要开足够多的会话发请求（每会话 20 个），就能把其他用户正在跑的 turn 取消掉。另外 _drop_task 每次都遍历所有会话，复杂度是 O(会话数)。 修复：全局淘汰只针对终态任务；运 |
| #1816 | S3f-010 | fixed | - [ ] S3f-010 tool_surface_v2.py:177-182、191-225 — 检索补强的预算借用了 ToolCatalog 的“tier-2 增量预算”（24KB），但 used 从全部 kept（包括必发的 tier-1，而未标 tier 的工具默认就是 tier-1）开始累计。tier-1 本身通常已经超过 24KB，于是所有检索命中都被记成 dropped="budget"，检索补强实际 |
| #1817 | S3c-002 | fixed | - [ ] S3c-002 jobs/store.py finish_job 的 attempt 栅栏；调用方 spatial_tasks.py:166,738、simulation.py:106、geocompute.py:231 — finish_job 支持 attempt 参数来拒绝过期 attempt 的写回，但所有调用方都没有传这个参数，栅栏从来不会生效。重投递后，旧 worker 仍然可以覆盖新 att |
| #1817 | S3c-005 | fixed | - [ ] S3c-005 jobs/worker.py:298-300 与 store.py:1020 sweep_stale — watchdog._run 观察到 cancel 后直接 return，心跳随之停止。任务体可能还在执行，sweep_stale 会按心跳超时把它判为 cancelled 或 stale。结果是状态和实际执行不一致，资源也可能被重复调度。 修复：心跳和取消轮询拆成两个循环，任务体真正退 |
| #1817 | S3c-003 | fixed | - [ ] S3c-003 jobs/submit.py build_execution_key 与 store._is_reusable — 已完成的 job 按幂等键永久复用，没有 TTL，也没有输入版本。上游数据变化后，相同参数仍然返回旧结果。 修复：给 reuse 加上 TTL，或在键里加入数据或环境的 revision。 |
| #1817 | S3c-004 | fixed | - [ ] S3c-004 jobs/store.py:225-235, 261-264（async create — 捕获 IntegrityError 后调用 db.rollback()，会回滚调用方整个事务里的其他未提交写入。 修复：用 async with db.begin_nested()（SAVEPOINT）包住 insert，冲突时只回滚 savepoint。 |
| #1817 | S3c-010 | fixed | - [ ] S3c-010 mission_runtime resources.reserve/set_quota/release、artifacts.attach、service.revise_goal/attach_session — 这些方法先读再写，交给 patch_mission 时没有传 expected_revision。patch_mission 的 CAS 用的是它自己刚读到的 revision，并 |
| #1817 | S3c-011 | fixed | - [ ] S3c-011 api/routes/mission_runtime.py（worker_id 由客户端提供 — - worker_id 由客户端传入。 修复：worker_id 由服务端生成，acquire 时总是递增 epoch，diagnostics 不返回 lease_owner。 |
| #1817 | S3c-014 | fixed | - [ ] S3c-014 turn_journal/ledger.py:223-249 — - 查询可压缩 turn 时，会重复选中已经压缩过的 turn，因为保留下来的 terminal 和 map_mutated 行仍是 'recorded' 状态。 修复：压缩后把保留行标成 compacted，或者在查询中排除已经压缩过的 turn_id。 |
| #1817 | S3c-020 | fixed | - [ ] S3c-020 workflow_runtime/contracts.py:364-388 instance_status_from_nodes、driver 循环与 _finalize、recovery.py:203 — - 可选节点 FAILED/BLOCKED、其余节点都成功时，函数返回 RUNNING。 修复：可选节点失败后，把它的下游判为 SKIPPED，并让状态汇总把可选失败视为可终止。rec |
| #1817 | S3c-021 | fixed | - [ ] S3c-021 workflow_runtime/adapters_cartography.py:165-216, 92-109 — matplotlib 渲染、PDF 和 blob 写入都同步跑在事件循环上。_render_features_png 的复杂度是 O(要素数×类别数)，最坏约 20000×20000。 修复：放进 asyncio.to_thread 或进程池执行，先按类别分组，并限制类别数 |
| #1817 | S3c-022 | fixed | - [ ] S3c-022 workflow_runtime/hooks.py（owner_scope_for_session）对照 geocompute/executor.py:167-192 — - 没有 user 时，hooks 调用 owner_scope_for(None, None)，没有传 session_id。 修复：传入 session_id，让匿名用户落到 "s:<hash>" 域。 |
| #1817 | S3c-023 | fixed | - [ ] S3c-023 reuse.py:163-166（真实用户 session_scope=""，可跨会话复用）+ driver 复用命中路径 — - 复用命中时，节点的 output_ref 被设成另一个会话的 artifact_ref。eligibility 用 rec.artifact_session_id 探测，能够通过。 修复：命中后把 artifact 复制或映射到当前会话，或者在节点上记录 ar |
| #1817 | S3c-024 | fixed | - [ ] S3c-024 subworkflow.py:134-143 → service.instantiate:178-186 — - 子工作流用父实例的 session_id 调 instantiate。 修复：parent_instance_id 非空时跳过 supersede，或者只 supersede 同一 parent 下的实例。 |
| #1853 | S4g-001 | fixed | - [ ] S4g-001 Medium 正确性/架构 — 项目知识卡片注入提示词时不做 liveness 复核，且 rebuild 从不清扫消失的权威行 |
| #1853 | S4g-002 | fixed | - [ ] S4g-002 Medium 资源/正确性 — 行预算 400 小于 rebuild 自身产出（最多约 590 行），每次 rebuild 都会删掉刚写入的 active 行 |
| #1853 | S4g-003 | fixed | - [ ] S4g-003 Medium 错误处理/事务 — rebuild 的"单源失败不影响其余源"在 PostgreSQL 上不成立 |
| #1853 | S4g-004 | already_fixed | - [ ] S4g-004 Medium 正确性 — session ref 失效时，只要有直接回指行，ref-tag 引用行就不再降级 |
| #1853 | S4g-009 | fixed | - [ ] S4g-009 Medium 正确性 — artifact_cache 类 GC 计划永远不会回收任何文件，却报告成功 |
| #1853 | S4g-010 | fixed | - [ ] S4g-010 Medium 正确性 — spill 文件回滚会被还原到错误目录 |
| #1853 | S4g-011 | fixed | - [ ] S4g-011 Medium 正确性 — staging 观察期从"计划创建"起算，而非从执行起算 |
| #1853 | S4g-012 | fixed | - [ ] S4g-012 Medium 正确性 — staging 目录位于被枚举的数据根内，已暂存的 parquet 会被下一次评估重新当作候选 |
| #1853 | S4g-013 | fixed | - [ ] S4g-013 Medium 正确性/架构 — "复活（引用即预热）"是死代码，最近使用的对象仍可能被 GC |
| #1854 | S4h-001 | fixed | - [ ] S4h-001 Medium｜正确性｜temporal/aggregation.py:197-199 + models.py:95-96 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1854 | S4h-002 | fixed | - [ ] S4h-002 Medium｜正确性/错误处理｜temporal/aggregation.py:173-175, 183；models.py:56-64 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1854 | S4h-003 | fixed | - [ ] S4h-003 Medium｜正确性｜temporal/filter.py:142-155 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1854 | S4h-004 | fixed | - [ ] S4h-004 Medium｜并发｜project_runtime/store.py:280-297 |
| #1854 | S4h-005 | fixed | - [ ] S4h-005 Medium｜正确性｜dataset_semantics/preflight.py:114-118, 306-332 |
| #1854 | S4h-006 | fixed | - [ ] S4h-006 Medium｜正确性｜dataset_semantics/role_binding.py:210, 389-403；qualification_bridge.py:119-131 |
| #1855 | S4i-002 | fixed | - [ ] S4i-002 · Medium · 正确性 / 租户隔离 |
| #1855 | S4i-003 | fixed | - [ ] S4i-003 · Medium · 正确性（重试语义） |
| #1855 | S4i-004 | fixed | - [ ] S4i-004 · Medium · 可靠性（动作丢失） |
| #1855 | S4i-005 | fixed | - [ ] S4i-005 · Medium · 正确性 |
| #1855 | S4i-006 | fixed | - [ ] S4i-006 · Medium · 授权 |
| #1855 | S4i-007 | fixed | - [ ] S4i-007 · Medium · 正确性（时序） See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1855 | S4i-008 | fixed | - [ ] S4i-008 · Medium · 错误处理（伪造结论） See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1855 | S4i-009 | fixed | - [ ] S4i-009 · Medium · 正确性（统计） See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1855 | S4i-010 | fixed | - [ ] S4i-010 · Medium · 正确性 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1855 | S4i-011 | fixed | - [ ] S4i-011 · Medium · 正确性（统计） See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1856 | S4j-003 | fixed | - [ ] S4j-003 Medium（正确性/安全）热索引只增不删：已失效、已取代、已过期、已改为 sensitive 的记忆会一直被注入 |
| #1856 | S4j-004 | fixed | - [ ] S4j-004 Medium（正确性/性能）晋升每轮都会重做，而且 consolidated_from.at 让指纹每轮都变，长效记忆每轮都被 supersede |
| #1856 | S4j-005 | fixed | - [ ] S4j-005 Medium（安全）widget payload 的递归安全扫描只看列表前 256 项 |
| #1856 | S4j-006 | fixed | - [ ] S4j-006 Medium（正确性）histogram_slider 拒绝负值分箱，负值字段的直方图滑块挂载不上 |
| #1856 | S4j-007 | fixed | - [ ] S4j-007 Medium（安全/持久化）harvest 信任 map_state 里的 _gis_provenance，伪造的“用户决策”会被固化成永不过期的项目级记忆 |
| #1857 | S4k-005 | fixed | - [ ] S4k-005 Medium（正确性/数据丢失）autofix.py:323-329 MultiPolygon 只看第一个部件的第一个环，首部件退化就整条要素被删 |
| #1857 | S4k-006 | fixed | - [ ] S4k-006 Medium（正确性/数据丢失）autofix.py:137-140,182 repair_geometry 把所有多边形的 Z/M 坐标静默丢掉 |
| #1857 | S4k-007 | fixed | - [ ] S4k-007 Medium（正确性/CRS）autofix.py:337-366 reproject 后 geojson.crs 成员不更新；pyproj 缺失时只改标签、不改坐标 |
| #1857 | S4k-009 | fixed | - [ ] S4k-009 Medium（正确性/任务）engine.py:290-305 幂等复用分支直接 return，不回写 durable job，任务永远停在 queued |
| #1857 | S4k-010 | fixed | - [ ] S4k-010 Medium（正确性）api/routes/data_quality.py:242-255 + engine.py:290 job 幂等键不含 rule_defs，换规则集重新提交会拿到旧 job |
| #1857 | S4k-011 | fixed | - [ ] S4k-011 Medium（可用性/DoS）metrics.py:40-52 + engine.py:134 Prometheus label 用了用户自定义的 rule_id，基数不受限 |
| #1857 | S4k-015 | fixed | - [ ] S4k-015 Medium（正确性）rule_functions.py:800-801 顶层 nodata_ratio 被当成有效像元比例取反 |
| #1858 | S4l-003 | fixed | - [ ] S4l-003 [Medium] 正确性/架构：生产路径的时态产物只放在 worker 内存，ref 和 MapSpec dataPath 都是悬空的 |
| #1858 | S4l-004 | fixed | - [ ] S4l-004 [Medium] 安全/资源：同步路径和异步路径都没有设墙钟预算，LLM 可以触发超大推演 |
| #1858 | S4l-005 | fixed | - [ ] S4l-005 [Medium] 并发：What-If 注册表的读改写只有 create 时加了锁 |
| #1858 | S4l-006 | fixed | - [ ] S4l-006 [Medium] 正确性：diff_layers 把要素和投影后的形状 zip 起来，坏几何会导致错位 |
| #1858 | S4l-007 | fixed | - [ ] S4l-007 [Medium] 正确性：对比矩阵的 baseline 列和 Δ% 用的不是同一个 lat0 |
| #1858 | S4l-008 | fixed | - [ ] S4l-008 [Medium] 正确性：ROI 在 cost_proxy=0 时被放大到约 1e9 倍，实施优先级排序失真 |
| #1858 | S4l-009 | fixed | - [ ] S4l-009 [Medium] 正确性：没有任何可评价指标时，仍然推荐方案并给出 ≥0.1 的置信度 |
| #1858 | S4l-010 | fixed | - [ ] S4l-010 [Medium] 并发/性能：compare_branches 在事件循环上同步做 shapely 运算 |
| #1859 | S4m-002 | fixed | - [ ] S4m-002 · Medium · 正确性/可观测性（ContextCardReceipt 缺少 rtv_rejected 字段，receipt 日志会整条丢失） |
| #1859 | S4m-003 | fixed | - [ ] S4m-003 · Medium · 健壮性（provenance 的 detail 为 null 时 observe_session 崩溃，而且不在 try 里） |
| #1859 | S4m-004 | fixed | - [ ] S4m-004 · Medium · 正确性/性能（AOI 用视口 bounds 做 1e-9 精确比较，平移一下就判定基准漂移） |
| #1859 | S4m-005 | fixed | - [ ] S4m-005 · Medium · 安全/设计（DECISION_REAFFIRM 只靠文本匹配就能把失效决策恢复为当前，LLM 或提示注入可以随意“复核”） |
| #1859 | S4m-006 | fixed | - [ ] S4m-006 · Medium · 数据完整性（load 遇到瞬时 DB 错误会返回 None，随后以“新建”语义无 CAS 覆盖已有上下文） |
| #1859 | S4m-007 | fixed | - [ ] S4m-007 · Medium · 正确性（stale 标记达到上限后静默丢弃新的失效，fail-open） |
| #1859 | S4m-008 | fixed | - [ ] S4m-008 · Medium · 并发/正确性（rebase 时截断保留最旧的条目，并发写方的新决策和事实会被丢掉） |
| #1860 | S1-015 | already_fixed | - [ ] S1-015 [Low] 安全（提示注入）/正确性：环境感知块把前端 user_location.accuracy 原样拼入 LLM 上下文 |
| #1860 | S1-016 | fixed | - [ ] S1-016 [Low] API 契约：/chat/sessions 的 total 实为本页条数 |
| #1860 | S1-017 | fixed | - [ ] S1-017 [Low] 健壮性：map-action-ack 计数用无 strict 的 zip |
| #1860 | S1-018 | fixed | - [ ] S1-018 [Low] 授权：/chat/tools/execute admin 可对任意 session_id 执行工具，无会话归属校验/审计 |
| #1860 | S1-020 | already_fixed | - [ ] S1-020 [Low] 并发：async 路由内对同步 Session 直接 await 服务方法 |
| #1860 | S1-021 | fixed | - [ ] S1-021 [Low] 错误处理/信息泄露：异常文本直接回传客户端 See [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md). |
| #1860 | S1-022 | fixed | - [ ] S1-022 [Low] 性能/DoS：匿名可达的 quality-audit 同步处理任意大小 geojson See [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md). |
| #1860 | S1-023 | fixed | - [ ] S1-023 [Low] 性能/输入校验：目录检索 LIKE 未转义、无长度上限 See [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md). |
| #1860 | S1-024 | fixed | - [ ] S1-024 [Low] 资源/DoS：NDJSON 流式端点单请求可拉 20 万要素且无并发闸门 See [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md). |
| #1860 | S1-025 | fixed | - [ ] S1-025 [Low] 资源/DoS：栅格/地形瓦片与 descriptor 端点漏掉 per-session 数据面预算 |
| #1860 | S1-026 | fixed | - [ ] S1-026 [Low] 隔离：栅格路径校验根是整个 DATA_DIR 而非会话域 |
| #1860 | S1-027 | fixed | - [ ] S1-027 [Low] 安全加固：导出下载响应头与 SVG 净化存在缺口 |
| #1860 | S1-028 | fixed | - [ ] S1-028 [Low] 可靠性：导出所有权依赖进程内 LRU + 本地侧车文件 |
| #1860 | S1-030 | fixed | - [ ] S1-030 [Low] 错误处理：多处输入错误被映射为 500，并把内部异常文本回传 See [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md). |
| #1860 | S1-031 | already_fixed | - [ ] S1-031 [Low] 一致性：同步 /plans/execute 未执行 org 配额检查 |
| #1860 | S1-033 | fixed | - [ ] S1-033 [Low] 授权一致性：lakehouse 项目域使用自写的"仅 owner"判断，且对 actor=None 放行 See [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md). |
| #1860 | S1-034 | fixed | - [ ] S1-034 [Low] 性能/授权顺序：上传在会话归属校验之前完成落盘 + 完整解析/剖析 |
| #1860 | S1-035 | fixed | - [ ] S1-035 [Low] 认证：WS 撤销复核只在收到客户端帧时触发 |
| #1860 | S1-037 | fixed | - [ ] S1-037 [Low] mission-runtime：配额由调用者自定且默认不限；admin 判定仅凭 JWT claim See [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md). |
| #1860 | S1-039 | fixed | - [ ] S1-039 [Low] 信息泄露：匿名分享端点回传 session_id/error_message，且分享无法撤销 |
| #1860 | S1-040 | fixed | - [ ] S1-040 [Low] 模板删除：跨 org 存在性预言 + 管理员不分租户 + 32 位 id See [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md). |
| #1860 | S1-043 | fixed | - [ ] S1-043 [Low] data-lifecycle GC：状态转移非 CAS、执行冻结清单不复核、任意用户可触发全局 assess See [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md). |
| #1860 | S1-045 | fixed | - [ ] S1-045 [Low] GeoAI _upload_root 由 UploadRecord.filename 推导上传目录的逻辑错误，上传文件无法被合法访问 |
| #1860 | S1-047 | fixed | - [ ] S1-047 [Low] /status/detailed 对任意登录用户暴露内部拓扑 See [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md). |
| #1860 | S1-048 | fixed | - [ ] S1-048 [Low] project_knowledge：搜索先截断后过滤、rebuild 无写权限门、项目鉴权用原始 JWT org |
| #1860 | S1-049 | fixed | - [ ] S1-049 [Low] visual-snapshots 每次上传都在后台触发复验，没有节流 |
| #1860 | S1-051 | fixed | - [ ] S1-051 [Low] 全局限流对 /api/v1/local-data/ 与 /api/v1/layers/data/ 整体豁免 |
| #1860 | S1-052 | fixed | - [ ] S1-052 [Low] 幂等中间件与内存限流器的边界缺陷 |
| #1860 | S1-053 | fixed | - [ ] S1-053 [Low] 异常处理：非 "production" 环境返回完整 traceback；legacy 头可绕过生产 422 细节隐藏，且可能引发 500 See [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md). |
| #1860 | S1-055 | fixed | - [ ] S1-055 [Low] 匿名会话 owner_token 明文落库；DateTime 列无时区却写入 aware 时间 |
| #1860 | S1-057 | fixed | - [ ] S1-057 [Low] 请求 schema 多处自由结构缺少上限 See [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md). |
| #1860 | S1-059 | fixed | - [ ] S1-059 [Low] k8s/compose 部署卫生：浮动镜像标签、多副本并发迁移、单 replica 加 PDB 阻塞驱逐、双进程容器 |
| #1860 | S1-060 | fixed | - [ ] S1-060 [Low] nginx：访问日志记录带凭据的 query，前端页面缺少防点击劫持头，公开的 /health 透传 /ready See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1860 | S1-062 | fixed | - [ ] S1-062 [Low] SRE 健康指标只在有人调用 /status/detailed 时才刷新，相关告警会永久误报 |
| #1860 | S1-009 | fixed | - [ ] S1-009 [Low] 安全：幂等重放在认证之前，可绕过撤销 |
| #1860 | S1-010 | fixed | - [ ] S1-010 [Low] 配置一致性：ALLOW_PUBLIC_REGISTER 有两个事实源 |
| #1860 | S1-011 | already_fixed | - [ ] S1-011 [Low] 健壮性：注册的唯一性检查有 TOCTOU |
| #1860 | S1-012 | fixed | - [ ] S1-012 [Low] DB / 会话：get_async_db 的同步回退分支类型错误 See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1860 | S1-013 | fixed | - [ ] S1-013 [Low] 部署：dev 的 Dockerfile runner 和 compose api 以 root 运行，且没有迁移入口 See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1860 | S1-014 | fixed | - [ ] S1-014 [Low] 可维护性 / 迁移：迁移中的 f-string SQL 和死代码 |
| #1861 | S2-006 | fixed | - [ ] S2-006 · Low · 安全(授权/会话归属) See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2-007 | fixed | - [ ] S2-007 · Low · 错误处理/可观测性 See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2-008 | fixed | - [ ] S2-008 · Low · 性能 See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2-009 | fixed | - [ ] S2-009 · Low · 架构/可维护性 See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2-010 | fixed | - [ ] S2-010 · Low · 安全/资源(输入边界) See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2-016 | fixed | - [ ] S2-016 · Low · 并发(无强引用的后台任务) See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2-017 | fixed | - [ ] S2-017 · Low · 并发(legacy 投影绕过陈旧写防护并覆盖 steps) See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2-018 | fixed | - [ ] S2-018 · Low · 正确性(统计形态误分类) See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2-019 | fixed | - [ ] S2-019 · Low · 正确性(规则槽位抽取误报) See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2-020 | fixed | - [ ] S2-020 · Low · 可维护性/资源(checkpoint 与日志) See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2b-008 | fixed | - [ ] S2b-008 · Low · 资源/正确性（性能档案满容量后，新档案立即被驱逐） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2b-009 | fixed | - [ ] S2b-009 · Low · 资源（lineage store 超容量后不收敛） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2b-010 | fixed | - [ ] S2b-010 · Low · 并发/隔离（进程级单例没有锁，跨会话共享） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2b-011 | fixed | - [ ] S2b-011 · Low · 正确性（策略细节） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2b-012 | unresolved | - [ ] S2b-012 · Low · 架构/可维护性（死代码和没落实的约束） |
| #1861 | S2b-013 | fixed | - [ ] S2b-013 · Low · 安全（LLM 工具面绕过信任分层，目前是潜在问题） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2b-014 | fixed | - [ ] S2b-014 · Low · 错误处理（校验谓词吞异常，报错信息误导） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2b-015 | fixed | - [ ] S2b-015 · Low · 数据质量（recipe 关键词） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-006 | fixed | - [ ] S2c-006 · Low · 错误处理（多答案提交不是原子的，失败时已有部分落盘） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-007 | fixed | - [ ] S2c-007 · Low · 正确性（seq 栅栏导致变更被静默丢弃 / 返回未持久化的文档） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-008 | fixed | - [ ] S2c-008 · Low · 正确性（产物校验按 capability 去重，会漏报） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-009 | fixed | - [ ] S2c-009 · Low · 正确性（执行校验的回退路径对 open 的定义与图路径不一致） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-010 | fixed | - [ ] S2c-010 · Low · 错误处理（展示确认在异常时默认为 True） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-011 | fixed | - [ ] S2c-011 · Low · 正确性（一次终验可能消耗两次重规划预算） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-012 | fixed | - [ ] S2c-012 · Low · 正确性/可维护性（重算子图的边解析） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-013 | fixed | - [ ] S2c-013 · Low · 正确性（typed DAG 校验忽略 depends_on） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-014 | fixed | - [ ] S2c-014 · Low · 韧性（包体超预算时整个编译直接失败） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-015 | fixed | - [ ] S2c-015 · Low · 正确性（英文规范化用子串匹配，有误报） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-016 | fixed | - [ ] S2c-016 · Low · 正确性（CRS84 URN 被误报为非 WGS84） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-017 | fixed | - [ ] S2c-017 · Low · 测试/架构（评估器的 passed 忽略两个维度；分层反向依赖） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-018 | fixed | - [ ] S2c-018 · Low · 性能（描述符探测有冗余） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-019 | fixed | - [ ] S2c-019 · Low · 正确性（跨反子午线时视口和 bbox 判定出错） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-020 | fixed | - [ ] S2c-020 · Low · 并发（澄清账本读改写不在锁内） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2c-021 | fixed | - [ ] S2c-021 · Low · 正确性（参数校验放过 NaN 和 inf） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2d-011 | fixed | - [ ] S2d-011 · Low · 正确性（交互投影信任客户端自报的提交结果） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2d-012 | fixed | - [ ] S2d-012 · Low · 正确性（goal 需求被截断后静默视为满足） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2d-013 | fixed | - [ ] S2d-013 · Low · 正确性（证据 id 冲突 / 状态映射可疑） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2d-014 | fixed | - [ ] S2d-014 · Low · 授权（claim 查找跨租户扫描） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2d-015 | fixed | - [ ] S2d-015 · Low · 正确性（重试轮用了旧的 mapspec） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2d-016 | fixed | - [ ] S2d-016 · Low · 正确性（turn 序号由 turn_id 里的所有数字拼成） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2d-017 | fixed | - [ ] S2d-017 · Low · 正确性（census 的 supersedes 边只在有 inputs 时才添加） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2d-018 | fixed | - [ ] S2d-018 · Low · 性能（关系图邻接是全表扫描） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2d-019 | fixed | - [ ] S2d-019 · Low · 安全（跨会话截图守卫 fail-open） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2d-020 | unresolved | - [ ] S2d-020 · Low · 测试 |
| #1861 | S2e-007 | fixed | - [ ] S2e-007 · Low · 并发/幂等（委派 RUNNING 状态从不落盘，幂等检查失效；无 deadline） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2e-008 | fixed | - [ ] S2e-008 · Low · 资源限制（修复轮次账本按观测指纹重置，可能每轮清零） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2e-009 | fixed | - [ ] S2e-009 · Low · 正确性（trace 段裁剪会在仍有可丢弃普通记录时删掉受保护的 FINAL_VERDICT） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2e-010 | fixed | - [ ] S2e-010 · Low · 正确性（resume 的工作流指纹验证基本是同义反复） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1861 | S2e-011 | fixed | - [ ] S2e-011 · Low · 错误处理（rebind 引用探测失败时放行） See [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md). |
| #1862 | S3-003 | fixed | - [ ] S3-003 ｜Low（由 Medium 下调）｜并发/async｜app/services/mission_runtime/resources.py:136 |
| #1862 | S3-004 | fixed | - [ ] S3-004 ｜Low｜并发/async｜app/services/workflow_runtime/store.py:330-333 |
| #1862 | S3-005 | fixed | - [ ] S3-005 ｜Low｜并发/async｜artifact_lifecycle.py:141, 612；report_service.py:394-396；runtime_validator.py:332 |
| #1862 | S3-006 | fixed | - [ ] S3-006 ｜Low｜架构/并发｜app/services/agent_swarm/delegation.py:815-831 |
| #1862 | S3-007 | fixed | - [ ] S3-007 ｜Low｜正确性｜app/services/chat/execution_engine.py:1009 |
| #1862 | S3-012 | already_fixed | - [ ] S3-012 ｜Low｜正确性｜layer_schema |
| #1862 | S3-013 | fixed | - [ ] S3-013 ｜Low｜架构｜chat/context_assembler.py:133 |
| #1862 | S3-014 | fixed | - [ ] S3-014 ｜Low｜安全（日志脱敏）｜decision_log.py:63-66 |
| #1862 | S3-015 | already_fixed | - [ ] S3-015 ｜Low｜安全（工具授权）｜webgis_execute 代理 |
| #1862 | S3-016 | fixed | - [ ] S3-016 ｜Low｜正确性｜chat/llm_client.py:490 |
| #1862 | S3-017 | fixed | - [ ] S3-017 ｜Low｜性能｜pi_rpc_client.py:575 |
| #1862 | S3-018 | fixed | - [ ] S3-018 ｜Low｜安全（prompt injection）｜pi_turn_context |
| #1862 | S3-019 | fixed | - [ ] S3-019 ｜Low｜正确性｜chat/tool_pipeline.py:182 |
| #1862 | S3-020 | already_fixed | - [ ] S3-020 ｜Low｜状态一致性｜v6_context_blocks.py:492 |
| #1862 | S3-021 | fixed | - [ ] S3-021 ｜Low｜正确性｜governor/storage_pressure.py:82-84 |
| #1862 | S3-022 | already_fixed | - [ ] S3-022 ｜Low｜资源泄漏｜governor（cancel_session / close_session） |
| #1862 | S3-023 | fixed | - [ ] S3-023 ｜Low｜正确性｜agent_swarm/duo_session.py:171-176；aggregator.py:137-148 |
| #1862 | S3-024 | fixed | - [ ] S3-024 ｜Low｜安全（委派校验）｜agent_swarm/delegation.py |
| #1862 | S3b-007 | fixed | - [ ] S3b-007 正确性 |
| #1862 | S3b-008 | fixed | - [ ] S3b-008 性能 |
| #1862 | S3b-009 | fixed | - [ ] S3b-009 正确性 |
| #1862 | S3b-010 | fixed | - [ ] S3b-010 健壮性 |
| #1862 | S3b-011 | already_fixed | - [ ] S3b-011 安全 |
| #1862 | S3b-012 | fixed | - [ ] S3b-012 安全（潜在） |
| #1862 | S3b-013 | fixed | - [ ] S3b-013 性能 |
| #1862 | S3b-014 | fixed | - [ ] S3b-014 安全 |
| #1862 | S3b-015 | fixed | - [ ] S3b-015 健壮性 |
| #1862 | S3b-016 | fixed | - [ ] S3b-016 正确性 See [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md). |
| #1862 | S3b-017 | fixed | - [ ] S3b-017 可维护 See [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md). |
| #1862 | S3b-018 | fixed | - [ ] S3b-018 健壮性 See [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md). |
| #1862 | S3b-019 | fixed | - [ ] S3b-019 性能 See [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md). |
| #1862 | S3b-020 | fixed | - [ ] S3b-020 正确性 See [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md). |
| #1862 | S3b-021 | fixed | - [ ] S3b-021 正确性 |
| #1862 | S3b-022 | fixed | - [ ] S3b-022 安全 |
| #1862 | S3b-023 | fixed | - [ ] S3b-023 async |
| #1862 | S3b-024 | fixed | - [ ] S3b-024 正确性 See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3b-025 | fixed | - [ ] S3b-025 async See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3b-026 | fixed | - [ ] S3b-026 性能（待核） See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3b-027 | fixed | - [ ] S3b-027 性能 See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3b-028 | fixed | - [ ] S3b-028 正确性 See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3b-029 | fixed | - [ ] S3b-029 正确性 |
| #1862 | S3b-030 | fixed | - [ ] S3b-030 性能 |
| #1862 | S3c-006 | fixed | - [ ] S3c-006｜Low｜性能｜jobs/worker.py 取消 watchdog |
| #1862 | S3c-007 | already_fixed | - [ ] S3c-007｜Low｜可靠性｜jobs/submit.py:143-160 |
| #1862 | S3c-008 | fixed | - [ ] S3c-008｜Low｜可观测性｜jobs/views |
| #1862 | S3c-009 | fixed | - [ ] S3c-009｜Low/Medium｜资源｜mission_runtime/recovery.py:148,221 |
| #1862 | S3c-012 | fixed | - [ ] S3c-012｜Low｜可维护性｜mission_runtime/recovery.py:161-172 |
| #1862 | S3c-013 | fixed | - [ ] S3c-013｜Low｜安全｜mission_runtime service.attach_session |
| #1862 | S3c-015 | fixed | - [ ] S3c-015｜Low｜正确性｜turn_journal retention |
| #1862 | S3c-016 | fixed | - [ ] S3c-016｜Low/Medium｜安全｜context_assembly/assembly.py:117-143 _scope_gate See [2026-11-06-context-assembly-low-review.md](2026-11-06-context-assembly-low-review.md). |
| #1862 | S3c-017 | fixed | - [ ] S3c-017｜Low｜正确性｜context_assembly/allocator.py:204-229 See [2026-11-06-context-assembly-low-review.md](2026-11-06-context-assembly-low-review.md). |
| #1862 | S3c-018 | fixed | - [ ] S3c-018｜Low｜正确性｜context_assembly/governor_link.py:121 See [2026-11-06-context-assembly-low-review.md](2026-11-06-context-assembly-low-review.md). |
| #1862 | S3c-019 | fixed | - [ ] S3c-019｜Low/Medium｜正确性｜adapters_cartography.py:58-65, 105-106 See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3c-025 | fixed | - [ ] S3c-025｜Low｜并发｜subworkflow.py:128-133 与 store.count_active_subworkflows:900-920 See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3c-026 | fixed | - [ ] S3c-026｜Low｜正确性｜subworkflow.py:151-164 See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3c-027 | fixed | - [ ] S3c-027｜Low｜正确性｜service.run_instance:225-239 See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3c-028 | fixed | - [ ] S3c-028｜Low｜并发｜workflow_runtime/recovery.py _consume_cancel/_finalize_if_decided；service.cancel_instance/retry_failed_nodes See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3c-029 | fixed | - [ ] S3c-029｜Low｜安全｜registry.publish:168-171；registry.register 中 owner_scope[:40] See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3c-030 | fixed | - [ ] S3c-030｜Low｜可维护性｜dispatch.py:63-73, 165-172；DurableDispatcher See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3c-031 | fixed | - [ ] S3c-031｜Low｜正确性｜fingerprints.canonicalize_parameters；recompute._append_decision See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3d-007 | fixed | - [ ] S3d-007｜Low｜安全｜plan_mode.py:367-381, 871 |
| #1862 | S3d-008 | fixed | - [ ] S3d-008｜Low｜正确性｜nature_resource_analyzer.py:219-238 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1862 | S3d-009 | fixed | - [ ] S3d-009｜Low｜async｜project_artifact_promotion.py:410-437, 544-573, 590 See [2026-11-06-runtime-service-low-review.md](2026-11-06-runtime-service-low-review.md). |
| #1862 | S3d-010 | fixed | - [ ] S3d-010｜Low｜可维护性｜provider_health.py:26-62, 194-205 |
| #1862 | S3d-011 | fixed | - [ ] S3d-011｜Low｜正确性｜raster_cartography_converter.py:352-374 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1862 | S3d-012 | fixed | - [ ] S3d-012｜Low｜安全（潜在）｜raster_store.py:38-65 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1862 | S3d-013 | fixed | - [ ] S3d-013｜Low｜并发｜report_service.py:777-795 |
| #1862 | S3d-014 | already_fixed | - [ ] S3d-014｜Low｜资源｜runtime_validator.py:391-411 |
| #1862 | S3d-015 | fixed | - [ ] S3d-015｜Low｜性能｜publication_export.py:661, 705-732 See [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md). |
| #1862 | S3d-016 | fixed | - [ ] S3d-016｜Low｜正确性｜s3_blob_store.py:710-737 |
| #1862 | S3d-017 | not_a_bug | - [ ] S3d-017｜Low｜性能｜s3_blob_store.py:207-225 |
| #1862 | S3e-007 | fixed | - [ ] S3e-007 [Low] 正确性（目前没有调用方）：无带号的 GK 坐标被推断成带号 EPSG See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1862 | S3e-008 | fixed | - [ ] S3e-008 [Low] 正确性：未声明 CRS 的投影数据被门禁当成经纬度，直接判定 block See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1862 | S3e-009 | fixed | - [ ] S3e-009 [Low] 健壮性：主键查重遇到不可哈希的值会让整个审计崩溃 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1862 | S3e-010 | fixed | - [ ] S3e-010 [Low] 性能：审计的 issue 数量没有总上限 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1862 | S3e-011 | already_fixed | - [ ] S3e-011 [Low] 并发/一致性：Redis 的 ref_revisions/ref_digests 键不随会话刷新 TTL |
| #1862 | S3e-012 | already_fixed | - [ ] S3e-012 [Low] 正确性：MemorySessionStore.get_ref_descriptor 返回内部字典，还会就地修改 |
| #1862 | S3e-013 | fixed | - [ ] S3e-013 [Low] 架构：SessionPlan 和历史信封与数据 ref 共用 LRU 容量 |
| #1862 | S3f-011 | fixed | - [ ] S3f-011 Low｜安全/权限｜subagent_roles.py:57、261-267 |
| #1862 | S3f-012 | fixed | - [ ] S3f-012 Low｜架构｜spatial_tasks.py:120-145、251-276 See [2026-11-06-runtime-service-low-review.md](2026-11-06-runtime-service-low-review.md). |
| #1862 | S3f-013 | fixed | - [ ] S3f-013 Low｜可靠性/上下文｜spatial_tasks.py:845-891 See [2026-11-06-runtime-service-low-review.md](2026-11-06-runtime-service-low-review.md). |
| #1862 | S3f-014 | already_fixed | - [ ] S3f-014 Low｜架构｜task_queue.py:140-160 |
| #1862 | S3f-015 | fixed | - [ ] S3f-015 Low｜安全｜tool_dispatch_service.py:1786-1795 |
| #1862 | S3f-016 | fixed | - [ ] S3f-016 Low｜正确性｜tool_dispatch_service.py:1940-1944 |
| #1862 | S3f-017 | fixed | - [ ] S3f-017 Low｜正确性｜workflow_engine.py:1663-1706 |
| #1862 | S3f-018 | fixed | - [ ] S3f-018 Low｜并发｜workflow_engine.py:1588-1618 |
| #1862 | S3f-019 | fixed | - [ ] S3f-019 Low｜授权纵深｜workflow_engine.py:341-361、1568-1585 |
| #1862 | S3f-020 | fixed | - [ ] S3f-020 Low｜运维｜tool_metrics.py:29、126-151、60、83、158 See [2026-11-06-runtime-service-low-review.md](2026-11-06-runtime-service-low-review.md). |
| #1862 | S3f-021 | fixed | - [ ] S3f-021 Low｜隐私/公平｜viewport_naming.py:38-55、111-126 See [2026-11-06-runtime-service-low-review.md](2026-11-06-runtime-service-low-review.md). |
| #1862 | S3f-022 | fixed | - [ ] S3f-022 Low｜正确性｜subagent.py:336-339、628-632、712-735 |
| #1862 | S3f-023 | already_fixed | - [ ] S3f-023 Low｜性能/正确性｜tool_catalog.py:393-397、307-309 |
| #1915 | S2-001 | fixed | ### S2-001｜Medium｜并发/安全｜app/api/routes/auth.py:372-408 |
| #1915 | S2-002 | fixed | ### S2-002｜Medium｜正确性/可观测性｜app/agent_pi_bridge.py:570-590 vs 595-720 |
| #1917 | S3-002 | fixed | ### S3-002｜Medium｜安全｜chat.py:2262-2285 rotate_session_owner_token |
| #1917 | S3-003 | fixed | ### S3-003｜Medium｜正确性/多租户/资源｜cockpit.py:273、:301 |
| #1917 | S3-004 | fixed | ### S3-004｜Medium｜安全｜config.py:110-160 test_llm_config |
| #1917 | S3-005 | fixed | ### S3-005｜Medium｜正确性/数据安全｜data_lifecycle.py:404-424 purge；gc_plan.py:315-355 |
| #1917 | S3-006 | fixed | ### S3-006｜Medium｜安全/多租户｜data_lifecycle.py:96-97、:131、:165、:268、:290 |
| #1917 | S3-007 | fixed | ### S3-007｜Medium｜错误处理/并发｜data_lifecycle.py:331-358 execute |
| #1918 | S4-003 | fixed | - S4-003｜Medium｜资源/安全｜data_quality.py:373-395 /autofix/dry-run：可匿名调用，但没调 _enforce_inline_caps。docstring #1439 说三个端点已统一加闸，实际只有 /evaluate 和 /profile 加了。匿名大 body 会被 evaluate+plan+dry_run 全量处理，存在 CPU/内存放大。修复：在 :387 |
| #1918 | S4-004 | fixed | - S4-004｜Medium｜安全｜data_preparation.py:73-87 _require_session：只读端点（inspect / plans 列表 / preview / receipt）也用 authorize_session_write 的“首写允许”语义：Conversation 行不存在就放行。会话行被 _enforce_cap 淘汰或删除后，session_data 里残留的数据可以 |
| #1918 | S4-005 | fixed | - S4-005｜Medium｜一致性/配额｜geocompute.py:180-219 /plans/execute：同步执行不调 _enforce_org_quota，而异步 /plans/runs（:262）调了，用同步入口就能绕过 org 速率/并发配额。修复：execute 也先执行 await _enforce_org_quota(user)。NEW（与上轮 #1840 promotion 配额是同类问题 |
| #1918 | S4-006 | fixed | - S4-006｜Medium｜错误处理｜geocompute.py:198、815：/plans/execute 和 /plans/drift-check 中 _plan_from_request 遇到未知 category/policy 会抛 ValueError，drift-check 的 ExecutionPlanIn(plan_in) 会抛 ValidationError，都未捕获，结果是 500。subm |
| #1918 | S4-007 | fixed | - S4-007｜Medium｜可用性｜health.py:149-178 /ready：就绪判定把 LLM 外部连通性也算进去。LLM 供应商抖动时，所有副本同时被 k8s 摘流量，造成全站不可用（readiness 本应只看本地依赖）。另外 Celery 的 inspect 超时 2s、Redis 2s、LLM 3s 串行，最坏约 7s 以上，超过常见的 probe timeout（1s）。修复：LLM/Cele |
| #1919 | S5-001 | fixed | ### S5-001｜Medium｜正确性/并发｜app/api/routes/map_operations.py:143-151（配合 frontend/lib/api/map-operations.ts:70-84） |
| #1919 | S5-002 | fixed | ### S5-002｜Medium｜正确性/数据｜app/api/routes/project.py:950-969（full restore） |
| #1919 | S5-003 | fixed | ### S5-003｜Medium｜安全｜app/api/routes/layer.py:1168-1173、1343-1354（terrain TileJSON / terrain-sources） |
| #1920 | S6-001 | fixed | S6-001｜Medium｜正确性/数据｜app/api/routes/upload.py:226-233,313-335｜CSV 声明 crs 只影响本次 parse；随后幂等 dedup 仅按 (session_id, content_sha256) 命中并直接返回旧记录（旧 crs/bbox），新目录被删。用户发现 CRS 错了、带正确 crs=EPSG:xxxx 重传同一文件，拿到的仍是旧的错误 CRS 记录 |
| #1920 | S6-002 | fixed | S6-002｜Medium｜安全｜app/api/routes/upload.py:577-600｜栅格车道把匿名会话 owner_token（长期会话凭据）拼进 tiles_url_template/tilejson_url 的 query（&token=），这些 URL 会进入 MapSpec/前端瓦片请求，落入 nginx 访问日志、Referer 与可能的分享导出。｜追链推断｜改用短期签名 URL（app/c |
| #1920 | S6-003 | fixed | S6-003｜Medium｜安全/资源｜app/api/routes/upload.py:176-306｜会话写权限 authorize_session_write 在写盘 + 完整解析（最大 200MB 栅格、剖析）之后才校验；任何登录/匿名调用者可对他人 session_id 触发全量解析 CPU/IO，再被 404。｜追链推断｜在读取/解析前先做归属校验｜NEW |
| #1920 | S6-004 | fixed | S6-004｜Medium｜性能｜app/api/routes/ws_collab.py:93-100,451｜每次 sync（及连接 hello）都调用 invalidate_local_cache(session_id) 打穿整会话 L1，正是 _fresh_revision 注释（R2-M-5）所要避免的失效风暴；N 协作者 × 重连即反复全量 HGETALL。｜追链推断｜sync 改定向读 revision， See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1921 | S7-001 | fixed | ### S7-001 [Medium] 维度1/5 PlatformError 经全局 handler 一律返回 HTTP 500 |
| #1921 | S7-002 | fixed | ### S7-002 [Medium] 维度3 幂等单飞：等待者 30s 超时后在首个请求仍在跑时重复执行 |
| #1921 | S7-003 | fixed | ### S7-003 [Medium] 维度2 WS 鉴权对已删除用户放行 |
| #1921 | S7-004 | fixed | ### S7-004 [Medium] 维度4 inset_map bbox 拒绝跨日界线范围 |
| #1922 | S8-001 | fixed | S8-001｜Medium｜2 安全/6 降级｜app/core/rate_limiter.py:255-258（配合 222-229）｜Redis 不可用时每 60s 重探一次，失败分支 _rate_limiter = MemoryRateLimiter() 新建实例，丢弃全部内存计数。登录锁定（5/5min/IP、30/5min/标识符）和 record/count 失败台账在 Redis 宕机期间每 60s 清 |
| #1922 | S8-002 | fixed | S8-002｜Medium｜1 正确性｜app/core/rate_limiter.py:84-111 对比 159-168｜Redis 版先 zadd 再 zcard，被拒的请求也会写进窗口，持续超限的客户端永远解不了封（只有停止请求满一个窗口才行）；内存版被拒时不写入。两个后端语义不一致，Redis 故障切换时限额行为也会跳变。｜追链推断｜超限时 zrem 掉本次成员，或用 Lua 脚本做到 check-then |
| #1922 | S8-003 | fixed | S8-003｜Medium｜7 可观测性｜app/core/sre_metrics.py:53-58,90-91｜observe_export_duration 在仓库内没有任何调用方（grep 只命中定义处），sre_export_duration_seconds 直方图永远为空，基于它的告警或面板都不会触发。｜已核实（grep）｜在导出管线完成点调用。｜NEW |
| #1923 | S9-001 | fixed | ### S9-001｜Medium｜1 正确性/8 测试质量｜app/evaluation/report.py:460-480（diff_against_baseline） |
| #1924 | S10-001 | fixed | ### S10-001｜Medium｜测试质量/正确性｜app/evaluation/runner.py:256-278（及 :1032-1033 多轮子案例） |
| #1924 | S10-002 | fixed | ### S10-002｜Medium｜测试质量｜app/evaluation/runtime_corpus.py:524-529 |
| #1924 | S10-003 | fixed | ### S10-003｜Medium｜测试质量｜app/evaluation/runner.py:1406-1414 |
| #1924 | S10-004 | fixed | ### S10-004｜Medium｜契约/正确性｜app/extensions_platform/broker.py:280 |
| #1924 | S10-005 | fixed | ### S10-005｜Medium｜安全（纵深）｜app/extensions/webgis-tools/index.mjs:250-273, 353-373 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1924 | S10-006 | fixed | ### S10-006｜Medium｜测试质量｜app/evaluation/runner.py:872-925 + security_corpus.py:43/86 |
| #1925 | S11-002 | fixed | ### S11-002｜Medium｜安全/供应链｜distribution.py:353 + 184-214 |
| #1925 | S11-003 | fixed | ### S11-003｜Medium｜安全｜distribution.py:100-102、125-126、157-158 |
| #1925 | S11-004 | fixed | ### S11-004｜Medium｜错误处理｜capability_certification.py:459 |
| #1926 | S12-002 | fixed | ### S12-002｜Medium｜正确性/并发｜host.py:1478-1508（_on_worker_death） |
| #1926 | S12-003 | fixed | ### S12-003｜Medium｜安全（隔离边界）｜host.py:1129-1135 |
| #1926 | S12-004 | fixed | ### S12-004｜Medium｜正确性（描述符丢失）｜sdk/tool.py:116-120、:254-276 |
| #1926 | S12-005 | fixed | ### S12-005｜Medium｜正确性（激活序）｜resolver.py:113-116 + host.py activate_all |
| #1926 | S12-006 | fixed | ### S12-006｜Medium｜安全（发布治理）｜marketplace/service.py:42、:126-133；cli.py:1134 |
| #1926 | S12-007 | fixed | ### S12-007｜Medium｜并发｜marketplace/store.py:253-272、:157、:196 |
| #1926 | S12-008 | fixed | ### S12-008｜Medium｜安全（完整性校验缺口）｜host.py:786-801 |
| #1927 | S13-003 | fixed | - S13-003｜Medium｜资源/正确性｜spawn.py:44-50 + client.py:159-162：RLIMIT_CPU 是进程累计 CPU 秒，而 worker 是长驻进程，要服务很多次调用。累计超过 max_cpu_seconds（默认 60）后内核会发 SIGXCPU/SIGKILL，宿主把它当崩溃计数，累计到 max_worker_crashes 后扩展会被隔离。原本只想限制单次调用，结果限 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1927 | S13-004 | fixed | - S13-004｜Medium｜并发｜client.py:146, 274-297：start() 重建 self._frames 后，旧进程的 reader 线程仍引用 self._frames 和 self。旧 reader 的 finally: self._frames.put(__eof__) 可能落进新队列，导致新 worker 的首个调用误报 EOF 崩溃。ProtocolError 分支里的 self |
| #1927 | S13-005 | fixed | - S13-005｜Medium｜正确性｜server.py:437：seq >= max_events 是在第 max_events 帧已发出后才抛错。一个恰好产生 max_events 个事件的合法流也会以 STREAM_LIMIT_EXCEEDED 失败（上限变成实际只能发 max_events-1 个才算成功）。客户端 client.py:552 的判定是 > max_events，两侧口径不一致。看代码推断 |
| #1927 | S13-006 | fixed | - S13-006｜Medium｜配置契约｜projection_v3.py:309-313：provider 流预算把 max_events 硬编码为 10000，忽略 manifest 的 execution.max_stream_events 和运维的 EXTENSION_MAX_STREAM_EVENTS（settings_bridge.py:95 解析了但这条路径没用）。看代码推断。NEW |
| #1927 | S13-007 | fixed | - S13-007｜Medium｜诚实披露｜accessibility_manifest.py:74-94：色盲声明的默认文案写着“通过可分辨校验”，校验不可用走异常分支时（checked=False）这句文案原样保留，导出物会对一个没校验过的色带宣称已通过。看代码推断。修复：默认文案改成“未校验”，校验通过后再改写。NEW |
| #1928 | S49-001 | fixed | ### S49-001｜Medium｜安全（提示注入）｜app/services/chat/context/formatters.py:98-117（调用：layer_schema.py:264,287） |
| #1928 | S49-002 | fixed | ### S49-002｜Medium｜错误处理｜formatters.py:105-107 |
| #1928 | S49-003 | fixed | ### S49-003｜Medium｜数据/GIS 正确性｜app/services/cartography_service.py:114-121, 285 |
| #1928 | S49-004 | fixed | ### S49-004｜Medium｜正确性｜app/services/cartography/memory_harvest.py:59-66 + project_memory.py:166,185-195 |
| #1928 | S49-005 | fixed | ### S49-005｜Medium｜正确性（学习信号膨胀）｜memory_harvest.py:66-81 |
| #1928 | S49-006 | fixed | ### S49-006｜Medium｜并发｜app/services/cartography/intent_learning.py:367-381 |
| #1928 | S49-007 | fixed | ### S49-007｜Medium｜正确性｜intent_learning.py:343；selfheal_policy.py:506-510 |
| #1928 | S49-008 | fixed | ### S49-008｜Medium｜正确性｜intent_learning.py:250-272 |
| #1928 | S49-009 | fixed | ### S49-009｜Medium｜数据/DB 兼容｜cartography_metrics_store.py（ts 默认 models/cartography_quality.py:42-62）、cartography_ratchet.py:363,374 |
| #1928 | S49-010 | fixed | ### S49-010｜Medium｜正确性｜cartography_metrics_store.py query_quality_trend |
| #1928 | S49-011 | fixed | ### S49-011｜Medium｜正确性｜cartography_ratchet.py:289-293 |
| #1929 | S50-001 | fixed | S50-001｜Medium｜正确性/多租户｜execution_engine.py:701-710 → context_assembler.py:342-349,534-540,177-180｜引擎调用 assemble() 从不传 org_id；knowledge_block 在 org 为空时直接 return ""，导致 legacy 路径的 ProjectKnowledge 卡片永不注入（ADR-0206  |
| #1929 | S50-002 | fixed | S50-002｜Medium｜安全（提示注入）｜context/layer_schema.py:262-263,283-284；context_builder.py:200-203｜map_state 为客户端可写面，featureCount、user_location.accuracy 原样拼进系统消息，未经 _xml_fence，绕过 #555/P1-4 的 untrusted 围栏纪律（alias/type/n |
| #1929 | S50-003 | fixed | S50-003｜Medium｜错误处理/健壮性｜context_builder.py:201,209,212,231；layer_schema.py:286｜{lng:.6f}、{zoom:.2f}、{center[0]:.4f}、bounds {w:.3f}、{opacity:.0%} 对客户端写入的字符串/None 抛 ValueError/TypeError；build_map_state_summary 在  |
| #1929 | S50-004 | fixed | S50-004｜Medium｜正确性｜context/layer_schema.py:160-166,250-253｜visibility 匹配用子串 aid in ref_id or ref_id in aid 回退：短 id（如 "ref_1"/"ab1"）会命中别的 ref，导致模型看到错误的 可见/隐藏 状态、样式与要素数，并错误地把层 pin 进 inventory（H05）｜逻辑推断｜仅精确匹配或显式 r |
| #1929 | S50-005 | fixed | S50-005｜Medium｜资源/正确性｜context_policy.py:399-417｜OFFLOAD_REF 每次超预算组装把历史 tool 结果 store(session, parsed, prefix="data") 铸成新 ref；去重缓存仅进程内（多 worker/重启后重复铸造），新 ref 进入 list_refs → inventory 行数、“N 个数据引用”概览膨胀，反过来增加下一轮上下 See [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md). |
| #1930 | S51-001 | fixed | S51-001｜Medium｜正确性/降级｜app/services/chat/model_routing_bridge.py:163-185 + model_config.py:76 |
| #1930 | S51-002 | fixed | S51-002｜Medium｜错误处理/可观测｜model_runtime/provider.py:259-278；model_routing_bridge.py:239-245 |
| #1930 | S51-003 | fixed | S51-003｜Medium｜正确性/API 契约｜llm_client.py:687-805；execution_engine.py:1599-1611、2463-2475、2641-2646 |
| #1930 | S51-004 | fixed | S51-004｜Medium｜数据完整性/错误处理｜execution_engine.py:3157-3179、3233；session_data.py:903-923 |
| #1931 | S52-001 | fixed | ### S52-001｜Medium｜错误处理/并发｜app/services/chat/pi_post_dispatch.py:248-332（调用方 app/agent_pi_bridge.py:940） |
| #1931 | S52-002 | fixed | ### S52-002｜Medium｜正确性/状态｜app/services/chat/plan_orchestrator.py:486-512, 473-477 |
| #1931 | S52-003 | fixed | ### S52-003｜Medium｜可靠性｜app/services/chat/pi_rpc_client.py:491-504 |
| #1932 | S53-002 | fixed | ### S53-002｜Medium｜正确性/可维护性｜app/services/chat/tool_surface_v3.py:60-77, 284-299 |
| #1932 | S53-003 | fixed | ### S53-003｜Medium｜正确性｜app/services/context_assembly/allocator.py:204-235 |
| #1932 | S53-004 | fixed | ### S53-004｜Medium｜正确性｜app/services/collab/presence.py:463-479 |
| #1932 | S53-005 | fixed | ### S53-005｜Medium｜正确性/一致性｜app/services/collab/leases.py:225-272 |
| #1939 | S96-002 | fixed | ### S96-002｜Medium｜正确性｜graph_builder.py:276-300（平行边去重） See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1939 | S96-003 | fixed | ### S96-003｜Medium｜正确性｜graph_builder.py:244-253 + 382-399（NetworkDataset 重建） See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1939 | S96-004 | fixed | ### S96-004｜Medium｜正确性｜app/services/network/centrality.py:132-160（eigenvector_centrality） See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1939 | S96-005 | fixed | ### S96-005｜Medium｜GIS 语义｜centrality.py:300-306 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1939 | S96-006 | fixed | ### S96-006｜Medium｜安全/多租户｜app/services/nature_resource_analyzer.py:143-152 |
| #1940 | S96b-001 | fixed | ### S96b-001｜Medium｜正确性/错误处理｜app/services/network/allocation.py:211-218（p-median）、320-327（p-center） See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1940 | S96b-002 | fixed | ### S96b-002｜Medium｜正确性｜allocation.py:1065-1076（_exact_milp_result） See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1940 | S96b-003 | fixed | ### S96b-003｜Medium｜GIS 正确性/渲染｜app/services/mvt.py:1416-1425, 1308-1313, 1238-1247 |
| #1940 | S96b-004 | fixed | ### S96b-004｜Medium｜API 契约/正确性｜mvt.py:69-79, 1020-1037, 1399-1431；layer.py:485-515 |
| #1941 | S97b-001 | fixed | ### S97b-001｜Medium｜正确性/GIS｜app/services/network/routing.py:818-842 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1941 | S97b-002 | fixed | ### S97b-002｜Medium｜正确性/错误处理｜app/services/planning/deps.py:126-150 + app/services/plan_mode.py:598-624, 901-904 |
| #1941 | S97b-003 | fixed | ### S97b-003｜Medium｜并发/资源｜app/services/plan_mode.py:1145-1163 |
| #1941 | S97b-004 | fixed | ### S97b-004｜Medium｜GIS 正确性｜app/services/network/interaction.py:148, 239 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1941 | S97b-005 | fixed | ### S97b-005｜Medium｜GIS 正确性/诚实性｜app/services/network/interaction.py:39-41, 86, 163, 256 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1941 | S97b-006 | fixed | ### S97b-006｜Medium｜错误处理｜app/services/network/vrp.py:180-181, 221-235, 251-263 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1941 | S97b-008 | fixed | ### S97b-008｜Medium｜可用性/错误处理｜app/services/offline_preflight.py:209-218 |
| #1942 | S4g-001 | fixed | - S4g-001（#1853）：知识卡片注入时不做 liveness 复核，rebuild 也不清扫已消失的权威行。仍存在，见 card.py、indexer.py。 |
| #1942 | S4g-002 | fixed | - S4g-002（#1853）：行预算 400 小于一次 rebuild 的产出（最多约 590 行）。仍存在，见 store.py:328-370、indexer.py 各个 limit。 |
| #1942 | S4g-003 | fixed | - S4g-003（#1853）：rebuild 按源 try/except 时没有 savepoint，Postgres 事务一旦 abort，后面的源会连锁失败。仍存在，见 indexer.py:429-438。 |
| #1942 | S4g-004 | already_fixed | - S4g-004（#1853）：只要有直接回指的行，就跳过 ref-tag 引用行的降级。仍存在，见 invalidation.py:459。 |
| #1942 | S4g-006 | fixed | - S4g-006（Low）：父产物 ref-tag 的 token 用的是子产物的指纹。仍存在，见 indexer.py:154。 |
| #1942 | S4g-007 | fixed | - S4g-007（Low）：卡片里的 authority 指针没有转义。仍存在，见 card.py:110、118。 |
| #1942 | S4g-021 | fixed | - S4g-021/022/023（Low）：仍存在，见 freeze.py:295-297、contract.py:237-245、contract.py:307-349（status 不处理 permission_changed）。 |
| #1942 | S4h-004 | fixed | - S4h-004（#1854）：抢占过期 lease 不是原子操作。仍存在，见 store.py:284-297。 |
| #1942 | S4h-007 | fixed | - S4h-007/008（Low）：仍存在，见 rebind.py:310、resume.py:539-543（docstring 写在语句之后）。 |
| #1942 | S4h-009 | fixed | - S4h-009（Low）：lease 的同步文件 IO 跑在事件循环上。仍存在，见 resume.py:104、112。 |
| #1942 | S4d-014 | fixed | - S4d-014（Low）：planning/store.py:176-203 的 save 先写缓存，再做守卫判断。仍存在。 |
| #1943 | S99-002 | fixed | ### S99-002｜Medium｜可复现性/正确性｜app/services/provenance/manifest.py:211、:148-158、:272-281 See [2026-11-06-provider-provenance-rag-review.md](2026-11-06-provider-provenance-rag-review.md). |
| #1943 | S99-003 | fixed | ### S99-003｜Medium｜安全｜app/services/provenance/manifest.py:211 See [2026-11-06-provider-provenance-rag-review.md](2026-11-06-provider-provenance-rag-review.md). |
| #1943 | S99-004 | fixed | ### S99-004｜Medium｜数据一致性/多租户｜app/services/rag/faiss_store.py:498-519、:609-613 See [2026-11-06-provider-provenance-rag-review.md](2026-11-06-provider-provenance-rag-review.md). |
| #1943 | S99-005 | fixed | ### S99-005｜Medium｜可靠性｜app/services/provider_health.py:340-344 See [2026-11-06-provider-provenance-rag-review.md](2026-11-06-provider-provenance-rag-review.md). |
| #1943 | S99-006 | fixed | ### S99-006｜Medium｜契约/多租户一致性｜app/services/rag_service.py:247-251 vs app/services/rag/faiss_store.py:579-583 See [2026-11-06-provider-provenance-rag-review.md](2026-11-06-provider-provenance-rag-review.md). |
| #1944 | S100-001 | fixed | - S100-001｜Medium｜并发/治理｜app/services/review/service.py:451-481｜rebase 的状态与 merge_in_progress 检查在锁外（451-458），锁内 _mutate 直接 cur.status = SUBMITTED，既不复检状态也不走 _transition。并发 merge/reject/withdraw 在检查后完成时，rebase 会把  |
| #1944 | S100-002 | fixed | - S100-002｜Medium｜并发/正确性｜app/services/review/merge.py:375-409｜base 校验 read_current_revision 与 checkpoint 之间无 CAS；CheckpointIntent 未带 expected_revision，之后 rev 取自 checkpoint 结果。并发提交落在两者之间时，审批过的 intents 被回放到未审阅的新状 |
| #1944 | S100-003 | fixed | - S100-003｜Medium｜数据/GIS正确性｜app/services/rs/spectral_engine.py:1-5,59-85；stac_client.py 全文｜文档声称"带云掩膜的波段代数"，但全链路未读 SCL/云掩膜资产；仅挑 eo:cloud_cover 最低的一景（max_items=1）。云/阴影像元计入 NDVI 统计与 vegetation_coverage（compute_ndv See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1944 | S100-004 | fixed | - S100-004｜Medium｜数据/GIS正确性｜app/services/rs/stac_client.py:468-480；spectral_engine.py:110,212｜数组保持 UTM 网格，但 bounds 用 transform_bounds 转成 WGS84 外包矩形，下游按矩形贴图（image_size + bounds）。UTM 网格相对经纬网有子午线收敛角旋转、外包矩形大于真实足迹，A See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1944 | S100-005 | fixed | - S100-005｜Medium｜资源/错误处理｜app/services/runtime_validator.py:405-411｜subprocess.run(["npx","tsx",...], timeout=90) 超时只杀直接子进程 npx，tsx/node/Chromium 孙进程成为孤儿继续运行并写同一 runtime 目录；CP-14 的"等孤儿收尾"只等到线程返回，实际进程未终止，全局 2 并发 |
| #1944 | S100-006 | fixed | - S100-006｜Medium｜正确性｜app/services/runtime_validator.py:339-347 vs 105-110,289-290｜valid 判定不含 failedRequests，而注释声明"缺失 PNG 以 404/failedRequests 作为诚实信号"——栅格/瓦片缺失时仍判 valid=True（评分侧却扣分），两口径分叉｜追链推断｜valid 加入 len(fail |
| #1945 | S101-001 | fixed | S101-001｜Medium｜正确性/多租户｜app/services/session_data_redis.py:468-535, 1653-1679｜Redis overwrite 不检查 ref 是否存在，直接 SET data_key；协议（session_data_protocol.py:123-126）和内存后端都约定"ref 不存在返回 False"，checkpoint.py:501-505 的注释 |
| #1945 | S101-002 | fixed | S101-002｜Medium｜数据正确性/缓存｜session_data_redis.py:404-407, 519, 1941-1960｜_refresh_session_ttl 没有续期 ref_revisions 和 ref_digests 两个 hash，它们只在 store/overwrite 时 expire。会话靠读和编辑保活超过 4h 后，revision hash 过期；下一次 overwrite |
| #1945 | S101-003 | fixed | S101-003｜Medium｜性能/安全降级｜session_data_protocol.py:38-52, 440, 485, 538｜_conversation_owner_kind 在 async 的 get_ref_data / get_ref_data_shared / get_ref_descriptor_authorized 里同步调用 SessionLocal() 查库，每次读 ref 都阻塞事件循 |
| #1945 | S101-004 | fixed | S101-004｜Medium｜正确性/数据丢失｜session_data.py:397-415；session_plan.py:594-596｜内存后端字节预算淘汰：单个 payload 估算超过 SESSION_STORE_MAX_BYTES（50MB）时，循环会把本会话所有旧 ref 全部逐出，连 session-plan 信封和 history alias 也一起删掉（spill 只按 ref_id 存，al |
| #1945 | S101-005 | fixed | S101-005｜Medium｜错误处理｜session_plan.py:623-631, 667-676｜save_session_plan 和 _archive_envelope 没有用 is_unavailable_ref 检查 store() 的返回值。Redis 抖动时，CURRENT 或 history alias 会指向一个不存在的哨兵 ref，后续 load 返回 None，静默重建空计划；plan. |
| #1945 | S101-006 | fixed | S101-006｜Medium｜正确性｜session_plan.py:1273-1287｜工具成功分支用 geojson_ref 把能力行标成 complete，并 register_artifact。但 geojson_ref 可能是 ref:redis-unavailable-* 哨兵：它以 "ref:" 开头，能通过 1284 行的检查，结果计划声称已完成，并登记了一个不存在的产物，违反了协议"ref 存在  |
| #1945 | S101-007 | fixed | S101-007｜Medium｜逻辑｜session_plan.py:1150, 766-771｜失败分支 _mark_progress(status="failed") 会把已经 complete 的能力行改成 failed（row.status != status 即算变更）。同一工具后续的一次失败调用（比如换参数重跑）会抹掉已验证的完成事实，DAG 下游被阻塞，bound_ref 却保留旧值｜追链推断｜失败只作 |
| #1945 | S101-008 | fixed | S101-008｜Medium｜数据完整性｜s3_blob_store.py:483-527, 615-651｜put_blob_stream 对内容寻址键（64 位 hex）不校验实际字节的 sha256 是否等于 key，也不做 put-if-absent，与 put_blob_from_path 的 B2 守卫不一致。get_blob_stream 先把字节 yield 给消费方，到尾部才校验 digest，损 |
| #1948 | S104-002 | fixed | - S104-002｜Medium｜API契约/正确性｜spatial_events/service.py:444-506 + ledger.py:656｜一个 watch 多个 actions 共用 (watch_id,event_id) 唯一 fire 行：["notify_only","mission_create"] 时 notify 先写 outcome=notified，mission 动作 claim_ |
| #1948 | S104-003 | fixed | - S104-003｜Medium｜可靠性｜service.py:189-195 + ledger.py:325-350｜governor defer / CAS 冲突 / mission deferred 均走 mark_failed，计入 attempts，满 max_attempts(5) 即终态 failed；文档宣称“持久重试、不丢”。背压持续 ~75s 即永久丢触发。修：defer 不递增 attempt |
| #1948 | S104-004 | fixed | - S104-004｜Medium｜正确性｜service.py:287-312｜事件因任一 watch 被 defer 而重投时，已完成（notified/mission_created）的其它 watch 不走重试短路，重新 evaluate_watch 并 CAS 写状态：consecutive_hits/window 计数对同一事件重复累加，last_fired_at 被刷新（冷却被重置）。修：state.l |
| #1948 | S104-005 | fixed | - S104-005｜Medium｜数据正确性｜ledger.py:395-432｜coalesce 分组键不含 session_id/project_id：同 org 同 subject_key 不同会话的事件被互相吞并；且幸存者取最高优先级而非最新，最新 payload 被标 coalesced 丢弃（代码注释自认）。修：分组键加 session/project，幸存者取最新并提升其 priority。NEW |
| #1948 | S104-006 | fixed | - S104-006｜Medium｜功能失效｜service.py:637-652 + 683-687｜_resolve_aoi 依赖 self._session_store，但单例 SpatialEventService() 不注入 store → aoi_ref 条件生产中恒 aoi_ref_unresolvable，永不触发；且 get_ref_data_sync(ref) 不带 session/org 作用域 |
| #1948 | S104-007 | fixed | - S104-007｜Medium｜多租户(组织内)｜mission_bridge.py:302-315,373｜mission_revise/resume 在 watch 无 project 作用域时取 org 内任意未完成 mission 的 cands[-1]（依赖 list_unfinished 排序），可改写/恢复同组织其他用户的 mission。修：要求显式 mission/project 作用域并校验  |
| #1948 | S104-008 | already_fixed | - S104-008｜Medium｜GIS正确性(CRS)｜spatial_decision/target_resolver.py:264-316｜高德 district/geocode 返回 GCJ-02 坐标，直接当 WGS84 写入 TargetAreaSpec（约数百米偏移），而同文件 GeoJSON 路径已做 gcj02→wgs84。修：transform_geojson(geom,'gcj02','wgs See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1948 | S104-009 | fixed | - S104-009｜Medium｜正确性｜target_resolver.py:318-359｜离线子串字典：任意包含“北京/高新/朝阳/测试”等的查询（如“广州北京路”“西安高新区”“长春朝阳区”）被解析到北京/成都，confidence=0.85、source=offline_geocode，与“绝不静默回退北京”的声明相悖；且在有 provider 但其失败时仍兜底。修：仅测试环境启用或精确匹配，置信度降为需 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1948 | S104-010 | fixed | - S104-010｜Medium｜GIS正确性｜guardrail_middleware.py:398｜AUTO_FLIP 写回 container[key]=[lng,lat]，Point 的 Z（高程）被丢弃。修：保留 pair[2:]。代码推断。NEW See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1949 | S105-002 | fixed | ### S105-002｜Medium｜正确性｜spatial_repair_pipeline.py:1080-1091 × plan_repair_ops:400-403 / spatial_quality_gate.py:780-784 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1949 | S105-003 | fixed | ### S105-003｜Medium｜数据正确性｜spatial_repair_pipeline.py:1001-1014 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1949 | S105-004 | fixed | ### S105-004｜Medium｜错误处理/数据丢失｜spatial_repair_pipeline.py:588-611 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1949 | S105-005 | fixed | ### S105-005｜Medium｜健壮性｜spatial_quality_service.py:766-782（DUPLICATE_PRIMARY_KEY） See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1949 | S105-006 | fixed | ### S105-006｜Medium｜健壮性｜spatial_meta_profiler.py:378/399/404（文件内对应 list(dict.fromkeys(vals))） |
| #1949 | S105-007 | fixed | ### S105-007｜Medium｜测试质量｜spatial_repair_matrix.py:517-524 与 591 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1949 | S105-008 | fixed | ### S105-008｜Medium｜数据正确性｜spatial_repair_pipeline.py:383-391 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1950 | S1-006 | fixed | ### S1-006 ｜Low｜数据解析｜gov_data_adapter.py:225,249-256,292-294 See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1950 | S1-007 | fixed | ### S1-007 ｜Low｜配置文档｜.env.example:132-133、app/core/config.py:544 See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1950 | S1-008 | fixed | ### S1-008 ｜Low｜配置文档｜.env.prod.example:11-17 See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1950 | S1-009 | fixed | ### S1-009 ｜Low｜CI 覆盖｜.github/workflows/production.yml:1134-1215 See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1950 | S1-010 | not_a_bug | ### S1-010 ｜Low｜可维护性｜app/adapters/gov/gov_data_adapter.py:68-69,166-167 See [2026-11-05-root-issues.md](2026-11-05-root-issues.md). |
| #1950 | S2-003 | fixed | ### S2-003｜Low｜错误处理｜app/api/routes/auth.py:204-226 |
| #1950 | S2-004 | fixed | ### S2-004｜Low｜性能/DoS｜app/api/routes/auth.py:321-359 |
| #1950 | S2-005 | fixed | ### S2-005｜Low｜可维护性｜app/agent_pi_bridge.py:761-762 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L01 | fixed | - S2b-L01 :1774 跨 worker 转发 abort(session_id) 丢失 source，system/policy 被记为 user→cancelled（生产 session_cancellation 直接用 entry.bridge，影响面小）。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L02 | fixed | - S2b-L02 :1116 loop.create_task(bridge.abort(...)) 未保留引用，任务可被 GC；异常未观测。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L03 | fixed | - S2b-L03 :878-879 TaskTracker 记账 except Exception: pass 全吞，无日志。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L04 | fixed | - S2b-L04 :941-944 两个分支行为相同（cache_plan_sse_unconditionally 冗余）。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L05 | fixed | - S2b-L05 :294-301 _pop_session_entries 每次都顺带清除 "" 会话键条目，可能清掉其他无 session 回调的缓存。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L06 | already_fixed | - S2b-L06 :2162-2165 非流式 drain 对任意事件类型覆盖 final_text，agent_end 无文本时可能返回工具事件文本。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L07 | fixed | - S2b-L07 :2805-2826 timeout/process_died 的 error SSE 不带 turn_id（task_error 带），前端关联不一致。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L08 | fixed | - S2b-L08 :3104 int(os.getenv("PI_BRIDGE_POOL_SIZE")) 非数字直接启动失败，未走 _env_float_drain 式容错。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L09 | fixed | - S2b-L09 :3191 Swarm 主 turn 互斥检查为 TOCTOU（检查后 Pi turn 可启动）。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L10 | fixed | - S2b-L10 :3218 swarm 失败 reason 内插异常原文，可能向模型/前端泄露内部信息。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L11 | fixed | - S2b-L11 :3270 org_id 缺省写 "0"，durable 镜像落入伪组织。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S2b-L12 | fixed | - S2b-L12 :2353-2363 / 2995-3016 内外两层 finally 重复 unregister（Redis 两次往返），无害但冗余。 See [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md). |
| #1950 | S3-008 | fixed | - S3-008｜Low｜API 契约｜chat.py:1525-1535 list_sessions：total=len(sessions) 其实是本页条数，不是总数，前端分页会显示错误的总数。 |
| #1950 | S3-009 | fixed | - S3-009｜Low｜错误处理｜chat.py:2318-2331 clear_session：先向 Pi 发 abort，然后才去拿锁；拿锁失败返回 503，结果是会话没删，在途的 turn 却已经被中止。修复：把 abort 放到拿到锁之后。 |
| #1950 | S3-010 | fixed | - S3-010｜Low｜可维护性｜chat_resume.py:83-92：session_key == "" 这个分支实际进不去，因为 chat.py:1217 在没有 session_id 时会填一个随机 uuid hex。注释里描述的匿名续传语义和实际不一致：没有 session_id 的续传永远拿到 buffer-miss 错误。另外 chat.py:107-117 的 _RESUME_LIVE_HOLD_ |
| #1950 | S3-011 | fixed | - S3-011｜Low｜错误处理/信息泄露｜config.py:157-159 返回 连接测试失败: {e}，composition_template_assets.py:141-144 和 data_fabric.py:829 返回 pydantic 异常全文，data_lifecycle.py 的 _map_gc_error 遇到非 GcPlanError 时直接 raise exc，变成 500。 |
| #1950 | S3-012 | not_a_bug | - S3-012｜Low｜可维护性/运维｜config.py:66-78 update_llm_config：只改当前进程里的 engine。多 worker 或多 pod 部署时各进程配置不一致，重启后也会丢失。 |
| #1950 | S3-013 | fixed | - S3-013｜Low｜性能｜config.py:215-310：上传 skill 时同步写文件、同步执行 load_skills，都在 async 处理函数里，会阻塞事件循环；data_fabric 的 _conversation_owner_kind（被 chat_session_views 间接调用）在 async 路径里使用同步 SessionLocal。 |
| #1950 | S3-014 | fixed | - S3-014｜Low｜多租户一致性｜data_fabric.py:94-104：有 org 的用户看不到全局源（org/owner 都为空），没有 org 的用户却能看到，两种身份的可见性不一致。 |
| #1950 | S3-015 | fixed | - S3-015｜Low｜安全｜data_fabric.py:181-198 _require_existing_session_owner：会话行不存在时放行，可以往一个任意的、尚未创建的 session_id 里预先写入 ref（抢注）。由于 session_id 是 uuid，利用难度高。 |
| #1950 | S3-016 | fixed | - S3-016｜Low｜正确性｜data_lifecycle.py:96-109 _owner_scope_values：同时放入裸的 uid 和裸的 org 值。如果某个 user_id 和某个 org_id 的字符串相同（例如都是 "1"），就会越权看到对方 scope 下的对象。修复：只使用带前缀的形式。另外 data_lifecycle.py:416 用 replace(tzinfo=utc) 处理时间，对 |
| #1950 | S5-004 | fixed | ### S5-004｜Low｜可维护性/性能｜app/api/routes/project.py:693-713、1747/1786/1819/1852/1885-1886/1917/1945；project_runtime.py:46-59 |
| #1950 | S5-005 | already_fixed | ### S5-005｜Low｜并发｜project_runtime.py:94、198、234；project.py:915 |
| #1950 | S5-006 | fixed | ### S5-006｜Low｜API 契约｜app/api/routes/map.py:249-273、496-507、688-696 |
| #1950 | S5-007 | fixed | ### S5-007｜Low｜资源｜app/api/routes/map.py:579-614 |
| #1950 | S5-008 | fixed | ### S5-008｜Low｜错误处理｜app/api/routes/local_data.py:61-62 |
| #1950 | S5-009 | already_fixed | ### S5-009｜Low｜资源｜app/schemas/project_runtime_schema.py:19-22（project_runtime.py:103-107 使用） |
| #1950 | S5-010 | fixed | ### S5-010｜Low｜正确性｜app/api/routes/layer.py:466-504、536-542 |
| #1950 | S6-005 | fixed | S6-005 spatial_events.py:336-339 速率桶“驱逐最早写入”实为按 org_id 字典序驱逐，可被构造 org 名挤掉他人窗口。 |
| #1950 | S6-006 | fixed | S6-006 spatial_events.py:373-375 webhook 8KB 上限在 pydantic 已解析整个 body 之后才检查。 |
| #1950 | S6-007 | fixed | S6-007 spatial_events.py:484,499 docstring 说空闲 ≤600 tick，代码 240 tick×15s（=1h）；不一致。 |
| #1950 | S6-008 | fixed | S6-008 report.py:286 share_url 指向 /shared/{code}（JSON 信息）而非 /shared/{code}/view。 |
| #1950 | S6-009 | fixed | S6-009 security_admin.py:368-381 配额 upsert 先查后插，并发 PUT 触发唯一键冲突 500；org_id 不校验存在性。 |
| #1950 | S6-010 | fixed | S6-010 static.py:430 is_public 用未解析的原始路径判断，public/ 下的 symlink 可指向 DATA_DIR 内私有文件（需写权限，纵深防御）。 |
| #1950 | S6-011 | already_fixed | S6-011 templates.py:352-381 删除不做可见性检查：不可见模板返回 403 而非 404，泄露存在性。 |
| #1950 | S6-012 | fixed | S6-012 template_versions.py:55 用 "not found" in text 映射 404，脆弱。 |
| #1950 | S6-013 | fixed | S6-013 upload.py:762-771 删除上传不清理已登记的会话 ref（V3/terrain 车道），留下悬挂 ref。 |
| #1950 | S6-014 | fixed | S6-014 upload.py:306 会话不存在视为首轮写入放行，记录挂在尚未创建的 session_id 上，后续创建该 id 者继承。 |
| #1950 | S6-015 | fixed | S6-015 visual_repairs.py:464-519 已 apply 的提案仍可 reject，决策账本出现“批准+拒绝”矛盾。 |
| #1950 | S6-016 | fixed | S6-016 visual_repairs.py:530-533 width/height 无上界。 |
| #1950 | S6-017 | fixed | S6-017 ws.py:70-77 客户端只传 token（无 "bearer"）时服务器仍选中 "bearer" 子协议，浏览器握手失败；与“容忍”注释相悖。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1950 | S6-018 | fixed | S6-018 ws_collab.py:339-358 注释称 doc <64KB 才随 hello 下发，代码无条件下发；presence 满员连接仍可经 presence 事件 heartbeat 写入注册表、绕开 32 人上限（视 heartbeat 实现）。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1950 | S6-019 | fixed | S6-019 contracts/basemap_profiles.py:349-389 高德条目 crs 默认 "EPSG:3857"，实际 GCJ-02，仅 notes 文字提示，机器可读字段失真。 |
| #1950 | S7-005 | fixed | - S7-005 app/core/network.py:142-186 _PerLoopLockProxy 把 _entered_lock 存在单例上，跨 loop 并发进入会互相覆盖导致释放错锁/死锁；当前生产无调用方（仅 legacy 兼容）。 |
| #1950 | S7-006 | fixed | - S7-006 app/contracts/workbench_locks.py _MAX_LOCK_IDS=64：第 65 个起的用户锁静默失效（应拒绝或报告截断）。 |
| #1950 | S7-007 | fixed | - S7-007 workbench_locks _lock_matches 双向前缀：锁 roads 会误锁独立图层 roads-2024（误报方向，fail-closed）。 |
| #1950 | S7-008 | fixed | - S7-008 app/core/i18n.py:204-214 非默认 locale 下，用 category 通用短语覆盖路由给出的具体 detail（如“会话不存在”→ 通用 404 文案）；PlatformError 自定义 user_message 同样被覆盖。 |
| #1950 | S7-009 | fixed | - S7-009 i18n.py _reissue 用 dict(response.headers)，多个 Set-Cookie 只留最后一个（4xx 清 cookie 场景）。 |
| #1950 | S7-010 | fixed | - S7-010 app/core/errors.py classify_exception：上游 httpx 401/403（如 API key 失效）归为 VALIDATION/400，应为 DEPENDENCY_FAILURE/PERMISSION。 |
| #1950 | S7-011 | fixed | - S7-011 app/core/idempotency.py:274 / i18n.py body += chunk 对 50MB 体为二次方拷贝，应用 bytearray/list join。 |
| #1950 | S7-012 | fixed | - S7-012 app/core/config.py _validate_no_ssrf 在 Settings 构造（import 期）做同步 DNS；air_gapped/DNS 慢时启动阻塞，且 DNS 失败一律放行（仅 warning）。 |
| #1950 | S7-013 | fixed | - S7-013 app/core/base_layers.py resolve_provider_id_to_name 子串双向模糊匹配，短 id（如 "a"）命中首个底图，非“解析不出返回 None”。 |
| #1950 | S7-014 | fixed | - S7-014 app/contracts/completion.py MapCompletionFinding.to_dict 丢弃 family 字段（序列化往返丢信息）。 |
| #1951 | S8-L01 | fixed | S8-L01 rate_limiter.py:185-199 _maybe_evict 只清空 deque；只含过期时间戳的 key 不算空，不会被清。超过上限按“最旧”整 key 删除，攻击者可以用大量不同 key 把受害者的登录失败台账挤掉。 |
| #1951 | S8-L02 | not_a_bug | S8-L02 password_policy.py:49,93-110 失败计数在进程内，多 worker 时每个进程各自计数，渐进延迟被 worker 数稀释（文档已注明 best-effort）。 |
| #1951 | S8-L03 | fixed | S8-L03 password_policy.py:65 常见口令只做精确小写匹配，“Password1!” 这类变体不拦截。 |
| #1951 | S8-L04 | fixed | S8-L04 tenancy.py:248-253 org 取自 JWT claim，不实时查 DB；用户换组织后旧 token 在过期前仍在原组织（scopes 用 require_admin 实时校验，org 没有）。 |
| #1951 | S8-L05 | fixed | S8-L05 signing.py:24-28 JWT_SECRET_KEY 为空时退化为固定密钥派生，没有 fail-loud（依赖 config 侧校验）。 |
| #1951 | S8-L06 | fixed | S8-L06 network_dependency.py:82-94 endpoint 为空或是非 URL 描述（如“allowlist 端点”）时 is_local 恒为 False，air-gapped 下可能误报 unavailable，属于诚实降级，但没有单独的 unknown 状态。 |
| #1951 | S8-L07 | fixed | S8-L07 chain_gate.py:430-441 在 expected_stages 模式下，未知阶段名（拼写错误）不报错，只会让门恒失败；na_stages 有 unknown 披露，expected 没有。 |
| #1951 | S8-L08 | fixed | S8-L08 case_matrix.py:611 / conformance.py:690 ID 去重用 assert，python -O 下失效。 |
| #1951 | S8-L09 | fixed | S8-L09 failure_corpus.py:_exc pyproj 或 cancellation 导入失败时返回 None，案例静默退化为只匹配 message，回归语义变了，而且没有披露。 |
| #1951 | S9-002 | fixed | - S9-002｜Low｜1｜mission_driver.py:216 if step.task_id in frontier.get(bucket) or []: 运算符优先级错误，等价于 (x in get(bucket)) or []；桶缺失时 TypeError。当前 MissionFrontier 五桶均有 default，属潜伏。改为 in (frontier.get(bucket) or [])。追链 See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S9-003 | fixed | - S9-003｜Low｜3/9｜mission_driver.py:165-177、88-93 通过改写全局 os.environ 与模块级 store._utcnow 打补丁，非线程/并发安全；并行跑 benchmark（pytest-xdist 线程或同进程并发）时会互相污染。改为注入式时钟/参数。NEW See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S9-004 | fixed | - S9-004｜Low｜8｜mission_driver.py:349 ok = ok and True 无意义；终态探针只捕获任意 Exception，任何崩溃都算"正确拒绝"，应限定为 InvalidTransition/FencingError 类。NEW See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S9-005 | fixed | - S9-005｜Low｜8｜mission_driver.py:189、206 及 _execute_fault 使用私有 runtime._require_lease，与"走生产缝"的声明不符，私有 API 变化会让基准静默漂移。NEW See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S9-006 | fixed | - S9-006｜Low｜8｜replay.py:173 normalization_report_var.get() 读取 contextvar，若某步 dispatch 未重置该变量，会带出上一步的 repairs（跨步串值）。建议每步前 set([])。追链推断。NEW See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S9-007 | fixed | - S9-007｜Low｜6｜replay.py:165 simulate_agent_loop 会对破坏性工具真实 dispatch，仅事后检查是否被 tier-3 闸拒绝；若闸失效，评测本身会执行破坏性副作用。应在 dispatch 前对 destructive_level>=3 做短路或强制沙箱 registry。NEW See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S9-008 | fixed | - S9-008｜Low｜8｜hard_negative_corpus.py:177-186、mission_corpus.py:295-303、quality_corpus.py:1214/1337/1348-1349 构建期守卫使用 assert，python -O 下全部失效（重复 id、空 query 等不再被拦截）。改为 raise AssertionError/ValueError（index.py/re See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S9-009 | fixed | - S9-009｜Low｜7｜index.py:39-57 _BUILDER_CACHE 永不失效且返回可变 list 本体，调用方修改会污染全局缓存与后续 version_hash。返回 tuple 或拷贝。NEW See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S9-010 | fixed | - S9-010｜Low｜9｜index.py:209-215 前缀唯一性检查放在逐 id 循环内（重复做 N 次），且只校验 prefix 注册冲突、没校验 id 是否真以该 prefix 开头；注释"跨语料唯一性锚"未被实际强制。NEW See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S9-011 | fixed | - S9-011｜Low｜8｜retrieval_corpus.py:151-161 stride 抽样 corpus[::stride][:sample_n] 在 ceil 步长下实际样本数可远少于 sample_n（如 total=801,n=800→stride=2→401 条），docstring 暗示取 n 条。NEW See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S10-L01 | fixed | - S10-L01 runner.py:353-355/916-919：descriptor 抛异常的工具直接 continue，forbid_network_tools 对未注册/异常工具静默放行。 See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S10-L02 | fixed | - S10-L02 runner.py:65-77：_reduce("sum") 对 dict 项取“第一个数值字段”，依赖字典序，语义随意。 See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S10-L03 | fixed | - S10-L03 runner.py:577-591：policy tier 改写进程级 os.environ[SKILL_POLICY_ENV]，并发跑案例时会串扰。 See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S10-L04 | fixed | - S10-L04 runner.py:1161-1164：expect_error_contains 只在前 200 字符里匹配，长前缀错误会误判失败。 See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S10-L05 | fixed | - S10-L05 runner.py:1145-1147：fixture: 前缀只在顶层参数里替换，嵌套参数不生效。 See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S10-L06 | fixed | - S10-L06 runtime_metrics.py:379-381 等：空分母返回 0.0 而不是 None，“未测”和“全错”无法区分（与 runner 的 None 约定不一致）。 See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S10-L07 | fixed | - S10-L07 runtime_metrics.py:423-424：truncation_incidence 把任何 violation（含 over_budget）都计为截断，和 overflow 重复计数。 See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S10-L08 | fixed | - S10-L08 retrieval_eval_corpus.py:34-35：MIN_EVAL_CORPUS_SIZE 连续赋值两次（300 被 350 覆盖），是残留。 See [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md). |
| #1951 | S10-L09 | fixed | - S10-L09 broker.py:137-144：op=="secret_get" 且 payload 不是 dict 时，except 分支里的 payload.get 抛 AttributeError，逃出 handle()，破坏“永不抛出”契约。 |
| #1951 | S10-L10 | fixed | - S10-L10 broker.py:291-292：HTTP 错误返回 ok=True/status 0，审计记为成功；broker.py:322-327（api<1.2 平铺模式）resolve 与 open 之间存在 symlink TOCTOU（读路径）。另外 SSRF 校验与实际连接是两次解析（DNS rebinding），是否由 guarded_client 兜底未核实。 |
| #1951 | S10-L11 | fixed | - S10-L11 index.mjs:82-105：_pinnedTurnToken 是模块级全局，取不到标记时回退到上一次的 token（同进程多会话时可能串用，后端仍会校验活跃轮）；index.mjs:153 在读响应体前就 clearTimeout，大响应体读取不受超时约束。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S11-005 | fixed | - S11-005 cli.py:987/1016：未知 extension_id 时报告无 trust/execution_mode 键，非 --json 输出 KeyError。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S11-006 | fixed | - S11-006 cli.py:1144-1153 _pack_blob_bytes：rglob 的 is_file() 对符号链接为真，tar.add 保留为 SYMTYPE，publish 出的包安装必然被拒；也会把 .certification.json 打进包。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S11-007 | fixed | - S11-007 certification.py:87 / capability_certification.py:136：signature 检查对 unsigned / signed_untrusted 视为 pass，认证报告可对未签名包给 certified=true。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S11-008 | fixed | - S11-008 context.py:518-521：recipe 注册未包 try（非 typed 错误）；同一 spec 内重复 recipe id 两次均通过预检。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S11-009 | already_fixed | - S11-009 context.py:134-152 _project_theme：无先查碰撞，可能覆盖同 id 主题后 undo 误删。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S11-010 | fixed | - S11-010 context.py:707-709 load_sibling：exec_module 失败后坏模块残留 sys.modules，后续调用返回半初始化模块。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S11-011 | fixed | - S11-011 conformance.py execute_case：state: 断言用激活前取得的 record 引用（reload 换记录则陈旧）；fail: 只看激活诊断不含发现诊断。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S11-012 | fixed | - S11-012 capability_certification.py:437-439：报告指纹在认证结束后重算，与被认证内容存在 TOCTOU。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S11-013 | fixed | - S11-013 cli.py:135-140 _build_host 文档称“绝不 activate”，但 certify 两路径都会真实激活（执行扩展代码），文档误导。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S11-014 | already_fixed | - S11-014 distribution.py:72-76 docstring 声称 filter="data" 兜底，未实现（与 S11-003 同源的文档问题）。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S12-009 | fixed | - S12-009 host.py:644-655：TRUST_BLOCKED 拒绝路径每次 activate 都往 record.diagnostics 追加一条，重复调用会无界增长。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S12-010 | already_fixed | - S12-010 host.py:1915-1925：refresh_revocations 重载的 TrustStore 没有写回 self._policy.trust_store，与 discover 的语义不一致。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S12-011 | fixed | - S12-011 metrics.py:27/64：disabled 激活结果和 revocation 隔离原因从不被记录，发现期的签名/信任隔离也不计数，计量词表与实际接线不符。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S12-012 | fixed | - S12-012 loader.py:124 / manifest.py：namespace 允许下划线，a_b+c 与 a+b_c 的模块名和工具投影名会碰撞，purge_modules 也可能误清他包模块。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S12-013 | fixed | - S12-013 pack_catalog.py:34：certified_only 视图始终用 evidence 模式，不验 HMAC，strict 策略下仍会把未签名报告列为 valid。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S12-014 | fixed | - S12-014 permissions.py:53-55：注释说高危权限“即使被授权也要求显式二次确认”，但 host 只发 warning，授权照常生效。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S12-015 | fixed | - S12-015 marketplace/models.py:42：VersionRecord.version 无 semver 校验，versions 字典的键与 record.version 是否一致也不校验，与注释“解析失败加载期即拒绝”不符；latest_version 静默过滤这类记录。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S12-016 | fixed | - S12-016 sbom.py:139-145：只要文件里有一个非 UTF-8 字节就跳过 secret 扫描，可被轻易绕过；path.is_file() 会跟随文件符号链接（发布路径的解包大概率已拒绝 symlink）。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S12-017 | fixed | - S12-017 sdk/algorithm.py:200-225、sdk/model.py:182：run_authoring_checks 没有真正做“规模守卫”，整数期望值不走容差；aggregate_stream_events 收集事件无上界；model.py 注释“worker 不可用 streaming”已过时（V3 已支持）。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S13-L1 | fixed | - S13-L1 trust_store.py:218-288 revoke_package 是读-改-写，没有文件锁，两个进程并发吊销仍可能丢一条（CR-2 只缩小了窗口）。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S13-L2 | fixed | - S13-L2 context.py:29-46 无 id 的 profile 缓存键 = 前 64 字符 + 16 位 hash，存在碰撞的可能；profile 更新后旧实例永不回收。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S13-L3 | fixed | - S13-L3 context.py:594-604 load_sibling 先把模块放进 sys.modules 再 exec，exec 失败后半初始化的模块会留在缓存里。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S13-L4 | fixed | - S13-L4 client.py:529-537 流里收到无关帧或 broker 帧也会重置 idle deadline，恶意 worker 可以发垃圾帧让流永不超时（max_events 不计这些帧）。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S13-L5 | fixed | - S13-L5 capability_policy.py:116-120 fail_closed 时只拒绝 "unknown"，空串和 "missing" 都放行，和 docstring “扩展面 unknown 也拒绝 / 默认最小权限”的说法不完全一致。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S13-L6 | fixed | - S13-L6 artifact_cache.py:42 ARTIFACT_DIR 是相对路径，依赖 cwd；orphan sweep 会删除 data/artifacts 下所有不符合命名、超过 1h 的文件（目录归属的假设比较强）。 |
| #1951 | S13-L7 | fixed | - S13-L7 atlas_layout.py:294-298 page_title 只放在 page 外层，没写进 IR 的 title 组件；场景 id 重复时组件 id 会冲突；共享 chrome 的 frame dict 是浅拷贝，各页共用同一个对象。 |
| #1951 | S13-L8 | fixed | - S13-L8 client.py:403-429 call_stream 的 window 参数本身就是 S13-001 的根源，这个 API 不应该让调用方自报窗口。 See [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md). |
| #1951 | S49-L1 | fixed | - L1 snapshot.py:334 全量 capability 只取字典序前 16，其余静默缺席且无披露。 |
| #1951 | S49-L2 | fixed | - L2 intent_learning.py:107-131 抽取异常时 matched_rules/fallback/candidates 未绑定 → 第二段 NameError 被吞，整行证据丢失。 |
| #1951 | S49-L3 | fixed | - L3 memory_harvest.py:120-127 不同 source 同名字段相互覆盖 profile。 |
| #1951 | S49-L4 | fixed | - L4 cartography_ratchet.py:178-186 负基线值时 (1±f) 方向反转；基线 0 时零容差。 |
| #1951 | S49-L5 | fixed | - L5 cartography_runtime.py:150-154 跨 loop 替换锁时可能替换掉正在持有的锁。 |
| #1951 | S49-L6 | fixed | - L6 cartography_runtime.py:1002-1004 commit 类 attempt 无前端 ACK，每次评估把状态重置为 issued。 |
| #1951 | S49-L7 | fixed | - L7 history_compression.py:27-37 memo 以 hash(content) 为键可碰撞，满 8192 后不再淘汰。 |
| #1951 | S49-L8 | fixed | - L8 history_compression.py:117-122 用户可在消息里写 "Tier 3"/"confirm_destructive" 让任意轮被 pin。 |
| #1951 | S49-L9 | fixed | - L9 cartography_service.py:244 build_legend_spec 的 palette 参数默认值与实际使用色带无关。 |
| #1951 | S50-006 | fixed | S50-006｜Low｜安全/日志｜decision_log.py:854-857｜REDACT_KEYS 含 "Authorization" 但比较用 k.lower() → 永不匹配；仅顶层 key 脱敏，嵌套 dict（headers.token）原样写盘｜NEW |
| #1951 | S50-007 | fixed | S50-007｜Low｜性能/观测｜context_assembler.py:653-664 vs 706｜estimated_tokens 在 CONDENSE/OFFLOAD 执行前计算，返回值与模型实际看到的 head 不一致｜NEW See [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md). |
| #1951 | S50-008 | fixed | S50-008｜Low｜资源｜context_projections.py:596-602｜逐出只 pop _store 不 pop _fps → _fps 无界增长；同 key 覆盖写入时仍会多逐出一个无关条目｜NEW See [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md). |
| #1951 | S50-009 | fixed | S50-009｜Low｜正确性｜context_policy.py:274-282｜condense 起始 max_chars=cap*2 按 CJK 估算，ASCII 文本会被压到预算约 1/2，过度截断；bound_text 可能在 XML 围栏中间截断留下未闭合标签｜NEW See [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md). |
| #1951 | S50-010 | fixed | S50-010｜Low｜正确性｜context_policy.py:679,689｜retrim 对非 str content（None/多模态 list）调用 _estimate_tokens 可能抛错（被 hook 吞掉 → 放弃重试）；folded is rest 恒假（rest 是新 list），首个早退条件是死代码｜NEW See [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md). |
| #1951 | S50-011 | fixed | S50-011｜Low｜一致性｜context_builder.py:289 vs context_policy.py:90｜两套 ref 正则（ref:[\w-]+ vs 限定前缀词表），摘要/墓碑漏识别其他前缀 ref｜NEW See [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md). |
| #1951 | S50-012 | fixed | S50-012｜Low｜正确性｜context_builder.py:312-318｜多模态 content（list）被切片并 f-string 成 list repr 写进系统块｜NEW See [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md). |
| #1951 | S50-013 | fixed | S50-013｜Low｜正确性｜context_builder.py:88,195｜环境时间为 UTC 但未标注时区，LLM 易当作用户本地时间（UTC+8 偏差 8 小时）｜NEW See [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md). |
| #1951 | S50-014 | fixed | S50-014｜Low｜可维护性｜context_assembler.py:270-272｜_build_v6_context_blocks 注解返回 list，实际返回 tuple｜NEW See [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md). |
| #1951 | S50-015 | fixed | S50-015｜Low｜安全（残留）｜event_resume.py:428-433｜匿名 "" key 共享命名空间，Last-Event-ID>0 仅弱所有权（文档已自认 RESIDUAL）；任何知道消息文本的匿名者可回放他人已缓冲事件尾｜NEW（已知设计残留） See [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md). |
| #1954 | S101-009 | not_a_bug | S101-009 s3_blob_store.py:285-289 _is_absent 靠字符串匹配判断；对象缺失但没有 ListBucket 权限时返回 403，会被误判为 S3StoreUnavailable。 |
| #1954 | S101-010 | fixed | S101-010 s3_blob_store.py:438-443 put_blob_from_path 的去重命中只比对 sidecar 和长度，不像 put_blob 那样整读校验（遇到同长度覆写会误命中）。 |
| #1954 | S101-011 | fixed | S101-011 s3_blob_store.py:337-341 get_blob 的 json 副本 digest 不符时直接返回 None，不再尝试 binary 副本。 |
| #1954 | S101-012 | fixed | S101-012 s3_blob_store.py:306-316 head 检查大小与 get 之间有 TOCTOU 窗口，大小上限可被绕过。 |
| #1954 | S101-013 | fixed | S101-013 session_data.py:404 淘汰时的 spill 磁盘 I/O 在持有 _lock 期间 await，会阻塞该进程所有 ref 的 store/get。 |
| #1954 | S101-014 | fixed | S101-014 session_data.py:880-884 get_ref_descriptor 返回内部 dict 并直接改写它，存在引用逃逸，调用方修改会污染缓存。 |
| #1954 | S101-015 | fixed | S101-015 session_data.py:514-519 set_alias 不加锁，也不校验 ref 是否存在，alias 可以指向已逐出的 ref。 |
| #1954 | S101-016 | fixed | S101-016 session_data.py:638-644 get_state_field 只做一层浅拷贝，嵌套对象仍会逃逸（#701 同类）。 |
| #1954 | S101-017 | fixed | S101-017 session_data_redis.py:1666-1676 clear_session 漏删 ref_digests key。 |
| #1954 | S101-018 | fixed | S101-018 session_data_redis.py:1914-1921 ref_exists 的 zadd 没有 WATCH 守卫，重新引入了 CONC-F6 的悬挂 refs_order 成员问题。 |
| #1954 | S101-019 | fixed | S101-019 session_data_redis.py:1171-1183 set_map_spec_fingerprint 不失效 L1 缓存，也不续期。 |
| #1954 | S101-020 | fixed | S101-020 session_data_redis.py:1119, 1347, 1539 get_map_state、get_event_log、get_map_action_events 的 json.loads 没有防护，脏数据抛出的非 RedisError 异常会逃逸。 |
| #1954 | S101-021 | fixed | S101-021 session_data_redis.py:1716-1745 cleanup_idle_sessions 没有隔离 RedisError；既没有 activity 也没有 refs 的 sid 永远判不成 idle，sessions:active 集合会无界增长。 |
| #1954 | S101-022 | not_a_bug | S101-022 session_data_redis.py:1486-1497 以及各处多 key 事务：key 没有 hash tag，部署到 Redis Cluster 时会报 CROSSSLOT。 |
| #1954 | S101-023 | fixed | S101-023 session_data_redis.py:910 退避抖动用 id(_attempt)%100（小整数的 id 是固定的），实际起不到抖动作用。 |
| #1954 | S101-024 | fixed | S101-024 session_plan.py:1351 _register_plan_artifacts 忽略传入的 store，直接用全局 session_data_manager（注入 store 的测试和调用方会不一致）。 |
| #1954 | S102-L1 | fixed | - S102-L1 baseline_resolver.py:131/137/239：isinstance(v,(int,float)) 把 bool 当数值，True/False 被平均成 0~1 的“指标”。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S102-L2 | fixed | - S102-L2 baseline_resolver.py:243-247：对所有数值属性一律取算术平均，人口、面积等可加量被平均而非求和/面积加权，基线失真（docstring 未说明）。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S102-L3 | fixed | - S102-L3 comparison_engine.py:66-76/154-187：scenario_id 重复时 metric_matrix/affected_area/scores 静默覆盖，Pareto 自比较被 a_id==b_id 跳过。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S102-L4 | fixed | - S102-L4 comparison_engine.py:241-255：综合得分直接把不同量纲指标的 delta_pct 相加，无权重/归一化说明。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S102-L5 | fixed | - S102-L5 spatial_analyzer.py:440-462：spatial_join 的 join_type/predicate 未白名单，非法值抛 geopandas 原始异常而非结构化错误。 |
| #1954 | S102-L6 | already_fixed | - S102-L6 spatial_analyzer.py:522-524：zonal_statistics 结果按序号回填，若下游丢弃空几何要素会错位（未验证 zonal_statistics 是否保序）。 |
| #1954 | S102-L7 | fixed | - S102-L7 singleflight.py:44-86：version 参数只是原样回传，并未提供“构建期间失效”检测，docstring 易误导；异常对象跨线程共享 raise，traceback 会被叠加。 |
| #1954 | S103-L02 | fixed | - S103-L02 constraints.py:56-58：== 通过时 margin 仍 ≤0，与“>0 表示安全”的语义冲突。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S103-L03 | fixed | - S103-L03 constraints.py:116-122：属性值不能转 float（如 "N/A"）时 float() 抛错，整个决策失败。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S103-L05 | fixed | - S103-L05 decision_engine_v3.py:443-462：dict(f) 是浅拷贝，往 properties 写字段会改到 problem.reference_geometry 的原对象；properties 为 None 时 setdefault 不生效，随后报 TypeError。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S103-L07 | fixed | - S103-L07 evidence_hardening.py:40：valid_until 用字符串比较日期，格式不统一时会误判；119：以 min 为分母，负值时比例失真。整个模块在 app 里没有调用方（死代码）。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S103-L08 | fixed | - S103-L08 impact_engine.py:45：FeatureCollection 中有 MultiPolygon 时，MultiPolygon(geoms) 在 shapely 2 下会报错；不全是面时只取第一个几何，其余静默丢弃。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S103-L09 | fixed | - S103-L09 impact_registry.py:191-205：医院模型的假设写“1500–3000m”，实际生成的影响区仍是 500/1500m，假设与几何不一致。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S103-L10 | fixed | - S103-L10 impact_registry.py:47-51：基线为负值时 min/max 顺序颠倒。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S103-L11 | fixed | - S103-L11 mapspec_integration.py:279-305：约束图层 upsert 的返回值被丢弃，失败时没有任何提示；V2 用 stops、V3 用 cases 两种 match 结构并存。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S103-L12 | fixed | - S103-L12 rule_pack.py:478-485：RAG 检索不传 tenant，按 engine 的 fail-closed 逻辑只能检索到公共文档，用户自己的知识库永远用不上（不泄露，属功能缺失）。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S103-L13 | fixed | - S103-L13 recommendation_policy.py:205：“满足全部 N 条硬约束”中的 N 用的是所有约束数（含软约束）。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S103-L14 | fixed | - S103-L14 slices/*.py：演示数据标成 BaselineTruthState.OBSERVED，source_refs 是虚构的 ref；生产代码没有引用，仅用于测试或演示，但标注不诚实。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S104-011 | fixed | - S104-011 target_resolver.py:68,247 owner_token 接收但未传入 store.get（Redis/PG 后端带 owner 校验时 session ref 恒失败或绕过）。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S104-012 | fixed | - S104-012 target_resolver.py:233 LineString/MultiLineString/GeometryCollection 被标为 geometry_type="Polygon"。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S104-013 | fixed | - S104-013 target_resolver.py:144 跨日界线 bbox（w>e）被拒，落到地理编码。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S104-014 | fixed | - S104-014 uncertainty.py:68-73 EMPIRICAL 无 values 时返回全 0 样本（伪造统计，违背 GIS-108 原则）；FIXED 未 float() 转换。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S104-015 | already_fixed | - S104-015 adapters.py:327-331 diff_summary 键可覆盖 version_no/data_changed（spread 在后），可误触/漏触失效桥。 |
| #1954 | S104-016 | already_fixed | - S104-016 adapters.py:210-227 异步 notify_job_finished 不走 org 解析器，仅有 session 的作业事件静默丢弃；session_id>64 字符时信封校验失败静默丢弃。 |
| #1954 | S104-017 | fixed | - S104-017 service.py:127-132,94-96 ingest_sync 在 to_thread 线程里调用 asyncio.Event.set()，非线程安全（应 loop.call_soon_threadsafe）。 |
| #1954 | S104-018 | fixed | - S104-018 service.py:177,205 _watch_cache 为实例属性，多个 drain 并发（replay 与 worker）互相清空缓存。 |
| #1954 | S104-019 | fixed | - S104-019 ledger.py:621-652 CAS 以 updated_at 等值为版本号，DB 精度到秒（MySQL DATETIME）时同秒写入无法检测冲突；建议整型 revision。 |
| #1954 | S104-020 | already_fixed | - S104-020 portfolio.py:520-533 fires 子查询 LIMIT 200 无 ORDER BY，项目视图随机漏 fire；MySQL 不支持 IN 子查询含 LIMIT。 |
| #1954 | S104-021 | fixed | - S104-021 watch.py:177-183 乱序（更早）事件时间窗过滤用 (now-t)<=window，负差恒满足，窗口计数被放大。 |
| #1954 | S105-L1 | fixed | - L1 spatial_quality_service.py:320-369：shapely 会自动闭合环，<4 点的环在 shape() 阶段就已抛错，RING_CHECK_FAILED 两个分支基本是死代码。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S105-L2 | fixed | - L2 spatial_quality_service.py:372-377：MultiPolygon 的 iso_ratio 随部件数线性增长，100 个正方形组成的 MultiPolygon 会被误报为 SLIVER。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S105-L3 | fixed | - L3 spatial_quality_service.py:528-645：命中 MAX_TOPOLOGY_ISSUES 后外层 break，后续要素的线端点没有收集，这些端点会被误报 DANGLING_ENDPOINT（追链推断）。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S105-L4 | fixed | - L4 spatial_quality_service.py:429-440 / pipeline:628-641：用质心判定 Null Island，以 (0,0) 为中心的合法大面会被误报，开启 drop_zero_coordinates 时还会被删除。 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S105-L5 | fixed | - L5 spatial_meta_profiler.py:282-288（profile_geojson_source 的字符串入参）：超长 JSON 字符串先走 Path.is_file()，会抛 OSError ENAMETOOLONG（已复现）；同时 str 入参会被当作本地路径读取。当前唯一调用方只传 dict，所以不可达。 |
| #1954 | S105-L6 | fixed | - L6 spatial_operator.py:411-417：吞掉所有 Exception，包括 OperationCancelled 和 SoftTimeLimitExceeded，取消和超时都被转成普通 failed 结果。 |
| #1954 | S105-L7 | fixed | - L7 storymap/story_compiler.py:20-22：ORDER BY created_at ASC LIMIT 200 取的是最早 200 条，长会话会丢掉最新的叙事。 |
| #1954 | S105-L8 | fixed | - L8 storymap/assets.py:44-50 + 60-61：更新路径是 read-modify-write，并发保存会写出相同的 revision；IntegrityError 时 db.rollback() 会把调用方同事务里的其他改动一起回滚。 |
| #1954 | S106-001 | fixed | ### S106-001｜Low｜并发/资源｜subagent.py:408-411；app/lib/cancellation.py:130-132 |
| #1954 | S106-002 | fixed | ### S106-002｜Low｜可观测性｜subagent.py:430-435 |
| #1954 | S106-003 | fixed | ### S106-003｜Low｜正确性｜temporal/aggregation.py:244-247 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |
| #1954 | S106-004 | fixed | ### S106-004｜Low｜契约｜temporal/models.py:24-54；aggregation.py:168-169 See [2026-11-05-data-issues.md](2026-11-05-data-issues.md). |

## Review notes

- #1816 S3d-003 remains unresolved: policy/usage/rate failures now reject writes with 503; atomic concurrency/storage reservations still require lifecycle integration. No claim of complete quota repair.
- #1954 S101-009: an access-denied S3 response is kept as an availability/authorization error. Without object-list authority, a permission failure cannot establish that the object is absent; treating every 403 as missing would hide broken credentials.
- #1945 S101-008: stream upload digest is verified before multipart completion and uses conditional completion; stream reads verify a disk-spooled payload before yielding bytes. API contract verified against [AWS CompleteMultipartUpload](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CompleteMultipartUpload.html).
- Migration `0100_spatial_watch_action_revision`: full SQLite `alembic upgrade head` passed (`/tmp/webgis-core-migration0100.log`). Downgrade deliberately relies on the old uniqueness constraint to reject ambiguous multi-action audit rows, instead of deleting audit data.
- New verification: 400 passing tests in storage/S3/review/spatial-event/data-quality batch; one outdated project-knowledge live-org fixture corrected and independently passed. Subsequent task/workflow/MVT/subagent/lock/skill batch: **207 passed** (`/tmp/webgis-core-tests25.log`).
- #1861 has been delegated to the frontend specialist; its authoritative per-finding results live in `2026-11-05-core-low-1861.md` and are merged by the root agent.

## Continuation evidence

See [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) for new per-finding dispositions, test evidence, and explicit remaining gaps. Aggregate counts await cross-worker reconciliation.
