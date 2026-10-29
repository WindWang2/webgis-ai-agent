# RISKS

| # | 风险 | 缓解 | 残余 |
|---|---|---|---|
| R1 | WeasyPrint 进程级互斥：批量 job 与同步导出争槽（429/忙） | 组装单次短临界区；页编译不占锁；attempt 内有界退避（≤3×5s）；仍忙→typed failed 可重试且页缓存保进度 | 高并发部署下吞吐受限（既有架构约束，非本 PR 扩大） |
| R2 | 同步路径接缝提取引入字节漂移 | 只提取不改逻辑；既有 atlas pipeline/IR golden/route 测试锁字节；PR 前 diff 审查 | 低 |
| R3 | committed spec 含 ref: 源水合失败（会话数据过期/清理） | worker 水合 per-source best-effort + 既有 `mapspec_ref_sources_unhydrated` typed 拒绝 → job failed 单行证据 | 用户需重跑数据链（如实披露） |
| R4 | #1800 heat_matrix 页崩溃 / #1810 filter 语义 / #1808 graticule 数学 | 既有缺陷：页级失败证据化（不静默空白）；测试矩阵避开 heat_matrix；RISKS/PR 披露"继承既有行为" | 依赖独立 issue 清偿 |
| R5 | 页缓存无界增长 | 内容寻址 + 双上限（条目 ≤256 / 总字节 ≤512MB）+ mtime 逐出；上限命中照常渲染（缓存是加速不是正确性依赖） | 低 |
| R6 | 跨 worker 多进程并发同一 job（重投递） | durable_job 已有终态跳过（§16）+ 工作目录写用原子 replace + attempt 目录隔离（`{job_id}/attempt-{n}`）| 低 |
| R7 | 50MB 级 spec 在 worker 重复物化内存放大 | 页文档浅拷贝（`_page_filtered_doc` 既有模式）；SVG 累积预算沿用 96MB；manifest 只存 digest | 中（与同步链同级） |
| R8 | owner/租户越权（下载他人图册/看他人 job） | 下载走既有属主 fail-closed；atlas API owner 三元组 + verify_session_owner；非属主 404；测试覆盖跨用户负例 | 低 |
| R9 | 取消后临时文件泄漏 | 取消路径 finally 清 attempt 目录；测试断言目录不存在；缓存目录除外（内容寻址非 job 私有） | 低 |
| R10 | 数据变更后旧缓存页误命中 | 指纹含 feature_count/结构指纹/legend_digest/编译器键；数据内容变更→content_revision→结构指纹变→指纹变 | 内容级碰撞理论残余（sha256 截 40hex，可接受） |
| R11 | 前端 TasksTab/词表改动破坏既有任务渲染 | kind 词表 additive；新分支渲染不动旧 kind 路径；vitest 全量回归 | 低 |
| R12 | eager 模式（无 Redis 本地测试）与真实 Celery 行为差 | executor 核心逻辑与 celery 装饰解耦（纯函数入口 `execute_atlas_job(job_id)`），任务体薄壳；两态都测 | 部署态 worker 队列配置需 ops 关注（披露） |
| R13 | 12 区验收依赖中文字体（CI 无 CJK → tofu） | `_cjk_font_face_css` vendored 内嵌既有机制复用；验收测试断言文本可提取（pypdf）而非像素 | 无 CJK 字体环境 PDF 文本层仍在（字形内嵌） |
| R14 | `product_manifest.py` 为共享演化点（#1830 也在扩） | 只读消费 build_product_manifest 摘要；不改其签名/结构；若 master 前移则 rebase 对账 | 低 |
