## G13-CAPABILITY-EXECUTION · 能力生态生产装配与可执行算法闭环

基线：`origin/master = 816705218a5aa6202bf15d745cb31cddb3437f44`（2026-11-10）。
分支：`zcode/goal-g13-capability-execution-20261110T133500Z`。本 PR **不自动合并**，等待 review。

---

### 一、解决什么问题

G06（#2016）交付了跨层可用性快照、三态科学预检、可用性重排与两项真实原语，
但它们是**离线可用、生产未接**：`app/` 内零调用点、无缓存（全量 8 面审计
~秒级每次重跑）、两原语因无算法绑定永远 `available_unverified`、输出的
`artifact://` 合成 URI 无任何存储/读取方（写-only 的翻页承诺）。

本 PR 把这条链接入真实生产路径：

| 接线 | 位置 | 用户可见结果 |
|---|---|---|
| 同代快照缓存（WP03） | `app/lib/gis/capability_availability_cache.py`（新） | 热路径零全量审计（5.3s→~20ms）；单飞重建；失败退避不风暴、不服务跨代旧数据；关闭清理 |
| 候选检索可用性投影（WP04） | `tool_surface_v3.select()` 步骤 6.8 + `pi_native_surface` 消费 | 停用/缺 provider/声明态工具不再因相似度进入激活名单（原因入披露与 SurfaceReceipt）；未完全验证工具降权但**保留可见**；`GIS_AVAILABILITY_RERANK=0` 逐位回退 |
| 执行前资格复核（WP02/05） | `tool_dispatch_service` 1.42b（bind 后） | 选后撤权/停用 → typed `CAPABILITY_UNAVAILABLE`（附同能力替代+重试指引）；同名 schema 换代 → `STALE_TOOL_SCHEMA`；拒绝释放去重占位；复核只认阳性结构事实、失败放行（不建第二权限系统/不做慢探测） |
| 两原语算法绑定（WP06） | `algorithms/capability_ecosystem.py` 两条真实 AlgorithmDescriptor | planner 面 `binding_unbacked`→`algorithm_backed`，八面 pass → **native_verified（真实对账，非手改）**；capability_tool_map 可解析；刻意不声明 fallback（均值≠分位数等价物，C12） |
| 真实产物与复用（WP07） | 工具封套 v2 + dispatch seam | `ref:analysis-*` 真落 session store + ArtifactRegistry（`::analysis` 后缀键）；LLM 拿 ref + 有界摘要（完整逐 zone 表不进上下文）；下一轮把 ref 传回参数即生产解引用；伪造/跨会话 ref 诚实拒绝 |
| 使用边界（WP08） | `MAX_SCHEMES=8` typed 上界；不可达设施计数披露；经纬度启发式披露 | 无界 O(k²) 拒绝；无覆盖≠零差异；度≠米只披露不拒绝 |
| 发现面披露（WP10） | `list_available_tools` 携带 availability 状态与理由 | 与抑制面/拒绝面同一词表；普通用户能据此走下一步 |

### 二、旗舰旅程状态

- **区县 NDVI p90**：真实 Pi turn（`compute_turn_active_tools`）名单可含该原语；
  真实 dispatch 产物 p90 与 numpy 手算对拍；有效样本分母/小样本分区/条件
  全量入 science 披露（C01/C03 有测试）。
- **15 分钟路网覆盖**：真实边时间（travel_time_s 按段分摊/speed_mps）才进
  time 模式；缺时间成本 → typed `TIME_COST_NOT_EVALUATED`；秒语境下米制
  工具在检索层即被 `UNIT_DIMENSION_CONFLICT` 抑制（C04/C05 语义，G06 实现保留）。

### 三、兼容与边界

- 复用既有 ToolRegistry/Capability/Algorithm/Contract/ExecutionCatalog——
  无第二注册表/调度器/权限系统/Planner。
- G12 所有权文件零改写（vendor/bridge/`pi_rpc_client`）；`pi_native_surface`
  仅 additive 消费。
- 计数断言 253→255（+2 真实描述符，任务书允许的精确计数更新）；
  `binding_unbacked` ratchet 在 master 上已红（66>64），本线回到 64。
- capability-artifact v1→v2（additive；v1 零消费者）。
- 三个 kill switch（缓存/检索投影/执行复核），默认全开，可独立回退。
- service_area 能力 +stats_table 输出；网络原语 `cutoff` 必填
  （静默 2.0 默认可把「15 分钟」算成 2 米——伪成功，对齐参数契约）。

### 四、本地验证（真实执行，不等待线上 CI）

- `tests/unit/gis/test_g13_capability_execution.py` **26 passed**
  （C01/C03/C05–C08/C12/C13/C15/C16 + 故障反验：kill-switch 证明拒绝/抑制
  仅来自新接点 + 真实 Pi-turn 名单变化 + 缓存风暴/跨代回归）。
- 相关回归全绿：G06 oracles 19p、primitives 9p、g02 oracles 22p、
  conformance 套件（含 ratchet）、retrieval v4 23p、Pi surface 套件、
  dispatch/formatter/artifact 套件。
- `scripts/verify_capability_ecosystem.py` **6/6**；
  `python -m app.lib.gis.execution_catalog_docs --check` up to date；
  生成物仅经官方生成器更新；ruff/mypy 干净。
- 性能证据：同代命中全量 compiler 调用=0；10 并发冷请求构建=1；
  复用判定确定性；内存与配置上限相关（详见 planning/…/TEST_MATRIX.md）。

### 五、独立评审

独立 reviewer（未参与实现）冷读全量 diff + 实机并发探针 + 真实八面编译：
R1 **APPROVE-WITH-FIXES**（P0=0/P1=0，P2×2，P3×8）→ 全部 P2 与 7/8 P3
已修复（commit `5b4bb7884`）→ R2 复审结论见
`planning/G13-CAPABILITY-EXECUTION/REVIEW.md`（复审追加记录）。

### 六、未测限制（如实）

- 浏览器/Playwright 面与真实外部 provider 未测（本地无外部服务；
  缺事实=not_evaluated 披露，不伪造健康）。
- Pi 扩展侧 setActiveTools（vendor JS）不在本地测试面；Python 侧以
  真实 turn marker 内容断言。
- 全仓 3 万级 pytest 未整跑（资源纪律；按影响面选择，明细见 TEST_MATRIX）。

### 七、迁移/回退

无数据迁移；revert 本分支即回退。三个 kill switch 支持运行时逐面关闭。
