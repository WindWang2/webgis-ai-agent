# SCOPE — GOAL-20261126-01 Pi 原生决策、工具契约与交互运行治理

## 方向

从「可运行的 Pi Host」（前序 #2276/#2280 已交付）推进到「可用、可治理的 GIS
原生 Agent」：产品化 v1.6.1 已具备但未消费/未产品化的能力——有界决策、
provider-aware schema、原生交互阻塞语义、上下文压缩治理、provider 失败
可观察性。**不升级宿主、不动 Pi pin（v1.6.1）、不建第二套任何事实源。**

## 文件所有权（首选边界；实际调查后可校正，校正须回写本文件）

本线独占（新增或主要修改）：

- `app/services/chat/pi_decision.py`（WP2 新增：有界 decision 投影 + 确定性 fallback）
- `app/services/chat/pi_schema_compat.py`（WP3 若需单一来源修复点；否则仅投影边界小修）
- `app/services/chat/pi_interaction.py`（WP4 若需新增 blockedKind 映射；优先改既有文件）
- `tests/` 下本线新增测试（命名 `*_pi_decision_*` / `*_pi_schema_*` / `*_pi_interaction_*`）

修改需谨慎（共享入口，薄接线，逐处记录原因）：

- `app/services/chat/pi_compat.py` — 能力快照词表若需扩充（先证明必要）
- `app/services/chat/pi_native_surface.py` — schema 投影/strict 分类
- `app/services/chat/pi_event_mapper.py` — blockedKind/notices/auto_retry 事件映射
- `app/agent_pi_bridge.py` — decision/澄清/压缩挂点（最小 diff）
- `app/services/tool_surface_v2.py` / `tool_dispatch_gates.py` — 仅接线
- `app/extensions/webgis-tools/index.mjs` — 仅当 blockedKind/classify 需扩展侧配合
- `docs/PI_UPSTREAM_COMPAT_MATRIX.md` / `docs/PI_RUNTIME_CONTRACT.md` — 契约文档同步

禁止触碰：

- `vendor/pi` gitlink（不升 pin、不改动子模组内容）
- 其他四线核心：数据供给（02）、联动探索（03）、图符表达（04）、发布验证（05）
  的核心模块；`frontend/` 大改（本线前端只允许既有事件消费点的必要小接线）
- 主工作树 `webgis-ai-agent`（`zcode/full-code-review-fixes` 未提交改动）
- 兄弟 worktree、其他分支、共享根账本 `.goal-loop-ledger.md`（只读）

## 所有权校正（实际调查后回写，review F6）

原边界将 `app/tools/**` 列为禁止触碰；实现中两处**必要的最小修改**据此
校正记录（均为兼容现有接口的局部增量，未重排任何共享文件）：

1. `app/tools/map_plan_tools.py` — WP2 生产接线点：`webgis_compile_map_plan`
   在 `resolve_intent_adaptive` 后调用 `assist_map_request_intent`
   （feature gate 默认关 = 零行为差），结果 receipt 增加 decision_assist
   披露。该工具是任务识别决策的唯一生产调用方。
2. `app/tools/local_stats.py` — WP3 schema 卫生：`query_local_poi` 补
   typed `QueryLocalPoiArgs`（C20 bare 桶清零）。schema 单一来源本就在
   registry args_model，属本线（工具契约）所有权范畴。

## PR/方向去重结论（2026-11-27 实查）

- 开放 PR 0；#2265–#2282 全部 merged；无他人在做本方向（决策/schema/阻塞
  语义产品化）。前序 Pi host 线（#2276/#2280）不重做。
- 若 WP 执行中新 PR 出现：重查 diff，只避免冲突，不等待、不合并。

## 契约红线（贯穿所有 WP）

1. `agent_settled` 唯一终判；不伪造终态。
2. 唯一 ToolRegistry/ToolDispatchService 执行权威；decision/classify 建议永不
   越过确定性资格闸（数据、权限、计划义务、destructive 禁令）。
3. MapSpec 唯一 desired state；文本任务不得产出 map_product。
4. 无 session 持久化新增；压缩重建走既有 `GIS_STATE_REBUILD` typed assembly。
5. unsupported/unknown/not_evaluated/partial 不自动变 success；诚实披露。
6. 不注入任意 JS；`models.classify()` 只经 vendored codemode 真实 API，
   不经普通 RPC 伪造。
7. 新测试不得全 mock：stub LLM + 真 RPC/真 dispatch 管线为底线。
