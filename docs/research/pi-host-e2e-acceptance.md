# Pi-as-agent-host 真机 E2E 验收记录(2026-08-28)

Merge gate for `feat/pi-as-agent-host` (grilling 共识 R1-Q1(b)):live LLM 驱动,
无脚本回放。环境:本机 dev server(uvicorn --reload,含 hardening 修复)、
vendored Pi(`--no-session --no-builtin-tools`)、本地高德 POI GPKG + ChinaAdminDivisonSHP、
Redis SessionStore。SSE 原始留档:`/tmp/e2e_turn{1,2,3}.sse`。

## 检查单结果

| # | 项 | 结果 | 证据 |
|---|----|------|------|
| 1 | 「成都市小学分布情况」完整链 | ✅ | 工具序列 `webgis_map_intent → get_local_admin_boundary → query_local_poi → list_available_tools → webgis_execute(热力图) → webgis_map_product → webgis_cartography_status`(空参),MapSpec 落 3 层 |
| 2 | 边界 + 小学点上图 | ✅ | boundary FeatureCollection(1)+ POI 200 点(step eligibility 行 `feature_count=200`) |
| 3 | 热力图 + 成品组装 | ✅ | `webgis_execute` 内 heatmap;`webgis_map_product` 组装(recipe `poi_distribution_overview`) |
| 4 | status 空参、无幻觉参数 | ✅ | turn1 末次调用参数为 `{}`;无 correction reject |
| 5 | 无 shell/编码残留 | ✅ | 三轮事件里仅 GIS 工具,无 bash |
| 6 | 「换配色」不重启分析 | ✅ | turn2 仅 1 个工具调用(`webgis_map_product`),信封保留、无 supersede |
| 7 | 「分析北京学校」supersede | ✅ | SSE `session_plan_superseded`;Redis 归档成都信封 `superseded=true`,北京新信封 `superseded=false` |
| 8 | 下一轮知道当前任务 | ✅ | turn2 直接改样式、未重述任务(间接证明 `[SessionPlan]` projection 生效) |
| 9 | verdict 拉取行为同旧 | ✅ | 空参 status 正常返回 |
| 10 | 刷新后 refs/层仍在 | ✅ | Redis `session:<sid>:data:ref:*` 键在多轮后仍存在;层由 ref 挂载 |
| 11 | runtime 标识 | ✅ | `/api/v1/health` 与 `task_start.agent_runtime` 均为 `pi` |
| 12 | 无 `plan_*` 事件 | ✅ | 三轮事件清单无 `plan_ready/plan_step_done/plan_finalized` |

## 备注(非阻塞)

- **本地数据集无「北京市」**:`get_local_admin_boundary` 报 `未找到名为 '北京市' 的行政区(level=city)`、`query_local_poi` 报 `未找到行政区 '北京市'`(本地 POI/SHP 为四川为主抽取)。模型重试 3 次(step_error)后以 0 特征完成组装,supersede 语义仍正确。属数据覆盖问题,与 host 契约无关;北京验证需换在线 provider key 或补充数据。
- turn2 模型选择 `webgis_map_product` 而非 `webgis_component_update` 完成换色——均为"不重启分析"的合法路径,信封未被替换。
- turn3 结束后 map-state 为 0 层:北京 0 特征数据的诚实结果,非宿主缺陷;turn1 成都态有 3 层(MapSpec 证据)。

## 相关

- 共识与修复清单:本分支 grilling 会话(R1/R2 决策记录)。
- 回归:全量 5420 passed;排除项均为本机环境(openpyxl 缺失、alembic 子进程负载抖动),基线 commit 对照证实与本分支无关。
