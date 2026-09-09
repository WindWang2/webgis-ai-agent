# 02 — Waves 计划（12 waves）

每个 wave：契约/失败测试先行 → 最小闭环 → targeted tests → lint → progress 记录 → 小步 commit。

| # | wave | 主要产出 | 测试 |
|---|---|---|---|
| 1 | V2 contract | manifest execution/model_providers/dep.version；api_version 1.1.0 门控；新诊断码；config+settings_bridge+HostPolicy | test_manifest 增量 + corpus V2 骨架 |
| 2 | worker protocol+server | protocol.py（帧/版本/上限）、worker/server.py（握手/call/health/shutdown/broker 交织） | 协议单测 + 进程内 loop 测试 |
| 3 | worker client+host 集成 | worker/client.py（spawn/握手超时/call/崩溃检测/kill）、host worker 分支（proxy 投影/对账/quarantine） | 真实子进程生命周期集成测试 |
| 4 | capability broker | broker.py + worker ctx.broker/secrets stub + URL allowlist + artifact 根 + audit ring | 默认 deny/授权放行/SSRF/越权 typed |
| 5 | resource enforcement | spawn rlimit+降级、输出上限、subprocess 拒绝 | 内存超限 typed、输出超限 typed、fake 降级 |
| 6 | signed packages | signing.py + discovery 排除 signature.json + host 信任集成 + CLI package/verify | 签名向量/篡改 quarantine/未签名 dev |
| 7 | SBOM/provenance | sbom.py + CLI inspect --sbom + secrets 扫描 | 确定性/无 secret/上界 |
| 8 | dependency resolver | resolver.py + 约束解析/冲突/确定性序 + upgrade/rollback | 解析矩阵/冲突 typed/升级回滚 |
| 9 | lifecycle V2 | on_projection_change + main.py 接线 + crash→rollback→refresh + in-flight guard | 钩子触发矩阵/stale manifest 消除 |
| 10 | SDK V2 provider + model_provider | provider mixins(streaming/tile/raster window)、ModelProviderSpec、register_model_provider、worker 流式帧、第二示例 pack（worker 模式） | mixin 校验/工具投影/broker model_invoke 流式 |
| 11 | certification + CLI | certification.py、certify 命令、示例包认证绿 | 认证报告确定性/示例包全绿 |
| 12 | docs/ADR 收口 | ADR-0105、security-boundary 文档、limitations 重写、compatibility/架构文档更新、全量验证 | corpus 全绿 + 扩展域回归 |

## Commit 约定

`feat(extensions-v2): wave-N <scope>`；test/docs 独立 commit；禁止混入无关格式化。
