# PR BODY — 04｜交互式专题地图发布、可访问表达与读者工作台（GOAL-20261127-04）

## 基线与 Pi pin

- 基于 origin/master `f34cb69dda6150ee4135e6d2a476708149d9eef7`（与参考快照一致）。
- vendor/pi gitlink `ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe`（v1.6.1 参考）**未改动**——本线只消费
  既有运行能力，不触碰 vendor/pi、模型路由、host/provider 层（01 线 #2290 独占）。
- 分支：`zcode/goal-interactive-map-publication-20261127`；独立 worktree 开发。

## 实际新能力（唯一新增产品中心：只读读者视图）

一张已验证的 MapSpec → **版本化、可授权、可撤销、交互式只读 WebGIS 专题地图**：

1. **发布快照契约（WP1）**：`publication_snapshot.v1`（`app/services/publication/contract.py`）——
   product_id+version 版本化、发布后不可变、源 spec fingerprint（canonical sha256，与 mapspec store
   sidecar 同算法）、公开字段白名单、许可/署名/用途/缺失值、可访问性清单、QA 摘要、typed 阻断词表；
   红线负向扫描（owner_token/provider key/会话历史/绝对路径 fail-loud）。
2. **有界 release projection + 读者数据面（WP2）**：字段白名单/几何精度/bbox/预算（单层 50k 要素、
   总 24MiB）写时投影 + 读时二次过滤（同一实现，纵深防御）；物化产物 `data/publications/{pid}/`；
   share_code（token_urlsafe(16)、naive-UTC 过期、撤销即断链、常量时间复核、per-IP 限流）；
   发布物化走 durable job（atlas_batch 范式，幂等键 publication_id，eager/worker 双模式验证）；
   REST 13 端点（作者 8 + 读者 5，scope matrix 全登记，读者面匿名）。
3. **只读阅读器（WP3）**：`/p/[code]`（Next.js 公开页，story 页先例）——compileMapSpec(授权投影 spec)
   只读 MapLibre、获准层显隐、图例、要素弹窗（白名单属性）、白名单内筛选（谓词 ↔ 服务端 where AST ↔
   MapLibre filter 单一映射）、可访问数据表（分页≤200）、统计（显式分母）、撤销/过期/未发布统一 404。
4. **可访问等价信息（WP4）**：`accessibility_manifest`（原孤儿模块）接线进发布链并补生产语义
   （title 组件回退链、sources dict 形状）；读者"说明"面板携带 alt text/色觉实测声明/
   数据来源/投影/时间范围/缺失值说明/QA 摘要；aria-live 播报、键盘跳转链接、无 WebGL 等价视图。
5. **Pi 发布流程与作者端（WP5）**：`publication_inspect/prepare/draft/validate`（全只读或私有草稿，
   tier≤2）——**工具族不提供 publish/link/revoke**（同意红线：发布与链接签发是作者显式动作，
   服务端 confirm 门 + durable job）；作者端"发布"子面板（预览计划→逐层字段白名单→草稿→
   显式勾选确认发布→链接签发/撤销/版本历史）。

## 去重矩阵摘要

详见 `planning/GOAL-20261127-04-INTERACTIVE-MAP-PUBLICATION/DEDUP_MATRIX.md`。核心结论：
交互地图发布快照/公开只读数据面/读者页/可访问接线/Pi 工具族与作者端均为 missing→本线新建；
复用 report 分享范式、durable jobs、feature_pages、compileMapSpec、atlas 管线范式；
不重写 StoryMap/图册/PDF/atlas/#2289 文件面板（零重写）。

## 架构与双入口

UI（作者面板 + /p/[code] 读者页）与 Pi（工具族）共用同一领域服务
（`app/services/publication/`：contract/projection/store/access/plan/service/executor/tasks/reader），
同一权限（作者三元组归属 + 会话归属校验；读者 share_code），同一状态/证据（MapPublication 行 +
冻结快照 + 物化产物 + durable job）。

## 测试与性能

- 后端 pytest 38 passed（契约 9 + 生命周期 11 + 可访问性 4 + 工具 4 + Pi 旅程 4 + cartography 基线 6）
- 前端 vitest 18 passed；typecheck/eslint 干净（基线既有错误同 master，见 BASELINE.md）
- 迁移：sqlite upgrade/downgrade 实测；单 head g5202（锚 aa356ce2fcdc）
- 必过旅程 1-4 真实跑通（真实 uvicorn+sqlite+HTTP；生产 next build/start + Playwright chromium 四变体；
  Pi 链 = 真实 registry.dispatch）。证据：`planning/.../journeys/` + PROGRESS.md
- 性能（12+3 要素 fixture）：读者端点 p50 6.4-7.2ms / p95 ≤9.4ms；投影 0.025ms；分页 0.007ms；
  快照 10.3KB；浏览器筛选与服务端统计数学一致（≥500 → 5/12 行）

## 未测项 / 基线遗留（诚实声明）

- Pi RPC 全链（bridge secret + turn token）无凭证环境未验证——已验证同一 dispatch 链。
- 屏幕阅读器实测未做（键盘+aria-live 结构已验证）；Postgres 迁移未实测（sqlite 通过）。
- master 基线既有失败（非本线引入，master 原样复现）：scope-matrix 331 未登记端点、
  live-spec.test.ts 'tiles' 类型错、runtime manifest capability 悬空、tool descriptor coverage 80%。

## 迁移 / 兼容 / 回退

- 纯增量：新表 map_publications（g5202）、新路由前缀 /publications、新工具模块、新前端路由 /p/[code]；
  对既有代码的改动仅：models/__init__ 注册、main.py include、JobKind 加性枚举、
  scoped_raster_paths reserved 词表加 "publications"、i18n messages 注册、map-studio 子 tab、
  accessibility_manifest 两处**加性**回退（title 组件/sources dict——既有 list 形状行为不变，
  cartography 基线 6 passed）。
- 回退：revert 该 PR 即可（新表可保留不影响既有路径；无数据回填需求）。

## 冲突边界

- 与开放 PR #2290（01 线 Pi host/provider/model-routing）文件零交集；其迁移 g5201 不在 master，
  本线迁移锚 master head —— 合并顺序无关；PR 前重新 fetch 复核。
- 同域已合并（#2274/#2278/#2287/#2289）零重写、零冲突面。
- 开放 PR #2291/#2292/#2293 交集仅加性接线点（app/main.py include、app/tools/__init__.py
  _TOOL_MODULES、i18n messages 注册、#2292 另有 map-studio-tab）——相邻行加性变更，
  合并序无关；后合并者平凡解决。

## R1 / R2

- R1：独立审查者（非作者）逐行 diff 审查（九维度：访问隔离/撤销缓存/许可/科学语义/读者隔离/
  契约回归等）——结论 **无 P0/P1**；4×P2 + 13×P3 全部处置：
  - P2-1 base 谱系挂靠补归属校验（403，不继承他人 meta/license）
  - P2-2 Pi validate 补三元组+运行时会话双链归属
  - P2-3 发布门搬进 executor CAS 单点（retry/第二提交面无门路径消除）
  - P2-4 reader spec 源按层重写（共享源兄弟层不再错配）
  - P3：DB 级 CAS 消除 revoke×publish 复活窗口、summary 非数值/NaN 诚实化（全序列化
    allow_nan=False）、红线正则补全+typed 422、版本竞态重试、物化-快照漂移 typed 失败、
    layer_id 404 同语义、限流分桶（meta 120/data 600）、撤销清产物、死键清理
  处置表见 `REVIEW_R1.md`；新增回归测试 7 项。
- R2：关闭证据复查 + 关键旅程重跑（真实服务旅程 ok + 浏览器桌面/无 WebGL 全绿）+
  门禁终态（71 passed；唯一失败为 master 原样基线）+ 冲突复检（master 未动；与
  #2290-#2293 交集仅加性接线点），见 `REVIEW_R2.md`。
