# FINDINGS — review-fix-rereview-20261108

状态值：fixed(本分支) / record-only（不修，理由注明） / protected（在途 PR 区域，仅记录）

来源：R1 三路 subagent 审计（A 后端 GIS/科学、B 安全并发、C 前端制图）+ 主 Agent 交叉验证；R2 独立冷审（全新 subagent）追加 6 项（1×P2 + 5×P3）。

## 已修复（本分支，7 + Fix-2 的 5 项合并计 12 项）

| ID | 严重程度 | 根因 file:line | 一句话根因 | 修复 commit | 链路 |
|----|----------|----------------|-----------|-------------|------|
| A-1 | **P1** | app/services/geocompute/ops.py:428-441（原行号） | SPATIAL_JOIN 把两侧 features 原样送度空间谓词，声明 CRS（如 EPSG:3857）不归一 → WGS84×3857 静默 join_pairs=0"成功"；G2 修复只覆盖 buffer/clip/overlay | 88cea9daf | 2 科学正确性 |
| C-1 | **P1** | frontend/components/sidebar/map-product-versions.tsx:235-255 + app/services/mapspec/mutation_registry.py:555-560 | style-only 版本恢复经 REST 带外突变权威 MapSpec（RestoreStyleIntent 无 collab_event，仅 op journal），前端只 reload 账本 → 地图/面板停旧样式 + 下一笔用户 mutation 必 409 | 0c74f572d | 3/5 状态同步 |
| B-5 | P2 | app/api/routes/map.py:435-450（原） | 诊断端点硬性 Bearer + user_id 比对，匿名会话导出主体 session:\<sid\> 永远不等 → 匿名导出者读不到自己的渲染诊断（下载可走签名 URL） | c2707b4dc | 6 权限 |
| A-2 | P2 | app/services/network/service_area.py:137-138（原） | graph 在场而 network_dataset 缺席 → snapper AttributeError → 上层 500（应类型化拒绝） | 74b1f9ac8 | 2 健壮性 |
| B-4 | P2 | app/services/distributed_lock.py:222-232（原） | 同步上下文 release() 用 asyncio.run 临时循环执行 token DEL —— redis-py 池 loop 亲和必失败 → DEL 从未生效，同 session 后续 acquire 排队到 30s TTL | 42621250a | 6 并发 |
| B-1 | P2(perf) | app/services/session_data.py:514-519（原） | overwrite() 在全 store 进程级锁内 await to_thread 跑 O(features) descriptor 计算（与 store() 锁外纪律矛盾）→ plan_mode 热路径全 store 串行停顿 | 7fc214cac | 6 性能 |
| TESTFLAKY-1 | P2(test-infra) | tests/test_map_export_download_auth.py:19-54（原） | 固定共享 /var/tmp 目录 + autouse 清盘在 xdist worker 间互相删文件 → 404 假红（master 既有 flaky：串行 6/6，-n4 每轮 1-2 随机失败，三跑 1/2/2 复现；R2 在 /tmp master 快照独立复现 6P/1F/6P）；同款模式亦见于 test_export_diagnostics_sidecar.py | b450012c4 | 6 质量门禁 |

### Fix-2（R2 冷审发现，5 项闭环）

| ID | 严重程度 | 位置 | 一句话根因 | 修复 commit | 链路 |
|----|----------|------|-----------|-------------|------|
| R2-N1 | P2 | frontend/lib/mapspec/user-mutation.ts + frontend/lib/hooks/use-sse-stream.ts:621-632 | stale map-state/SSE spec 被 commitMapSpecDocument 旧代次拒绝后仍无条件镜像/修剪 HUD —— 剪掉新 mutation 刚加的镜像行/复活已删层（预存在 SSE 模式，C-1 新调用点扩大暴露面） | 4b12c8359 | 5 状态同步 |
| R2-N2 | P3 | user-mutation.ts | refresh 游标不取 fetch 到的更高 revision → 一次 spurious 409 | 4b12c8359 | 5 |
| R2-N3 | P3 | app/api/routes/map.py | 诊断端点 404 detail 文本差异（"Session not found" vs "该导出件没有诊断记录"）构成弱存在性/归属种类 oracle | 1b8077691 | 6 |
| R2-N4 | P3 | app/services/network/service_area.py | graph/dataset 双 None 仍 AttributeError → 500（master 预存在残留） | 1b8077691 | 2 |
| R2-N6 | P3 | app/services/distributed_lock.py | dead-loop 分支 coro 未 close → never-awaited RuntimeWarning | 1b8077691 | 6 |

## 仅记录（不修，理由）

| ID | 严重程度 | 位置 | 根因 | 不修理由 |
|----|----------|------|------|----------|
| R2-N5 | P3 | app/services/geocompute/ops.py:447-448 | 声明 CRS 路径 round-trip `gdf.to_json()` 对非 JSON 原生属性（如 datetime）TypeError；feature `id` 成员被位置索引重编号（join 只消费 properties/geometry，federation.py:248/268/289-291，无功能影响） | GeoJSON 来源 features 不可能携带非 JSON 原生值；未发现 datetime 生产者（R2 探针确认）。坐标全精度无舍入。若未来出现非 JSON 属性生产者，届时在入口规范化 |
| B-2 | P2 | app/services/session_data_redis.py:200-214 + 43 处 `self._r` 裸读 | per-loop 客户端表与共享 legacy `_r`/`_bound_loop` 并存，跨 loop 并发下裸读可拿到别的 loop 的客户端（间歇 wrong-loop 异常 → LRU 淘汰静默跳过） | 修复需重构 43 个调用点为局部捕获（或原子元组替换），改动面大且难以在本分支内充分验证双 loop 生产路径；主 loop/bridge loop 并发使用为低概率路径。记 finding 供后续专项 |
| B-3 | P2 | app/services/session_data.py:285-332 | per-loop 锁 property 换 loop 即静默新建锁对象 → 双 loop 并发下共享 dict 互斥失效（USE_REDIS=false 部署的记账漂移） | 与 B-2 同根（跨 loop 架构缺口，distributed_lock.py #1681 已文档化该模式缺陷）；正确修复需 per-loop 分桶或网关统一回主 loop —— 架构决策超出最小修复边界 |
| B-6 | P2 | app/services/session_context_refs.py:33-52 + distributed_lock.py:617-667 | store_context_payload 清尾 delete_ref 在 lock.lost 读取后仍可能无租约执行（爆炸半径：删除的是本协程私铸未发布 ref，WATCH 保证不误删他人） | PROTECTED 邻接（与 #2017/#2014 共享面耦合）；防御性缺口而非可利用缺陷 |
| C-1b | P2 | 同 C-1 后端侧 | RestoreStyleIntent 无 collab_event → 其它浏览器/协作者看不到恢复直到 reconcile | 需为 collab 协议新增事件种类（presentation 单层 / doc 仅组织态均不适配全层表达面恢复）—— 设计变更超出最小修复；跨端一致性最终由 reconcile 兜底 |

## 误报/排除（审计确认无缺陷，摘记）

- 前端：SSE 乱序/重复/回放（per-turn 事件 id 去重 + Last-Event-ID 单调门）；卸载后 setState/泄漏（全部 hooks 有 mounted ref + AbortController）；跨会话残留（selectSession 清场清单完整）；导出数值精度（extent/tile-zoom/highdpi 均防御式）；WebGL context lost（idle 超时→降级→类型化失败，不伪造产物）。详见子代理 C 报告 12 项排除。
- 后端安全：所有权/IDOR 面全绿（require_owned_session/verify_session_owner 全路由接线、fail-closed、404 反存在性）；路径注入面全绿（safe_blob_key/raster_png_path/resolve 收容）；SSRF 面全绿（唯一抓取点 admin-only + 双重校验）；singleflight 取消语义、ref_payload_cache epoch guard、artifact_lifecycle GC 双复检均正确。详见子代理 B 报告排除清单。
- 科学算法：路由核心（中点切边/双向/单行道）、Moran/Geary/Gi*（与 esda 对拍）、zonal_statistics（像元中心/nodata）、graph_builder、snapping、od_matrix、accessibility、allocation MILP、raster_math、heatmap 单位自洽 —— 逐项核对无误。详见子代理 A 报告排除清单。

### R3 终审发现（2 项）

| ID | 严重程度 | 位置 | 一句话根因 | 处置 |
|----|----------|------|-----------|------|
| R3-P3-1 | ~~P3~~ → **refuted** | app/services/geocompute/ops.py | （R3 称空集行为收紧为类型化错误） | 主 Agent 在 origin/master 快照与本分支分别实测：算子路径对空 features 双方均抛同款 NodeExecutionError（前置检查空列表 falsy → 计数<2 → raise），消息逐字一致。R3 探针对照的是 federation 直调而非算子路径。**驳回，无行为变化** |
| R3-P3-2 | P3 | app/services/distributed_lock.py:230-241 | is_running() 检查与 run_coroutine_threadsafe 递交之间 bound loop 恰好关闭的极窄理论窗口 → never-awaited warning | record-only：关停路径、窗口极窄、TTL 兜底 |
