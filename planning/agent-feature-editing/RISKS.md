# RISKS

| # | 风险 | 缓解 | 状态 |
|---|---|---|---|
| R1 | GitHub 网络不可达（fetch/gh 全失败）→ 无法核对 open PR/issue，无法 push/建 PR | 已记录；PR 前重试；若仍失败：保留分支+提交，输出可粘贴 PR 正文与失败日志，不谎称已建 | 开放 |
| R2 | 会话锁重入死锁（数据锁内调 mapspec engine） | 严格顺序：数据锁释放后才调 engine；单测锁顺序 | 已设计（D6） |
| R3 | overwrite 后派生缓存（MVT/parquet/descriptor）残留鬼数据 | 统一走 ref_lifecycle.invalidate_ref_caches(OVERWRITE)（唯一失效权威）；集成测试断言 MVT 二次取数为新数据 | 已设计 |
| R4 | journal 有界丢弃导致 undo 链断裂 | head 链校验 fingerprint；断链 entry 无法 undo → typed `undo_unavailable_chain_dropped` + dropped 披露 | 已设计 |
| R5 | 大数据集编辑 O(payload) 放大（50MB 级） | ≤20000 要素门 + preview ≤200 目标 + diff 摘要有界（几何用 digest 不内嵌全量） | 已设计 |
| R6 | 模型伪造 actor/origin/session | actor/origin 服务端裁决（REST=鉴权面；工具=turn context）；session_id 注入；plan 绑 session+ref+revision；负例测试 | 已设计 |
| R7 | 枚举校验误判（高基数字段被当枚举拒绝合法值） | 仅 profile distinct ≤24 且非数值/时间类型才 categorial；finding 带 distinct 词表供模型改口 | 已设计 |
| R8 | 米→度换算误差（高纬/大位移） | pyproj 精确换算（非度近似）；move_by 位移上限 10km 防呆；测试含高纬案例 | 已设计 |
| R9 | 多标签双开同会话并发编辑 | 同 ref 串行化靠会话锁 + content_revision CAS（后写 409 + diff）；前端 409 收敛 toast（user-mutation 同款） | 已设计 |
| R10 | durability/checkpoint 恢复漏 journal（map_state 键被恢复面剥离） | 检查 durability snapshot 是否透传未知 `_` 前缀键（IR ledger 先例 P1-2 曾被剥离）；恢复测试钉住 | 待验证（M8） |
| R11 | MapSpec inline-only 层编辑后出现新旧双真相 | editable 判定要求 ref-backed；inline-only → typed 拒绝并说明；ADR 披露边界 | 已设计 |
| R12 | 与在途 open PR 冲突（网络不可达未核对） | PR 前二次去重；中心文件仅行级新增，rebase 成本低 | 开放（同 R1） |
| R13 | 前端 SSE mapspec 镜像与 dataset 更新竞态（戳未刷新前重拉旧 revision） | data-plane 单飞+ETag；戳刷新后再入一次失效；receipt 携带 mapspec_revision 供前端对齐 | 已设计 |
| R14 | 快照/生成物（openapi/endpoint-matrix/docs）漂移 | 原生成器再生（API_SNAPSHOT_UPDATE=1 等），不手改 | 流程纪律 |
