# G38 INTERFACES — 接线清单

## 只新增的文件（无碰撞命名空间）

- `app/services/data_join_authoring/{__init__,contracts,errors,plan,keys,executor,outputs,service,recovery}.py`
- `app/models/data_join_authoring.py` + `migrations/versions/0107_data_join_authoring.py`
- `app/api/routes/data_join_authoring.py`（prefix `/data-join-authoring`，14 端点）
- `app/tools/data_join_authoring_tools.py`（8 工具）
- `frontend/lib/api/data-join-authoring.ts`、`frontend/lib/hooks/use-data-join.ts`、
  `frontend/components/sidebar/data-join/data-join-panel.tsx`
- `tests/unit/data_join_authoring/*`（7 文件 61 测）、`tests/integration/test_pi_g38_data_join_journey.py`
- `frontend/test/project/data-join-panel.test.tsx`
- `frontend/messages/{zh-CN,en-US}/dataJoin.json`

## 共享文件最小加性接线

| 文件 | 改动 | 兼容约定 |
|---|---|---|
| app/main.py | 挂载 data_join_authoring router（1 行 import + 1 行 include） | 加性 |
| app/tools/__init__.py | `_TOOL_MODULES += ("app.tools.data_join_authoring_tools", ...)` | 1 条登记 |
| app/services/jobs/lifecycle.py | `JobKind.data_join`（String(20) 无 CHECK 纯加性） | 加性 |
| app/models/__init__.py | DataJoinPlan/DataJoinRun 再导出登记 | 加性 |
| app/lib/gis/capabilities/data_access.py | `data_join_authoring` CapabilityDescriptor（启动严格词表闸） | 加性 |
| app/tools/registry.py | skip_keys += webgis_data_join_describe/plan_upsert 的 source/plan（G30 bindings 同款：ref 是身份声明不是数据） | 加性白名单 |
| migrations/.alloc.json | zcode/g38 段位登记 0107 | 领号协议 |
| frontend/app/page.tsx | 动态面板 + 命令 tools.dataJoin + 单例渲染 | 加性 |
| frontend/components/sidebar/data-sources-tab.tsx | 工作台入口按钮（Link2 图标） | 加性 |
| frontend/lib/i18n/messages.ts + commands.json(zh/en) | dataJoin ns 登记 + 命令标题 | 加性 |
| docs/quality/**、docs/science/**、docs/api-docs.md、tests/quality/snapshots/openapi.json、realtime-contract.json | 按仓库生成流程再生成（gen_* + API_SNAPSHOT_UPDATE + REALTIME_SNAPSHOT_UPDATE + staleness --update） | 生成物，非手改 |

## 消费的既有稳定接口（未改签名）

G18：`resolve_source`/`SourceSnapshot`/`_snapshot_from_payload`（受信 session 回退）、`DeliveryActor`、交付 REST（旅程消费）；G23：`submit_async_job`/`run_async_job`/`AsyncJobHandle`（checkpoint/ensure_not_cancelled/progress）；`DurableJobStore.request_cancel_sync`；G14 `identity.wfid_for_created`；`feature_edit` FEATURE_ID_KEYS 语义；`session_data_manager.store/get/get_ref_descriptor`；`publish_data_object`（CAS DataObject）；`register_artifact`；`require_owned_session`/`verify_session_owner`；ToolRegistry `@tool`；`useHudStore/useToastStore/useT/apiFetch/useDialogFocus`。

## 与兄弟任务分区

不改：federation/geocompute 内核、MapSpec schema/引擎、G18 交付本体、G24 句柄、G33 专题、G32/G31 工作台、vendor/pi（本机经共享 clone 检出 pin b4afdddf 仅供旅程验证，不提交改动）。上图中 MapSpec 零改动（结果上图走前端 mapspec user mutation 与渲染 ref，不新增事实源）。

## 已知边界（诚实披露）

1. **渲染投影上限**：输出 >10k 行（G24 材料化闸同口径）禁用「加入地图」（typed render_ref_over_budget），完整数据经结果分页/导出交付；>10k 的 G18 直连导出需先经渲染 ref 或未来物化通道（本轮旅程验证 ≤10k 路径）。
2. **工具平面匿名回退**：Pi 工具经注入 session 的受信回退解析 session_ref/table_rows（跨会话仍拒绝）；project_artifact/fabric_parquet 需登录态。
3. **进程内 runner**：与 G18/G23 同款 asyncio 进程内执行（生产 Celery worker 不承载本域 job）；执行体已入线程池不阻塞事件循环。
