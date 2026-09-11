# 05 — Performance Findings（结构化性能审计汇总）

详证见 agents/{A,B,C,D}/findings.md。本轮未做 wall-clock 基准（master 基线上
环境阻塞：D-1/M-1 SSRF 启动校验 + M-4 fcntl），全部为结构性发现。

## Backend

| ID | 位置 | 问题 | 级别 |
|---|---|---|---|
| D-4 | app/api/routes/project.py:792,933 | 两个 async handler 内联同步 SQLAlchemy（restore/rerun map product），阻塞事件循环（CORE-02 残留） | P2 |
| B-9 | app/services/workflow_runtime/driver.py:148-153 | 孤儿恢复同步 store.transition_node 于事件循环（全文件唯一未 to_thread） | P2 |
| B-7 | app/services/modelops/engine.py:689,725,836,920 | RasterReader.open 作 template 不 close（GDAL 句柄泄漏；Windows 阻塞临时文件清理） | P2 |
| D-15 | app/main.py:491-495 | /layers/data/ 全局限流豁免且无 per-session 预算（带宽/CPU 无界） | P3 |
| B-18 | app/services/rag/faiss_store.py:434 | FAISS 固定 4× 超取，租户过滤下召回不足 | P3 |
| A-2 | app/tools/registry.py:1072-1170 | 别名调用指标错报 THREAD/UNKNOWN；失败降权信号对别名失效 | P3 |
| A-8 | app/agent_pi_bridge.py:1070-1077 | 进度跟踪器 FIFO 淘汰误称 LRU，>64 会话活跃 streak 被清 | P3 |

## Frontend

| ID | 位置 | 问题 | 级别 |
|---|---|---|---|
| C-9 | frontend/lib/mapspec-runtime/runtime.ts:245-300 | 同步 reconcile 缺 ref-only 跳过 → 恢复窗口 lastError 污染、appliedSpec 停滞 | P3 |

（历史 #1080-1082 系列读放大已由前轮修复；本轮 Agent C 复核 reconcile/compile
热路径未见新增结构性读放大。）

## Harness 请求成本画像（§8.3，静态测量）

- 工具面：309 工具注册；runtime manifest 投影 269,047 bytes（JSON）；
  tool_schema 预算 = min(usable×fraction, 12288 tokens)（context_budget.py:170）。
- 每回合 registry 扫描：manifest 指纹缓存（registry 变化才重编译）；
  planner memo 键含 manifest 指纹（planner_runtime.py）。
- 上下文：ContextLayers 九域 + 域字节上限 + 确定性淘汰（V7 合并后）；
  BudgetReport 诚实 window_unknown（#audit-6 修复后）。
- dispatch 缓存：dispatch_result_cache 短命（turn 内）；ToolDispatchService
  去重 + wave gate（每会话并发上限 2）。
- 已知放大点：A-2（别名指标分裂）与 B-10（model 不可检索 → LLM 猜工具名
  重试成本）为间接 token/latency 放大。
