# 06 — Backend Findings（索引）

详证：agents/A/findings.md（harness/tool runtime：A-1..A-8，P2×1 P3×7，核心链路复核为高质量）、
agents/B/findings.md（GIS/Model/Data：B-1..B-19，P1×3 P2×7 P3×9）、
agents/D/findings.md（platform/security：D-1..D-15，P1×2 P2×4 P3×9）。

后端重点（P1/P2）：B-1 modelops 工具 import 崩（modelops-v3 分支已修）、B-2 reuse
驱逐失效、B-3 并发 accumulator 竞争、A-1 TOOL_TIMEOUT 误分类致 plan 永久拒绝恢复、
B-4..B-7 stitching/engine 正确性与资源、B-9 driver 阻塞、D-3/D-4 安全与阻塞残留、
B-10 Model 能力实体缺失（V8 核心输入，详见 agents/B/modelops-architecture.md）。

已复核为干净的域（详见各文件）：PiBridge turn lease/abort、tier-3 闸、dispatch
取消/超时、geocompute CAS/调度/预算、data_fabric SSRF/参数化、auth/owner 全端点
扫描、lifespan shutdown、injection/secrets/path-traversal 扫描零命中。
