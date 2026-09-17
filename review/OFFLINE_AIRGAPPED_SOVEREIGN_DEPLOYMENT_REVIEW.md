# Review Memo — 离线/内网/信创部署 profile（offline-airgapped-profile-v1）

- 评审形式：独立 adversarial review（subagent B，2/2 配额；主 agent 独立复核 P0/P1 复现）
- 评审范围：`faa453a8..HEAD` 全量 diff + 三大接缝运行期行为（真实回环服务实证）
- 结论：**FIX-FIRST → 已修复**。1 P0 + 1 P1 + 5 P2 + 数项 P3 全部处置；
  修复后全套离线面 + 相邻回归 **两遍一致**（134 passed, 1 skipped）。

## Findings 与处置

| ID | 级别 | 发现 | 处置 |
|---|---|---|---|
| P0-1 | P0 | aiohttp 接缝守卫只挂 `on_request_start`——302 redirect 跳到公网/元数据端点完全绕过守卫（实证：真实出网发生） | 修复：`on_request_redirect` 对解析后的绝对目标重过守卫；red-test 覆盖。修复过程中初次实现静默失效（`aiohttp.URL` 不存在、URL 在 yarl，被防御性 try/except 吞掉）——由红灯测试暴露后修正为 `yarl.URL` |
| P1-1 | P1 | 元数据拒绝可绕过 ×3：尾点 host、IPv4-mapped IPv6（`::ffff:169.254.169.254`）、`metadata.google.internal/.goog`（落进 `.internal` 私网豁免） | 修复：元数据判定消费规范化 host（尾点折叠 + mapped IPv6 折回内层）+ 扩展主机名黑名单，先于任何私网豁免 |
| P2-1 | P2 | 前端 `getAvailableTileProviders()` 无 UI 消费者（切换器仍列远程底图）；runbook 未披露 | 修复：DEPLOYMENT-offline.md 增诚实边界声明（UI 过滤为 #1353 后续项 + `NEXT_PUBLIC_*` 构建期内联需重构建）；registry/helper 保留为 API 面 |
| P2-2 | P2 | `AirGappedEgressError` 文本内嵌完整 URL（query 中的 api key 泄入日志/evidence） | 修复：str(err) 与 `.url` 属性统一去 query（scheme+path 形态） |
| P2-3 | P2 | catalog 两条 call-site 路径不真实（`chinese_maps/providers/` 不存在） | 修正为 `app/tools/chinese_maps/{amap,tianditu}.py` |
| P2-4 | P2 | requests 接缝注释称"零开销"但每跳 2× urlparse | 修复：mode 快速路径（仅缓存查询+比较），注释对齐 |
| P2-5 | P2 | preflight llm_endpoint 对公网端点误提示"本地服务未启动？" | 修复：按 policy 形态区分提示 |
| P3-a | P3 | 尾点 host 不匹配 allowlist（fail-closed 方向，可接受但反直觉） | 已随 P1-1 规范化修复 |
| P3-b | P3 | `*.suffix` 不含裸域；裸 `"*"` 无放行语义（静默全拒） | `.env.example` 文档化（保守语义不变） |
| P3-c | P3 | 共享 aiohttp session / 池化 httpx client 在运行期改配置后仍按旧策略（无守卫/守卫混合） | 维持重启语义文档（Settings 启动期配置；`current_policy()` 本身按值重建，已实证） |
| P3-d | P3 | `.goal-loop-ledger.md` 与 `.agent-work/` planning 文档入库 | 保留（goal-loop 协议要求账本保留；planning 文档为仓库既有惯例目录） |

## 误报 / 无需处理（已实证排除，不再复议）

- allowlist 通配/后缀绕过（`evil-example.com`、`evil.host.example.attacker.io`）、userinfo 解析、大小写/端口 0/ws-wss、十进制/十六进制 IP 拼写的元数据 IP（fail-closed 拒绝）
- import 环：config ↔ egress 双向均 lazy，`import app.core.egress` 独立可载
- 策略缓存过期：按 settings 值为键，运行期 mutation 即重建；缓存有界
- httpx：event hook 每跳触发、异常裸传播、拒绝后 client 仍可用、caller hooks 保留且守卫先行
- cloud 默认零行为：aiohttp 无 trace、LLM 池无 hook（requests 接缝仅剩一次模式比较——P2-4 已修）
- 并发/取消：TraceConfig 每次新建不跨 session 复用；hook 抛异常不损毁 session
- 向后兼容：/status/detailed 只增字段；`SRE_COMPONENTS` 封闭词表扩张无基数风险；无 migration；kill-switch 真实
- 证据诚实：catalog 的 `enforced_by_egress_guard=false` 五项与实际建连面一致；vendor/pi 子进程缺口已在 ADR/runbook 披露；SBOM 确定性成立

## 与最新 master / open PR 的交叉

- 执行时 `origin/master=faa453a8`；open PR #1335/#1336/#1351–#1356 与本分支 ownership 面零交集（详见 `.agent-work/offline-airgapped-profile-v1/PARALLEL_OWNERSHIP.md`）。
- 需要后续 integration 的方向：前端底图切换器 profile 过滤（建议随 #1353 前端方向落地，消费本分支的 `getAvailableTileProviders()`）。

## 已知边界（不是缺陷，是声明）

1. pystac-client / rasterio /vsicurl / DDGS / HF hub / 浏览器瓦片不经运行时守卫（catalog 登记 + preflight 报告 + 部署网络层兜底）；
2. vendor/pi 子进程 LLM 调用不可从 Python 层拦截；
3. 未在国产 OS 上实测——仓库只交付可移植性 contract 测试与清单，合规认证属运营流程。
