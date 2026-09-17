# DECISIONS — 关键设计决策（ADR-0197 草案要点）

## D1 · profile 开关形态：`DEPLOYMENT_PROFILE: str = "cloud" | "air_gapped"`
- 默认 `cloud` = 行为逐字节不变（Oracle：profile 关闭时云模式不变）。
- 不引入布尔堆（`OFFLINE_MODE`+`EGRESS_DENY`+…），单一枚举避免半开状态矛盾。
- `NETWORK_EGRESS_MODE`（`unrestricted|allowlist`）独立成键：air_gapped 下 validator 强制 `allowlist`（显式覆盖报错），cloud 下默认 unrestricted 但也允许运维主动开 allowlist（半离线内网场景）。

## D2 · 允许集语义：deny-by-default + 显式 allowlist + 私网豁免
- air_gapped 默认允许：loopback/RFC1918/ULA/link-local（内网 LLM、内网 PostGIS/MinIO/瓦片服务器正是部署目标）+ `NETWORK_EGRESS_ALLOW` 显式追加主机（如 `tiles.intranet.example`）。
- 公网 host 一律 deny（typed），除非运维显式 allowlist（自担风险，记录在案）。
- 与 `EXTENSION_NETWORK_ALLOW`/`MODELOPS_REMOTE_ALLOWLIST` 语义对齐：host 匹配、default-deny、typed 拒绝。这三层是叠加关系（AND），互不替代。

## D3 · 守卫接缝：三大客户端家族 + 函数级单入口
- 单入口 `app/core/egress.py::assert_egress_allowed(url) -> None`（抛 `AirGappedEgressError`）+ `egress_allowed(url) -> EgressDecision`（探测面）。
- aiohttp：`get_shared_client()/create_client_session()` 挂 `trace_configs.on_request_start`（session 级，覆盖共享池所有调用方）。
- httpx：LLM 池 `acquire()` 建 client 时挂 `event_hooks={"request": ...}`；ad-hoc httpx 调用点统一改走新 helper `egress_guarded_httpx_client()`（vm judge/health/config/modelops/local_admin/local_stats/broker 逐点替换）。
- requests：`SSRFSafeHTTPAdapter.send()` 内先查 egress 再验 SSRF（每 redirect 跳都过守卫，天然覆盖 cursor/redirect 逃逸）。
- pystac-client/`/vsicurl`：不接线（第三方库内部建连）；由 catalog 登记 + preflight 在 air-gapped 下把 STAC/远程栅格报 typed unavailable 兜底；文档声明网络层兜底边界。

## D4 · catalog 是数据不是代码扫描
- `app/core/network_dependency.py` 手工登记（phase 0 勘察产物固化），每条含 `id/category/endpoint(env 或常量)/call_sites/offline_alternative/requires_network`。
- 工具 `network=True` 声明不重复登记，运行时从 registry 交叉引用生成 offline capability matrix。
- JSON 导出即 SBOM 的网络面章节；`manage.py network-catalog --format json` 机器可读。

## D5 · preflight 与 /ready 分离
- `/ready` 语义不动（k8s 探针）；`/status/detailed` 增 `network_policy` 组件（config 探测，无 IO，恒 ok/degraded/down）。
- `manage.py preflight` 是部署 doctor：不通过时 exit code 非 0，供 systemd/compose 启动前调用。

## D6 · 不声称信创实测
- 文档只描述可移植性 contract（跨平台路径/编码/换行/文件锁测试）+ 依赖清单；"未在统信/麒麟等 OS 实测"显式写进文档与 PR。

## D7 · 兼容性/回滚
- 全部新键默认值 = 旧行为；`DEPLOYMENT_PROFILE` 缺省 `cloud`。kill-switch：`NETWORK_EGRESS_MODE=unrestricted` 可一键关守卫（air_gapped 下 validator 会拒绝该组合——回滚 = 把 profile 改回 cloud）。
- 无 DB migration、无 API 破坏性变更；`/status/detailed` 只增字段。
