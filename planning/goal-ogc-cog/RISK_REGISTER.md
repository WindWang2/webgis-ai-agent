# RISK_REGISTER —— GOAL-02

| # | 风险 | 等级 | 缓解 | 状态 |
|---|---|---|---|---|
| R1 | 共享文件冲突（routes/data_fabric.py、mapspec_source.py、tools/__init__.py）被其他方向并发修改 | 中 | 纯追加 diff；每阶段前 fetch；PR 前再比对 | open |
| R2 | rasterio /vsicurl 在测试环境行为不稳（GDAL 版本差异） | 中 | T4 用本地 loopback HTTP + 版本探测；若 GDAL_HTTP 失败记录真实阻断并降级为本地路径同代码路径测试 | open |
| R3 | COVJSON 解析面过大（规范大） | 中 | 封闭子集：domain/grid axes/values/nodata 参数化；不支持形状→typed reason | open |
| R4 | 瓦片代理被滥用为开放代理（SSRF 面） | 高 | 代理只回源到已注册 catalog item 的 descriptor URL；不接受任意 URL 参数；同源校验复用 | open |
| R5 | 压缩炸弹（zip/tile 解压放大） | 高 | bounded_get 解压后逐块帽 + 解压比上限 + 单瓦片帽；fail-closed 测试 | open |
| R6 | MapSpec vector passthrough 分支破坏既有分类测试 | 中 | 只对显式 type=="vector" 生效；跑 mapspec 全量相关回归 | open |
| R7 | openapi/api-docs 快照再生漂移 | 低 | 用仓库既有生成脚本再生（先查 scripts/） | open |
| R8 | 性能测试（字节/峰值）在 CI 波动 | 低 | 用相对断言（窗口读 << 整幅）而非绝对阈值 | open |
| R9 | 前端 vitest/typecheck 环境缺失 | 中 | 先探测 pnpm scripts；失败记录真实阻断 | open |
| R10 | #1868 语义漂移（prepare 输入契约变化） | 低 | P5 用其公开 schema 测试；不 import 私有符号 | open |
