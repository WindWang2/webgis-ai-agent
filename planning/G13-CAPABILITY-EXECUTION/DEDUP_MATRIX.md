# DEDUP_MATRIX · G13 vs 最新 master + 开放 PR

基线 `816705218`。`present`=master 已有；`partial`=有基础需接线；`missing`=本线交付；
`owned-by-other`=他人所有权/在途；`blocked`=等待外部。

| 项 | 判定 | 证据（master 调用链） |
|---|---|---|
| 能力可用性快照编译/查询（G06） | present | `app/lib/gis/capability_availability.py:211,454`；仅 CLI 消费 |
| 快照同代缓存+单飞+失效 | **missing**（ADR-0287 成本段明示生产应缓存） | grep 无 `compile_availability_snapshot` 缓存；`get_execution_catalog` 有单例缓存（`execution_catalog.py:711`）可复用其代指纹 |
| 可用性重排接入真实候选检索 | **missing**（partial：V3 选择器有词法/语义/v4 rerank/v6 hybrid/v7 描述符信号，无可用性信号） | `tool_surface_v3.py:511-705` 无 snapshot 消费 |
| Pi 面 adapter 投影/严格校验 | present（离线可用）/ **missing（生产接线）** | `pi_availability_adapter.py` 无 `app/` 调用点 |
| dispatch 期资格复核（capability bind） | partial | `capability_bind.py:277` bind_tool_capability fail-open、无可用性快照复核、无 schema 代对拍 |
| situation 供给/资格投影 | present | `runtime_situation.py`；`_apply_surface_qualification`（`pi_native_surface.py:834`） |
| 两个原语真实计算 | present | `capability_ecosystem_tools.py:269,531`（含 F-01/02/03 修复） |
| 原语 AlgorithmDescriptor 绑定 | **missing** | `algorithms/capability_ecosystem.py:16` `ALGORITHMS=[]`；planner face warning（`tool_capability_conformance.py:365`） |
| 原语产物真实存储/ref 复用 | **missing** | `_ref()`（`capability_ecosystem_tools.py:81`）合成 URI；grep 零消费者；dispatch seam 铸 ref 分支不命中（`tool_dispatch_service.py:1258-1314`） |
| ref 透明解引用/复用查找 | present | `registry.py:1960`；`analysis_reuse.py:284` |
| 三态科学预检/单位硬门 | present（离线） | `method_eligibility.py:178`；无生产调用 |
| provider 五要素资格 | present（离线） | `provider_eligibility.py:230` |
| 扩展生命周期只读视图 | present | `provider_eligibility.py:63`；`_extension_state_digest`（`capability_availability.py:180`）进快照指纹 |
| SurfaceReceipt/指纹 echo/stale gate | present | `pi_surface_receipt.py`；`pi_bridge_surface_gate.py:71` |
| 字节预算/前门恒保留 | present | `apply_surface_byte_budget`（`pi_native_surface.py:286`） |
| Pi-only 对话（无 ChatEngine 回退） | present | #2036；本线不恢复回退 |
| Pi vendor/RPC/扩展宿主生命周期 | owned-by-other（G12） | 本线只经 `DynamicToolSurface`/adapter/dispatch 接点消费 |
| Pi v1.5.1 pin 升级 | owned-by-other（G12） | `.gitmodules` pin v1.5.0 不动 |
| 多画幅/标注/要素编辑 | owned-by-other（G14–G16） | 不触碰 |
| 地图简报/数据刷新/UI 重设计 | owned-by-other（#2021/#2022/#2032） | 避让 |
| #2071/#2072 扩展 worker 内存/碰撞 | owned-by-other（在途审计专项） | 只消费修复后的公开状态 |
| #2005/#2006 科学回归输入 | present（G06 只读对拍） | 不改其实现 |
| G09 参数化批量/计算编排 | 已安排他线（即使无 PR 也不纳入） | 本线不做批量编排 |

结论：本线无与 master/open PR 重复的实现项；全部工作是把 present-but-unwired 的 G06 面
接进生产热路径并补两个原语的绑定与产物闭环，符合任务书「不重建注册表/调度器/预检引擎」。
