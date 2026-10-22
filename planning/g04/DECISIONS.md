# G04 DECISIONS（执行期决策日志）

（架构级决策见 ADR-0230 与 PLAN.md D1-D9；本文件记录执行期的战术决策与理由。）

## D-E1 save-as-my-template 派生：实例 id → base 槽位解析
初版按组件**实例 id**（`legend-main` 等）作 slot id 与 base 槽位表（`legend`）
匹配 → 5/8 组件编辑静默丢弃（reviewer P1-2 实证）。改为按类型解析回 base
槽位（`INSTANCE_ID_BY_TYPE` 约定，声明序首个——与 apply_contract 偏好序同口径）；
**无法用差异覆写表达的新增组件 → 诚实降级 inline**（保真优先于变体简洁）。
测试用真实 apply 产物（不用手搓 id）钉住。

## D-E2 钉版本 apply 不绕过发布门
工具层曾把任何显式 revision 映射为 `for_replay`（草稿可应用——org/project
成员可把同事未过校验的草稿写进自己图）。改为：apply（含钉版本）要求目标版本
`published`；`for_replay`（旧图打开/导出）允许 `published`/`retired`，**拒绝
`draft`**（未过发布门的载荷不进渲染）。代价：旧图若最新版是 draft，requiredness
降级 `not_evaluated`（诚实，不冒充 pass）。

## D-E3 DB 资产永不进全局注册表
注册表是进程级无鉴权结构：DB 资产 upsert 会跨租户泄漏元数据。决策：仅 builtin
（公共命名空间）资产 upsert（单调 revision）；DB 资产 apply 走 `apply_contract
(template=...)` 显式钉版本模板，导出走 `extra_templates` 显式注入。代价：
DB 资产的新鲜度按请求解析（有界、scoped），与样式模板 on-demand 合并同语义。

## D-E4 export replay 的身份上下文
初版只传 `user_id`（org 共享资产的导出重放会静默降级）。改为经
`actor_ids(_user)` 传 (user_id, org_id, role) 三件套，与 REST 面一致。

## D-E5 save 工具的会话属主守卫
REST 双胞胎有 `verify_session_owner`，工具面初版缺失（可派生他人会话版面）。
补守卫：async handler 内直接 await，HTTPException → `missing_session` reason
（不泄露存在性语义与 REST 404 对齐）。测试夹具相应在 async 隔离库造
Conversation 主行。

## D-E6 测试隔离：autouse registry 重置
builtin 资产 upsert 进进程级注册表单例，两个 e2e 文件会污染其他文件的模板
计数断言（reviewer P2-3 复现红）。加 autouse `_registry_pristine` fixture
（重置 → 建 → yield → 重置），顺序矩阵验证通过。

## D-E7 P3 处置选择
- 修：db_unavailable 异常文本泄漏（结构化 reason）、asset_count 与截断一致、
  前端 project 可见性 project_id 输入、误名测试改名、弱断言收紧。
- 登记不修（PR 风险段披露）：org-NULL token 创建的资产无法 org 共享（JWT
  历史缺 org claim——样式模板线同款约束）；list_assets N+1（上限 50 行、有界）；
  builtin 资产 detail GET 404（列表已含全部信息）；fingerprint 不含 base 模板
  漂移（资产指纹=资产内容指纹，base 漂移由系统模板版本链负责）；
  min_count<=max_count 门禁（无消费方，inert）。
