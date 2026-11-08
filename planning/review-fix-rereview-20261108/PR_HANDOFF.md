# PR: fix(review): independent review-fix-rereview across GIS harness

全仓深度 Review → Fix → Independent Re-Review 的独立清偿分支。**不开发新方向、不抢在途 7 PR（#2011–#2017）的任何文件/语义区域。**

- 基线：origin/master `585812ee4e2d75612f2b124e6fe6587af3deca5c`
- 变更：17 文件，9 commits（每个修复独立 commit，全部先红后绿）
- 审查流程：3 路并行深度审计（后端 GIS 科学 / 安全并发 / 前端制图，深读 ~57k 行）→ 主 Agent 逐项交叉验证 → Fix-1（7 commits）→ 独立冷审 R2（无 P0/P1，7/7 判定有效，新增 6 findings）→ Fix-2（5 项闭环）→ 独立终审 R3

## 修复清单（Fix-1：2×P1 + 5×P2；Fix-2：1×P2 + 4×P3）

| Finding | 严重程度 | 摘要 | Commit |
|---------|----------|------|--------|
| A-1 | **P1** | geocompute SPATIAL_JOIN 声明 CRS 盲区：左右两侧坐标空间不一致（如左 WGS84 × 右 EPSG:3857）时静默 join_pairs=0"成功"——正是 GOAL 01 G2 注释自述修过的度/米混淆同型现行通路（buffer/clip/overlay 已修，join 漏掉）。修复：任一侧声明 CRS 时两侧经 gdf_from_features 归一 + to_crs 对齐（GIS-599 同款纪律），双侧未声明零开销走原路径 | 88cea9daf |
| C-1 | **P1** | style-only 版本恢复经 REST 带外突变权威 MapSpec（RestoreStyleIntent 无 collab_event，SSE 无事件），前端只 reload 版本账本——地图/图层面板停留在旧样式直到刷新，且用户下一次图层操作以过期 revision 提交必然 409 superseded。修复：refreshMapSpecAfterExternalMutation（游标推进 + map-state 回灌 + 镜像行同步 + 会话切换守卫） | 0c74f572d |
| B-5 | P2 | 匿名会话导出者永远读不到自己的渲染诊断 sidecar（下载可走签名 URL，诊断面被 Bearer+user_id 比对锁死）。修复：与 _export_principal 同源的 X-Session-Token 分层鉴权 | c2707b4dc |
| A-2 | P2 | service_area 直连路径 graph 无 network_dataset → snapper AttributeError → 上层 500。修复：入口类型化 ValueError | 74b1f9ac8 |
| B-4 | P2 | 分布式锁同步释放 asyncio.run 临时循环 × redis-py loop 亲和池必失败 → token DEL 从未生效，同 session 后续 acquire 排队到 30s TTL。修复：递交到 acquire 绑定 loop（有界等待），不可递交时诚实等 TTL | 42621250a |
| B-1 | P2(perf) | MemorySessionStore.overwrite 在全 store 进程级锁内做 O(features) descriptor 计算（plan_mode 每波热路径，与 store() 锁外纪律矛盾）→ 任意会话读写全部排队。修复：锁外计算 + 身份/revision 守卫回写 | 7fc214cac |
| TESTFLAKY-1 | P2(test-infra) | 导出下载/诊断测试用固定共享 /var/tmp 目录 + autouse 清盘，xdist worker 互相删文件 → 404 假红（master 既有 flaky，-n4 三跑 1/2/2 复现）。修复：每测试 tmp_path 隔离 | b450012c4 |

| TESTFLAKY-1 | P2(test-infra) | 导出下载/诊断测试固定共享 /var/tmp + autouse 清盘，xdist worker 互相删文件 → 404 假红（master 既有 flaky，主 Agent 三跑 1/2/2 + R2 独立复现 6P/1F/6P）。修复：每测试 tmp_path 隔离 | b450012c4 |

### Fix-2（R2 冷审闭环，2 commits）

| Finding | 严重程度 | 摘要 | Commit |
|---------|----------|------|--------|
| R2-N1 | P2 | stale map-state/SSE spec 被 commitMapSpecDocument 旧代次拒绝后仍无条件镜像/修剪 HUD —— 剪掉新 mutation 刚加的镜像行/复活已删层（面板漂移直到下一 spec 事件）。修复：refresh 与 SSE 实时路径统一只在 commit 接受时镜像/修剪 | 4b12c8359 |
| R2-N2 | P3 | refresh 游标取 restore 响应与 fetch 真相的较大 revision（防一次 spurious 409） | 4b12c8359 |
| R2-N3 | P3 | 诊断端点 404 detail 统一（"Session not found" vs "该导出件没有诊断记录"的文本差异构成弱存在性 oracle） | 1b8077691 |
| R2-N4 | P3 | service_area graph/dataset 双 None 并入类型化拒绝（master 预存在 AttributeError→500） | 1b8077691 |
| R2-N6 | P3 | B-4 dead-loop 分支 coro 显式 close（不泄漏 never-awaited RuntimeWarning） | 1b8077691 |

## 记录不修（理由见 FINDINGS.md）

- R2-N5（A-1 声明 CRS 路径对非 JSON 原生属性 TypeError）：GeoJSON 来源不可能携带，未发现生产者；feature id 重编号无功能影响
- B-2/B-3（RedisSessionStore 跨 loop 客户端裸读 / per-loop 锁互斥失效）：跨 loop 架构缺口，需 43 调用点重构或架构决策，超出最小修复边界
- B-6（context_refs 清尾删除的租约窗口）：PROTECTED 邻接（#2017/#2014 共享面），爆炸半径为零（私铸未发布 ref）
- C-1b（RestoreStyleIntent 跨客户端广播）：需 collab 协议新增事件种类，设计变更；reconcile 兜底

## 已验证（见 TEST_MATRIX.md）

- 每项修复先红后绿（REPRODUCTIONS.md 有红/绿输出；R2 独立复现 master 侧缺陷与 flaky）
- targeted 回归 194（后端 17 文件）+ 138（前端，含 Fix-2 用例）连续两遍完全一致
- ruff（触及源文件）/ tsc --noEmit / git diff --check 全干净
- 保护矩阵对拍：17 个变更文件无一落在 #2011–#2017 占有清单（FIX_LEDGER.md 逐文件对照；R2 独立复核零命中）

## 未验证/边界

- master 既有红（与本分支无关，未触碰）：tests/test_pdf_export_auth.py 两个 vector-pdf 参数化用例（属在途 #2015 出版链邻接）
- 未跑全量 tests/（按资源纪律跑 targeted 面）、浏览器/Playwright、真实 Redis/PostGIS/Pi RPC lane——本分支全部修复可在本地 unit/integration 级完整验证，无一项依赖上述外部服务
- B-2/B-3 的跨 loop 生产路径（低概率）未修复，如遇间歇 wrong-loop 异常/LRU 记账漂移请参照 FINDINGS.md B-2/B-3 排查

## 风险与回滚

- 每个修复独立 commit，可单独 revert；无 schema/API 形状变更（B-5 是鉴权放宽：匿名 owner 增加可达通道，认证语义不变）
- 回滚顺序建议：TESTFLAKY-1（纯测试）→ A-2/B-4/B-1（防御性/性能）→ B-5 → A-1/C-1（行为修复，回滚则缺陷复现且有测试红守卫）
