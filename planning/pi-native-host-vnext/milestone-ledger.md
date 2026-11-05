# 里程碑 Ledger

| 轮 | master HEAD | 变更 | 基线/证据 | 回归测试 | 失败项 | 风险 | 下一步 |
|---|---|---|---|---|---|---|---|
| R0 事实核查 | b6ac970eb | 无（只读） | master/PR/issues/submodule/tag 快照；PR #1965 零交集 | — | — | Review-R2 #1916/#1931 相邻 | 双向深挖 |
| R0b 活体验证 | b6ac970eb | 无（/tmp scratch harness） | v1.5.0 干净构建 exit0；握手/扩展/全事件序列/取消/12 例强制转换矩阵实证；旧 pin 构建失败（类型错误）复现 | — | 旧 pin 构建失败（升级论据） | setActiveTools 加载期、opening 串、disposition=handled | M1 |
| R1 规划 | b6ac970eb | planning/ 4 文档 | 兼容矩阵（活体实证）+ PR 重叠矩阵（#1965 零交集） | — | — | — | M1 |
| M1 pin+扩展 | 同上 | vendor/pi→v1.5.0；index.mjs（setActiveTools 延迟 session_start/兜底、preamble 双串）；pi_rpc_client 入口 bundle→dist | 驱动实测：0 加载期错误、默认/每轮投影、persona 替换（新 opening 命中） | test_pi_extension_hardening 15/15（含 4 新增） | — | — | M2 |
| M2 构建 | 同上 | scripts/build-vendor-pi.sh；Dockerfile(.prod) pi-builder 阶段 | 脚本幂等重跑 exit0；bundle 入口活体握手 | Docker 构建 NOT_RUN（本机无 daemon；阶段语法人工核） | — | 镜像体积 +9.2MiB | M3 |
| M3 身份+诊断 | 同上 | pi_compat.py；pi_doctor.py；/health pi_vendor；启动日志 | doctor 全绿（384 工具/v3/活体 RPC 探针） | test_health_api 回归绿 | — | gitlink 未提交前 doctor WARN（预期） | M4 |
| M4 契约车道 | 同上 | tests/integration/test_pi_vendor_contract.py（6 用例） | 真实 v1.5.0 rpc-entry：握手/事件序/唯一终判/abort/U+2028/内建工具不可见/persona+token | 6/6 绿（多次） | — | sandbox 端口过滤→OS 分配端口+自检 skip | M5 |
| M5 golden+防挂 | 同上 | coercion golden（18 例，2 例按实测修正：int 截断/union 首分支）；error_taxonomy+桥 disposition=handled | 活体 18/18 与 golden 一致；handled 流 task_error+done exactly-once | chaos 16/16；golden 2/2 | — | 上游行为变化必须先核实再改 golden | M6 |
| M6 A5 旅程 | 同上 | tests/integration/test_pi_stub_llm_journeys.py（独立子进程驱动；J1/J2/J3/J5；PI_JOURNEY_E2E=1 门控） | J1 map_intent 真实 dispatch success；J2 恰 1 次 LLM 调用；J3 零跨会话；J5 无假 map_product | 3×连续通过；J4 取消由 chaos+契约车道确定性覆盖（驱动内跨线程取消窗口在本 sandbox 不稳，已注明） | — | 旅程车道 opt-in（环境方差），绝不静默绿 | M7 |
| M7 文档 | 同上 | ADR-0282；ledger；部署文档（进行中） | — | — | — | — | R8 |
| R10 验收 | b6ac970eb + 5 commits | ca180d2a3/a04837bd9/67a46ab28/ea54a3b9c/993ce379c | doctor 6/6 PASS（pin 提交后 vendor 域转绿）；Pi 回归 sweep 275 passed；chat/SSE 30/30；health/api 契约通过；ruff 全绿；lint 重命名引发的 3 处 JS 内嵌变量错误已修复并复跑车道 | NOT_RUN：Docker 构建（无 daemon）、远端 CI、PI_REAL_E2E（需凭据） | J4 驱动内取消窗口（sandbox 方差）→ 确定性车道覆盖 | R8 独立 review |
