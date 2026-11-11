# G26 LOOP_LEDGER

## 当前状态（随里程碑更新）

- BASE_SHA：`b8f2a6ecc777d0ae633202034b609a48a8d140d9`
- 分支：`zcode/g26-mixed-map-publication-20261112T000000Z`
- worktree：`/home/kevin/projects/webgis/webgis-wt-g26-mixed-map-pub-20261112T000000Z`
- 环境：主仓 `.venv` 只读环境层（Python 3.13.17）；frontend 独立 pnpm install；
  vendor/pi 子模块已 init（dd30101c）+ build（进行中，A26 用）。

## 里程碑

### M1 侦查+探针（done）
- RECON/PLAN/INTERFACES/SUPPORT_MATRIX/ACCEPTANCE；E0 关键探针（WeasyPrint
  内嵌 SVG <image> 不可用 → HTML <img> 分层；fetcher 需 URLFetcherResponse；
  rsvg data URI 精确像素）。

### M2 核心实现（done；commit 2ccb99928）
- P2 `raster_authoring/publication_window.py`（地理对齐窗口渲染；11 测试）
- P1 `raster_authoring/publication_bindings.py`（冻结绑定）
- P3 `mapspec_to_svg` raster 分支嵌入 + `publication_export` 分层 PDF 组装
  + fetcher 修复/放行 + raster_layers 回执（10 测试 + 50 既有回归）

### M3 图例/服务/入口（done；commit 9a1bf842a）
- P4 derive_legend_items raster_rgb/notes + 图例脚注 + fetcher 单测（13）
- P5 `mixed_map_export` 服务（三格式/CAS/blocking/manifest）+ `/export/mixed`
  路由 + `webgis_map_export` 工具 + resolve_source typed 包裹（10 服务 + 6
  路由/工具测试）

### M4 UI 接线 + 多帧（done；commit a38ac912d）
- `export_vector_pdf` 检测数值栅格层 → `runMixedPdfExport`（前端 6 测试 +
  既有 10 回归）；A17/A18 多帧窗口测试（4 帧唯一 bounds/每页一图/无交集披露）

### M5 验证收口（进行中）
- 后端受影响目录全量：**3585 passed, 3 skipped, 3 xfailed**（cartography +
  raster_authoring + publication_basics）
- 前端全量：0 失败（68 文件通过）
- A26 Pi 子进程车道：vendor build 进行中 → 跑 focused stub-LLM journey
- TEST_EVIDENCE 更新（E1-E6）

## 下一步可执行动作

1. Pi build 完成 → 写/跑 A26 focused journey（真实 Pi 子进程 + stub LLM +
   webgis_map_export dispatch → 产物下载核验）。
2. 独立 review（reviewer subagent）：BASE→HEAD 全量 diff + 复跑关键用例。
3. review → fix → review 循环至 P0/P1/P2 阻断归零。
4. PR_BODY.md + 推送 + 创建 PR（不合并）。

## 必须保留的约束

- 不合并 PR；不动 master；不碰兄弟方向 worktree。
- Pi vendor 主修改权属 G22（本方向只 build，不修改）。
- 重任务（Pi build/全量 pytest/浏览器）串行执行。
- 生产路径权限/CAS/取消/披露不放宽。

## 未解阻断

- A27 真实浏览器 E2E：无授权浏览器会话环境（前端链路已有 mock-API 级测试；
  下载 URL 服务级已验证）——如实记录，不冒充完成。
- Docker slim 运行时无 rsvg-convert：PNG 面 typed 降级（能力探测 + 503），
  非本方向阻断（部署面依赖登记在 PR_BODY）。

## M6 独立评审循环（2026-11-12）

- Round 1：APPROVE-WITH-FIXES（4×P1 / 5×P2，无 P0；reviewer 独立复跑 80
  用例 + Pi 旅程 + 字节稳定性探针）。详见 REVIEW.md。
- 修复：commit 9b5323daa（P1-1 旋转 transform / P1-2 编译后复检 / P1-3 前端
  码表归一 / P1-4 合成 extent / P2 全关 + 4 个新回归测试）。
- 修复后全量：受影响目录 **3589 passed**（+4 新测试）；Pi 旅程 1 passed；
  前端 16 passed。
- Round 2 复审：进行中（同一 reviewer，未参与实现）。

## 最终出口

- Round 2 通过后：推送分支 → 创建 PR（不合并、不动保护）→ 输出总结。
