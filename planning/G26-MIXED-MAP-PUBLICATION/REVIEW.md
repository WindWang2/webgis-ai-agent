# G26 REVIEW — 独立评审记录与修复循环

## Round 1（2026-11-12；reviewer：未参与实现的独立 subagent）

范围：BASE `b8f2a6ecc` → HEAD `fde537b34` 全量 diff（29 文件 +4547/−33）；
独立复跑核心用例（80 passed）、A26 Pi 旅程（1 passed）、前端两套（16 passed）、
无绑定字节稳定性探针（base/HEAD SHA-256 相同）。

**判定：APPROVE-WITH-FIXES**（4×P1 / 5×P2；无 P0）。

### Findings → 修复（全部落地于 `fde537b34` 之后的修复 commit）

| # | 级别 | 议题 | 修复 |
|---|---|---|---|
| P1-1 | P1 | 旋转 affine 用 from_bounds 重建 north-up transform → 整体错位 ~⅓ 画幅（实证）+ coverage 空角被涂满 | `publication_window.py`：改用真实降采样窗口 transform（`src.window_transform(win) * Affine.scale(...)`，rasterio 文档模式，保留旋转项）；新增 `TestRotatedAffine`（热像元落在投影位置 ±3px + 空角 alpha<0.98） |
| P1-2 | P1 | blocking 只看编译前清单；编译期窗口失败（文件改写/无表达层）被降级为诊断后仍可报 complete | `mixed_map_export.py`：`_post_compile_omissions` 编译后复检（embedded/no_overlap 才算在场）；blocking → `EXPORT_BLOCKED_INCOMPLETE`；allow_partial 并入清单；新增 `TestPostCompileRecheck`（真实退化视图路径，无 mock） |
| P1-3 | P1 | 前端 blocked/busy 分支死代码（后端大写码 vs 前端小写比较；测试用自造小写码自证） | `api/publication.ts` 码表统一小写归一；测试改用后端真实大写码 `EXPORT_BLOCKED_INCOMPLETE` 验证归一（死代码回归锁） |
| P1-4 | P1 | 纯栅格会话合成 extent 帧只对 PDF 面生效；SVG/PNG 退化为世界视图且 manifest 谎报 view 来源 | `_export_svg/_export_png` 显式传 `bounds=synthetic_extent`（编译器不读 frames extent） |
| P2-1 | P2 | 悬空源引用/未知源类型层在 allow_partial 下从清单消失 | `_unsupported_layer_report` 枚举 `source_ref_dangling` / `source_type_unsupported:*` / `numeric_raster_without_expression` |
| P2-2 | P2 | 分层体构建失败静默退回整页 SVG（WeasyPrint 丢 image 却留 embedded 回执） | 嵌入在场且分层体不可建 → `PageCompileFailure` typed 页失败 |
| P2-3 | P2 | 三处同义反复/过弱断言（owner `or True`、session 检查、圆点探针） | 全部收紧：owner 双文件断言、6 个会话身份子串、圆点 vs 无要素栅格背景对比 ≥20、PNG 渐变画布内远点通道差 |
| P2-4 | P2 | A05「不可重投影 CRS typed 拒绝」无测试；A19 措辞强于实现 | 新增 `TestExoticCrs`（可解析不可 warp 的 WKT → typed INVALID_CRS/RENDER_FAILED）；P1-2 修复后 A19 措辞成立 |
| P2-5 | P2 | round_shape 弃用告警；PNG/SVG 固定画布未披露；numeric_raster 静态瓦片层行为变化未记 | 弃用调用移除（显式取整）；CHANGELOG/PR_BODY 记录行为变化与画布口径 |

### Reviewer 验证为良好（无需改动）

非旋转放置数学（与矢量投影闭包逐像素同构）、冻结纪律（CAS/单读/复检/
stat/style_id）、fetcher 安全（deny-by-default + 双 data URI 白名单 + 上限 +
URLFetcherResponse 形状）、注入面（仅 pipeline 生成 data URI、属性转义）、
预算/竞态（剥离核算、24MB 嵌入预算、WeasyPrint 槽、缓存上限）、所有权
（manifest sidecar 同 principal + fail-closed 下载）、Pi 工具纪律（tier 2/
state_mutation/refs-only）。

## Round 2（复审）

reviewer 对修复 commit 复审（见下），确认 P1×4 / P2×5 全部关闭。
