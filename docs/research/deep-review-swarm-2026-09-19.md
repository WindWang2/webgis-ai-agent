# 深度 Review Swarm 报告（全项目）

**日期：** 2026-09-19
**基线：** `master @ 184d4917`（干净工作树）
**方法：** 9-agent 并行只读审计 swarm（SEC / RUN / GIS / DATA / API / FE / TEST / ARCH / PLT）→ P0/P1 逐条由 4 个独立 verifier 对抗性复核（查可达性、默认开关、调用链；尝试证伪）→ 本文汇总。全程只读，未改动代码、未跑全量测试、未部署。

---

## 1. 结论摘要

| 级别 | 数量 | 说明 |
|---|---|---|
| **P0（复核确认）** | 2 | 任意文件读取（安全）；空间决策硬约束静默通过（正确性） |
| **P1（复核确认）** | ~16 | 跨租户 IDOR/越权、LLM 自确认破坏性操作、会话归因丢失、并发信号量泄漏、NBR/景观指标算错、前端主入口回归、部署链阻断等 |
| **P2/P3（清单）** | 40+ | 多数为静态审计结论，未逐条独立复核（已标注） |

**最优先三项：**
1. **SEC-01**：Data Fabric 本地文件适配器可读服务器任意文件（默认配置下可拖库 `data/webgis.db`，含口令哈希与会话 token）。
2. **GIS-101**：`spatial_decision_v3` 的 5/9 空间谓词与全部 LOGICAL 约束恒为 `passed=True`，不可行方案会被推荐并宣称"满足全部硬约束"。
3. **A8（#1382 回归）**：已登录用户的会话 ref 读取（图层/图表/表格/描述符）全部 403 —— 地图无法恢复 ref 图层，属 fail-closed 功能回归。

---

## 2. P0 详情（复核确认）

### SEC-01 — Data Fabric local_file/cog/geopackage 适配器绕过 allowed-roots（任意文件读取）
- **位置：** `app/services/data_fabric/adapters/local_file_adapter.py:48`、`cog_adapter.py:44`、`geopackage_adapter.py:54-62`（目录 glob 分支完全跳过安全校验）；对照 `flatgeobuf_adapter.py:146-150` 正确传参。
- **机制：** `security.py:545-553` 仅在 `allowed_roots` 非空时做根校验；`POST /api/v1/data-fabric/sources` 只要求 `endpoint_url` 字段存在（`schemas/data_fabric_schema.py:192-194`），`manager.py:189-192` 对空串跳过 SSRF 校验。任意已认证账号以 `{"source_type":"local_file","endpoint_url":"","options":{"base_dir":"./data/webgis.db"}}` 注册后即可查询该 SQLite（`users.password_hash`、`conversations.owner_token` 等）；LLM 工具 `connect_data_source` 同路径可达（`connection_manager.py:238-249`）。
- **复核修正：** 复核确认；Windows 下 `SENSITIVE_SYSTEM_DIRS`（POSIX 路径）实际失效，`~/.ssh` 仍拦截。需已认证（注册默认关闭）。
- **修复方向：** 三个适配器统一传 `_local_file_roots_from_settings()` + `_local_file_max_bytes_from_settings()`；目录 glob 分支同样过校验；路由/工具层对 local 类型禁止空 `endpoint_url`。

### GIS-101 — 空间决策 V3 硬约束静默通过
- **位置：** `app/services/spatial_decision/spatial_constraints.py:308-317`（默认分支 `passed=True`）、`constraints.py:141-151`（LOGICAL 恒满足）；`app/tools/spatial_decision_tools.py:415-433`（硬约束为默认类型，9 种谓词字符串映射进来）。
- **机制：** 仅实现 `OUTSIDE/WITHIN/MIN_DISTANCE/MAX_DISTANCE`；`INTERSECTS/DISJOINT/BUFFER_EXCLUSION/SERVICE_COVERAGE/OVERLAP_RATIO` 落入默认分支 → `passed=True` → `decision_engine_v3.py:83-91` feasibility 通过 → `recommendation_policy.py` 可选中违约束方案，报告还会写"满足全部硬约束"。
- **复核：** VERIFIED，端到端确认（谓词枚举 `models_v3.py:73-83`、工具注册 `spatial_decision_tools.py:302`），无用例覆盖；未知谓词回退 `OUTSIDE` 反而是安全的，危险的是 5 个"已知但未实现"的名字。
- **修复方向：** 默认分支改为 raise/`UnsupportedMethod`（fail-closed），或补齐谓词实现；LOGICAL 无评估器时拒绝；工具入口拒绝未知谓词字符串。

---

## 3. P1 清单（复核确认，含修正）

| ID | 域 | 结论 | 位置 / 要点 | 复核修正 |
|---|---|---|---|---|
| A8 | SEC/回归 | #1382 导致已登录会话 ref 读取 403（fail-closed） | `session_data_protocol.py:322-399` + 唯一 digest 写入点 `chat.py:1091-1100`（仅匿名）；`layer.py:85-88,503-523`、图表/表格 artifact 全中招 | 实证复现；P1 功能回归（登录 mandatory 的场景≈P0 产品影响） |
| SEC-02 | SEC | 模板版本 API 跨租户读/写 IDOR | `template_versions.py:48-189` 无 `_template_visible`；`versioning.py:135-173` 直接覆写任意模板 payload；用户模板 id 为 32bit 可枚举，读接口甚至无鉴权 | VERIFIED P1 |
| SEC-03 | SEC | LLM 可自确认 tier-3 破坏性工具 | `tools/plan_mode.py:154-167` 的 `confirm_destructive` 来自模型参数；`plan_mode.py:767-784,1000-1008` 只认该布尔值 | VERIFIED；`manage_analysis_asset`（删物理 TIFF）实可达；`create_new_skill` RCE 由 `ALLOW_DYNAMIC_SKILLS` 默认关 |
| SEC-04 | SEC | Mission/Cockpit 仅按 org 隔离，且用户归因恒为空 | `mission_runtime.py:31-32` 读 `id/sub`（实际 payload 是 `user_id`）→ 恒 `""`；`store.py:153-155,184-187` 只按 org；`tenancy.py:127-134` 无 org 用户共享 default 桶；`cockpit.py:120-124` viewer 可列全员在途 mission | VERIFIED；状态迁移需 editor+；runtime 默认开 |
| RUN-09 | RUN | `_MultiSlotAcquire` 取消时泄漏波次信号量 | `tool_dispatch_service.py:238-242`；`__aenter__` 抛异常不会走 `__aexit__`；heavy 槽=2，进程级信号量默认 5 | P0→P1（需精确窗口、每次泄漏 1 个；泄漏后工具调度永久挂起） |
| RUN-10 | RUN | Governor 预约/票据在硬取消时泄漏 | `governor/dispatch_adapter.py:124-136` 只 catch `Exception`（`CancelledError` 逃逸）；`cancel_session/close_session` 无生产调用者 | VERIFIED P1；泄漏后 heavy 通道永久饱和（降级而非无限挂起） |
| GIS-102 | GIS | 本地 NBR 用 SWIR1/B11 冒充 SWIR2/B12 | `raster_windowed.py:404` 角色 `("nir","swir1")`，`band_math.py:89-93` 形参 `swir12`，位置传参 `:539`；S2 预设无 swir2 角色 | VERIFIED；显式 `swir_band=11` 或 strict=False 时静默算错；样值真 NBR 0.455 vs 实算 0.185，跨 dNBR 分级 |
| GIS-103 | GIS | 景观指标 CRS 判断为 `"4326" in crs` 子串 + 缺 cos(lat) | `ecology_tools.py:180-184`；`ecology.py:273-274` 按公顷消费 | VERIFIED；EPSG:4490 面积低估约 **1.2e10·cos²(lat)**（原报告 1e7 说小了）；4326@40°N 高估 1.31× |
| GIS-104 | GIS | 红线 bbox 角点检测漏内部穿越 | `redlines.py:67-86`；反例已验证（zone 116.3-116.6E × 39.9-40.2N，bbox 116.0-117.0E × 40.0-40.1N）→ 放行 | VERIFIED；默认未配置红线时 latent（P3），配置后为 guardrail 绕过（P1） |
| API-04 | API | MVT 缓存键不含 fingerprint 且失效路径为死代码 | `data_fabric.py:791-798,805,850`；`invalidate_item` 仅测试调用；同步原地更新 fingerprint | VERIFIED P1（旧瓦片/旧 ETag 存活到进程重启或 LRU 淘汰） |
| FE-04 | FE | 快捷指令把 i18n key 当 prompt 发送（623f786e 回归） | `chat-tab.tsx:60-65,75-84`；`onSend`→`use-sse-stream.ts:1282,1309` 原样发送 | VERIFIED P1；welcome 态即可触发，无测试 |
| FE-05 | FE | 属性表虚拟滚动未接线，只渲染前 ~39 行 | `attribute-table-panel.tsx:164,212-216,244-246` 缺 `ref={virtual.scrollRef}`/`onScroll`；对照 `layers-tab.tsx:1303-1304` 正确用法 | VERIFIED P1；>39 行内联图层滚动空白，现有测试不滚动所以绿 |
| TEST-01 | TEST | contract/e2e/perf-budget 不在发布→部署 DAG | `production.yml:761` release-gate needs 不含；`deploy-prod` 仅依赖 `build`（`:1008-1009`）；分支保护仅 4 项 required checks（gh api 实证） | VERIFIED P1；master push 可在 contract/perf/e2e 红的情况下直接部署 |
| ARCH-11 | ARCH | V5 execution-graph 双宿主拼图，默认部署下完全惰性 | attach 仅 legacy `plan_orchestrator.py:657-660`→`execution_engine.py:1123`；完成事件仅 Pi `agent_pi_bridge.py:604-629`→`session_plan.py:687-694`；`workflow_runtime/service.py:627-632` 无实例即 no-op | VERIFIED；默认 `USE_NEW_AGENT=True`，两向都惰性（REST 手工实例化是唯一出口） |
| PLT-01 | PLT | `METRICS_TOKEN` 使 CI 部署与回滚双双起不来 | `docker-compose.prod.secure.yml:714-717` `${METRICS_TOKEN:?...}`（compose 解析期即失败）；`deploy/ci-generate-env-priv.sh:22-24` 不生成；`.github/**` 零引用 | VERIFIED P1；仅 preview 因 sed 模板而幸存 |
| PLT-02 | PLT | k8s ConfigMap `CORS_ORIGINS` 裸字符串 → pydantic-settings 解析崩溃 | `deploy/k8s/01-configmap.yaml:17`；`config.py:326-329` `List[str]`；api/celery/migration 全部 envFrom | VERIFIED（已复现 SettingsError）；相当于每次 apply 全栈 CrashLoop |
| PLT-03 | PLT | 生产镜像 Node 18 vs Next 16 要求 ≥20.9 | `Dockerfile.prod:63-66,122` 装 bookworm `nodejs`(18.x)；`frontend/package.json:23` next 16.3.4（engines >=20.9） | engines 不匹配 VERIFIED；崩溃未证实（standalone server.js 无版本守卫）；前后端健康检查都不探 3000 端口 |
| PLT-04 | PLT | 旧库 `alembic stamp head` 静默跳过全部 63 个迁移 | `deploy/docker-entrypoint.sh:42-71,81-86`；仅查表存在性；`tests/test_deploy_migration_wiring.py:110-125` 还把行为固化 | VERIFIED P1；legacy 且落后 head 的库是现实存在的人群，之后升级全是 no-op |

---

## 4. 复核中被修正的严重度（重要）

| 原报告 | 原级别 | 复核后 | 原因 |
|---|---|---|---|
| DATA-01 federated catalog 泄漏 | P0 | **P1** | 仅元数据（名称/字段/范围），无要素数据 |
| API-01/DATA-02 MVT 缓存越权 | P0 | **P1**（+P2 陈旧） | 缓存前已鉴权；仅跳过租户授权，需已知 item_id |
| API-02 非流式匿名会话丢 owner_token | P0 | **P2** | 功能连续性缺陷，无泄密；前端主路径用流式 |
| RUN-09 信号量泄漏 | P0 | **P1** | 触发窗口窄、每次仅泄 1 个；但泄漏后不可恢复 |
| API-03 reports 失败返 200 | P1 | **P2** | 是仓库统一信封的既定契约，且有测试固化（`tests/test_report_api.py:39-42`） |
| RUN-14 durable swarm 镜像后写 | P1 | **P3** | 代码顺序属实，但整条网关无生产调用者（默认关） |
| GIS-104 红线穿越 | P1 | **P1（配置后）/ P3（默认）** | 依赖 `SPATIAL_GUARDRAILS_REDLINES_JSON` 是否配置 |

无一条被完全证伪（0 REFUTED）；另有 2 条 PARTIAL（RUN-14、TEST-04，细节已修正）。

---

## 5. P2/P3 清单（静态审计结论，未逐条独立复核）

**SEC**：SEC-05 `/data-quality/evaluate` 匿名可写任意 project（`data_quality.py:121-159`）；SEC-06 GeoAI 工具绕过 DATA_DIR source-path 门（`geoai_tools.py:86-93,145-149`）；SEC-07 `/storymap/compile|export` 无鉴权（`storymap.py:33-87`）。
**RUN**：RUN-11 SpatialWatch 状态无 CAS 丢事件（`spatial_events/service.py:305-314`）；RUN-12 事件桥同步 SQLAlchemy 阻塞事件循环（`invalidation_bridge.py:158-162`）；RUN-13 会话快照覆盖在途消息（锁对象不一致，`execution_engine.py:814-819`）；RUN-15 mission 预算 RMW 丢计费（`resources.py:84-106`）；RUN-16 recover 早退不释放 lease（`recovery.py:162-176`）。
**GIS**：GIS-105 重投影失败静默用原始坐标且 confidence=1.0；GIS-106 kriging 忽略字符串形式 crs 成员；GIS-107 无不确定参数时用 N(0,0.02) 噪声伪造"概率/后悔值"；GIS-108 全 NaN 样本伪造 `[0.0]` 摘要。
**DATA**：DATA-03 `/data-lifecycle/assess` 全局枚举；DATA-04 lakehouse 适配器引用不存在列恒抛错；DATA-05 sync 瞬态失败标记 unavailable；DATA-06 模型↔迁移 CHECK 约束漂移；DATA-07 `server_default="now()"` 字面量；DATA-08 分页 total 全表物化；DATA-09 gc_plan 默认 kinds 必抛。
**API**：API-05 map-state 204 掩盖写失败；API-06 `/uploads` limit/offset 无界；API-07 gc plans total 全表；API-08 merge 失败返 200；API-09 死 except 分支。
**FE**：FE-06 SessionPlan 水合覆盖流式增量；FE-07 session 绑定中止首个 fetch；FE-08 `mountedRef` 不重置（StrictMode 下轮询全死）；FE-09 i18n 扫描盲区（JSX 表达式内中文漏检）。
**TEST**：TEST-02 perf 预算阈值 10–100× 宽（实测 0.17ms vs target 20ms）；TEST-03 Playwright CI retries=1 与注释矛盾；TEST-05 墙钟断言跑在 `--cov` lane；TEST-06/#1385 修复零测试；TEST-07 #1389 回归测试不编码失败；TEST-09 覆盖元测试恒真断言；TEST-10 gate 工具未 pin；TEST-11 order randomization 从未启用；TEST-12 skip 计为 pass。
**ARCH**：ARCH-12 swarm 子系统生产不可达（15 文件 ~3.7k LOC，默认关、无工具/路由接线）；ARCH-13 mission 无生命周期驱动（created 永不停留）；ARCH-14 legacy 宿主丢弃 SessionPlan SSE；ARCH-15 ADR 引用漂移（0202/0201）；ARCH-16 ToolRegistry 双持有者；ARCH-17 死副本 `index.ts` 仍在且"同步测试"无意义；ARCH-18 no-progress 三处账本语义漂移；ARCH-19 依赖方向倒置（core→services、services 引 tools 私有符号）。
**PLT**：PLT-05 标准 prod 栈监控全盲（prometheus token 未挂载）；PLT-06 README 不含 submodule 初始化 → 默认回退旧引擎；PLT-07 `.env.example` Overpass 域 NXDOMAIN（开箱 OSM 不可用）；PLT-08 基线上 `tests/test_env_template_parity.py` 已红（5 个 Settings 键缺失）；PLT-09 README 数字陈旧（表/revision/ADR/structlog）；PLT-10 pyproject 与 requirements 分叉（starlette 漏洞下界）；PLT-11 lock 按 3.13 编译而镜像跑 3.12。

---

## 6. 覆盖缺口（本轮未做）

- 全部结论为**静态阅读 + 少量定向验证**；未跑全量测试/端到端、未启服务、未做 docker/k8s 实机验证（PLT-01/02/03/04 为语义推演 + 1 个 config 复现）。
- 前端 `node_modules` 缺 vitest/tsc 可执行文件，FE 结论未动态执行。
- 未覆盖：WebSocket/collab、LLM provider/计费层、扩展平台 worker 沙箱内部、geocompute 集群、simulation 数值积分、kriging 变差函数数值、地图导出 4.7k 行 exporter、部分 ADR 一致性抽查。
- P2/P3 清单未逐条复核，实施前应二次确认。

---

## 7. 建议修复顺序

1. **立即（半天级）**：SEC-01 适配器根部校验；A8 回归（按 conversation 是否匿名决定 `session_has_owner`）；GIS-101 fail-closed；FE-04 一行修复；PLT-02 ConfigMap 一行修复；PLT-01 补 `METRICS_TOKEN`。
2. **安全批次**：SEC-02 版本 API 可见性；SEC-03 服务端确认 artifact；SEC-04 用户谓词 + 归因。
3. **正确性批次**：GIS-102（角色名 + 漂移守卫）、GIS-103（`classify_crs` + cos(lat)）、GIS-104（shapely 相交）、API-04（fingerprint 入键 + 失效接线）。
4. **可靠性批次**：RUN-09（`__aenter__` BaseException 回滚）、RUN-10（try/finally + 接线 cancel_session）、RUN-13。
5. **工程效能**：TEST-01（contract/perf-budget 纳入 required/deploy DAG）、TEST-02 阈值、PLT-03/04。
6. **架构决策（需 ADR）**：ARCH-11 宿主中立挂接、ARCH-12 swarm 接线或删除、ARCH-13 mission 生命周期。

---

*审计域划分：SEC 安全与租户隔离 / RUN 运行时与并发 / GIS 空间正确性 / DATA 持久化与迁移 / API 契约与工具层 / FE 前端 / TEST 测试与 CI 门禁 / ARCH 架构缝 / PLT 平台运维。所有原始发现与复核证据（file:line + 代码引用）保留在会话记录中，可追溯。*
