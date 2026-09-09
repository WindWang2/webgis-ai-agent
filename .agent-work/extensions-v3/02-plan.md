# 02 — Plan（Wave 执行序，允许按实现发现重排但不删关键 scope）

## Wave 序（commit 粒度）

1. **W1 基础设施**：`cryptography` 依赖 + config 新键 + HostPolicy 字段 + settings_bridge 解析
2. **W2 非对称签名**：signing.py ed25519（sign/verify/向量）+ keygen CLI
3. **W3 trust store**：trust_store.py（rotation/retired/revocation）+ host 集成 + CLI
4. **W4 registry**：marketplace/{models,store,service}.py + publish/search/deprecate/revoke + CLI
5. **W5 registry API**：只读 FastAPI 路由 + OpenAPI 快照刷新
6. **W6 distribution**：installer（下载/digest/验签/安全解包/staging/原子换装/versions/回滚/中断清扫）+ CLI
7. **W7 隔离后端**：worker/isolation.py bubblewrap + 探测/降级 + status 呈现
8. **W8 协议 V3**：protocol.py 3.0（协商/流帧/信用流控/取消）+ client/server 流式
9. **W9 流式投影**：worker streaming tool + model provider streaming + manifest 门控放开（api 1.2）
10. **W10 worker 算法/数据提供者**：WorkerAlgorithmSpec + WorkerDataProviderAdapter（7 方法 RPC）+ manifest/上下文/对账
11. **W11 cartography/recipe worker 化**：握手 payload 投影
12. **W12 fabric bridge**：extended dispatch helper + query_catalog_item_async additive 分支
13. **W13 lifecycle**：drain + 版本 pin + revoke 传播 + 刷新信号
14. **W14 certification V3**：新增检查 + report
15. **W15 恶意语料**：15 类安全场景测试
16. **W16 性能/文档/示例**：预算基准 + docs 更新 + extdemo-v3-pack 端到端完成证明

每 wave：targeted tests → ruff → progress 更新 → 独立 commit。

## 测试基线

- 扩展域 2388 passed（13.4s）为回归底线；每 wave 后跑扩展域；共享契约（quality gates + OpenAPI）在 W5/W13/W16 与 rebase 后跑。
