# G39 REVIEW — 独立评审记录（两轮）

评审者：独立 reviewer subagent（未参与实现；两轮同一模型角色，第二轮冷启动携带第一轮结论复检）。

## 第一轮发现（2026-11-15）

- **[P0] POST 路由 session_id 布线**：body 携带 session_id 而 `require_owned_session` 校验 query 参数 → 真实调用者全 422；且 body session_id 未与守卫比对 → 跨会话写入/预览数据外泄（评审者用真实守卫+两会话数据库实证：foreign body → 受害者命名空间写计划 + 读到受害者栅格值）。
- **[P2] 无效原因分类塌缩**：`_read_window` 把 mask+非有限+nodata 折叠进单一 valid 数组 → nodata/non_finite 原因永远 unreachable（单元测试只测 classify_raw 纯函数，掩盖了真实路径）。
- **[P2] execute 工具 wait_max_s 无上限钳制**（契约说 0-60s）。
- **[P3]×7**：results 工具行帽、站位层 ref 先登记后验证、_latest_job 只扫 10 行、supplement nodata 继承 band1、same_generation 漏 dev、零权支撑致命、进度 total=1 等。

已同时核实为好：probe 同口径 floor、bilinear 权重和=1、scale/offset 恰一次、分类拒插值、dtype 非证据、距离模式矩阵、part 断口、ABA 批间复核、并发收敛、预算闸无旁路、发布顺序、RESULTS_EXPIRED、G18/G20/G23/G24 复用为真、前端缺口断线/抽稀披露/hover 同步、i18n 对齐、共享文件最小。

## 修复（作者，同日）

1. **P0**：全部 POST 路由 `session_id: str = Query(...)`（与真实守卫签名同形）；body 模型全部去 session_id（CancelIn/ResultsIn 删除）；前端客户端 5 个 POST 全部改 query。真实守卫签名 override 的 API 测试 + 缺 query 422 测试。
2. **P2 分类**：mask 数组贯通（`_read_window → out_mask → sample_*_pixel(mask_arr)`）；声明 nodata 优先于 GDAL mask 折叠；**根因修复**：`facts.nodata` 在服务路径从未填充（G20 band_meta 无 nodata 字段）→ `_resolve_nodata` 回退 `source.descriptor.nodata`；supplement 用 `ds.nodatavals[band-1]`。新增端到端回归（真实 nodata+NaN 栅格 → coverage {nodata:1, non_finite:1}）——修复前红、修复后绿。
3. **P2 wait**：`min(60, max(1, wait_max_s))`。
4. **P3 批**：results per_page ≤100（模型+运行时双钳）；站位层改验证后登记；扫描 200；same_generation 含 dev；进度钩子 (done, total=站位数)；零权支撑不致命（双向测试）；`_domain_root` 归一化别名拒绝；no-op 行删除。

## 第二轮复检（2026-11-15）— APPROVED

评审者独立复跑与对抗探针：
- **真实守卫零 override**：alembic sqlite + 双 Conversation + 真实 X-Session-Token 依赖 —— 5 个 POST 缺 query 全 422；body 攻击结构上不可能（schema 无 session 字段）且 foreign query 404 不泄露；跨会话 execution_id 404。
- **分类三腿全通**：真实栅格 + nodata + NaN + **真 GDAL mask band 洞**（write_mask）→ nodata/non_finite/masked 三因分型正确（作者测试缺 mask 腿，评审者补证）。
- 零权/renorm 数学复检（评审者自算边缘权重后确认 kernel 正确）；发布顺序无 orphan 路径；进度钩子兼容分支安全。
- 复跑：backend 57 + Pi journey 1 + 邻域 107 + 前端 18 + tsc 0（与作者声明一致）。
- 遗留 P3 卫生项（死 ResultsIn、误导测试名、进度分母）——作者随后清理（57 绿复跑）。

**结论：APPROVED，无阻塞项。**
