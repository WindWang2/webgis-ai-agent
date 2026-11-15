# G37 DEDUP_MATRIX（每项判断追到生产调用链）

| 能力/需求 | 状态 | 依据（生产调用链） |
|---|---|---|
| LLM 基础三元组解析 + 运行时 override + 指纹 | present（复用） | `model_config.resolve_llm_config/config_generation`；config 路由 / spawn 均消费 |
| 模型能力描述符（context/tools/vision/reasoning/cost_class） | present（复用，仅元数据） | `model_runtime/descriptors.py`；**无任何会话选择消费方** |
| admin 配置路由 + SSRF 防护 + 代际换代 | present（保留不动） | `api/routes/config.py:67-131`；`recycle_if_config_stale` 每回合边界 |
| spawn 期单模型 models.json + env set_model | present（本任务扩展为目录投影） | `pi_rpc_client._spawn_process:416-477` |
| turn 边界 new_session 隔离 | present（复用，模型应用插其后） | `agent_pi_bridge._isolate_vendor_session:1576` |
| 单 writer / turn lease / 控制门 | present（不动） | `pi_writer_gate`、`_acquire_turn_lease`、`_control_gate` |
| 会话模型 preference（持久/CAS/继承） | **missing** | 全仓无 conversations 关联模型偏好表/字段；`session_data_manager` 无模型键 |
| 管理员允许目录（多模型同端点）+ 不可用原因 | **missing** | spawn 只写单模型；无 allowlist 投影 |
| turn 期 set_model + get_state 回读 + 失败可见 | **missing** | set_model 仅启动期 env 尽力而为（`pi_rpc_client:362-373`），失败仅日志 |
| 运行回执（requested/effective/responseModel/thinking） | **missing** | 无 receipt 持久层；SSE 无 model 事件 |
| message_end usage 采集 → 持久宿主账本 | **missing** | `pi_event_mapper` 无 message_end handler；全仓 0 消费者（grep 佐证） |
| nested/compaction/session_stats 对账 | **missing** | get_session_stats 全仓 0 调用 |
| 会话预算投影 + 新 turn prompt 前 admission | **missing** | chat 路径无 LLM token admission；governor 仅工具/渲染维 |
| 会话内模型选择/用量 UI | **missing** | `llm-config.tsx` 为 admin 全局只读页 |
| Pi 只读目录/状态/预算工具 | **missing** | NATIVE/注册面无该类工具 |
| G36 image-profile helper | not-merged（缺席兼容） | origin/master 无 image helper 文件；本任务目录生成不依赖它，合流后保留语义（M36） |
| Pi pin 升级 v1.5.2 | present（G22 #2093，不再作为成果） | gitlink = b4afdddf |
| ChatEngine fallback | 已下线（不恢复） | #2036；bridge 不可用如实 503 |
| G09 参数化批量 / G21 OD 流 | owned-by-other（worktree 存在，不占用） | worktree 清单实查 |

## 与 #2101–#2116 修复的叠加关系

- #2101 governor retry pool / turn ledger 有界：本任务新增账本表有独立有界设计（按会话 LRU 清理），不碰 retry_budget。
- #2113 agent P3（budget/lease/prompt-boundary）：复用其修复后的 lease/prompt 边界语义，模型应用插入点不破坏。
- #2107 worker projection / tenant authz：preference 读写在 owner 校验内（require_owned_session 同款）。
