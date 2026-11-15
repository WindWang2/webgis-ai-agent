# G40 TEST_MATRIX — 验证矩阵与执行记录

## 场景覆盖（任务书 S01–S32）

| ID | 覆盖位置 | 状态 |
|---|---|---|
| S01 点层绑内置三角形 | test_g40_symbol_assets::test_s01_point_binding_uses_defs_and_use（use×2/rotate/无默认圆）+ Pi journey binding_pins_builtin | ✅ |
| S02 typed 类别（1/"1"/true） | 同文件 test_s02（string 属性 vs number 声明 → 回落主绑定，不并类）+ live-project S02 前端测试（match 表达式不含 "1"） | ✅ |
| S03 一资产两层参数不同 | imageId 含 params（contracts.test）；catalog docs 按 digest 去重 | ✅（结构面）|
| S04 v2 发布旧图钉 v1 | test_s04_version_pin（digest 三元组不漂移）+ catalog digest_pin_mismatch | ✅ |
| S05 SVG 导入→预览→应用 | test_s05_import_happy_path（单一 digest）+ Pi journey | ✅ |
| S06 script/外链/CSS url | HOSTILE 参数化 6 例 typed 拒绝 | ✅ |
| S07 viewBox 零/NaN/节点/循环 | test_s07_*（viewbox/nan/节点帽/嵌套帽/use 未知目标） | ✅ |
| S08 clip/filter/text | test_s08_unsupported_elements_disclosed（5 例含具体码） | ✅ |
| S09 非中心锚+旋转 | anchor_offsets_geometry + point_symbol_transform（Python）；g40PointTransform（TS） | ✅（结构）；bearing 实测见浏览器面（未覆盖——见未测项）|
| S10 尺寸 0/负/极大 | test_param_bounds_s10 | ✅ |
| S11 洞/多面 | test_s11（evenodd ≥2 路径）+ readback PNG 像素 | ✅ |
| S12 同色不同向纹理 | test_s12_legend（swatch 片段不同）+ readback（同参数不同资产） | ✅ |
| S13 透明背景 | rasterize（alpha 0）+ compile（transparent 不画底）+ 归一化测试 | ✅ |
| S14 96/300 DPI | test_s14_dpi_invariance（根尺寸恒定；pattern 宽随 dpi_scale） | ✅ |
| S15 style reload | runtime reconcile 重挂 → ensureRegistered hasImage 幂等重装（实现面；jsdom 不渲染真 style——见未测项） | ⚠️ 结构 |
| S16 两 frame 实例 | register.ts idOwners 引用计数（releaseMap 只清末位）+ registrationStats | ⚠️ 单元面（无真双 map 浏览器实测——见未测项）|
| S17 迟到加载 | warmSymbolAssets epoch token（实现）+ doc 缓存 digest 寻址（迟到不串） | ⚠️ 结构 |
| S18 删被引用版本 | test_s18_delete_version_reference_guard（真实 mapspec.json 扫描） | ✅ |
| S19 跨 owner 猜 ID | test_s19_scope_matrix（404/写 403/admin） | ✅ |
| S20 CAS 冲突 | test_binding_preview_apply_undo_cycle（SUPERSEDED） | ✅ |
| S21 undo/重开/重启 | 同上（unbind 恢复快照）+ Pi journey unbound_paint_restored；磁盘权威（磁盘 store 落盘） | ✅ |
| S22 锁层/隐藏层 | test_s22（agent 拒/user 可；visible 不变） | ✅ |
| S23 资产缺席 | test_s23（诊断 + 不画默认圆）+ 前端 TS 孪生测试 | ✅ |
| S24 MVT 入口 | 绑定层型合同放行 vector 源（compiler source-layer 既有）；live 经 MapLibre 原生 source | ⚠️ 结构（无真 MVT 浏览器实测——见未测项）|
| S25 大量要素/目录分页 | defs 去重（同 digest+params）+ list 分页测试 + 缓存帽 | ✅（帽内）|
| S26 三格式回读 | test_s26_svg/png/pdf（rsvg+WeasyPrint+pdftoppm 真实像素 oracle） | ✅ |
| S27 出版期新版本 | test_s27（manifest 冻结清单） | ✅ |
| S28 不支持组合 | declutter/map-alignment 显式 UNSUPPORTED_*（前后端测试） | ✅ |
| S29 Pi 生产发现→绑定 | Pi journey（真实子进程：list→apply→unbind 全 dispatch） | ✅ |
| S30 完整旅程→回读 | journey verdict（MapSpec 权威断言）+ 出版回读 | ✅ |
| S31 碰撞合同 | test_s31（N 要素=N use 无省略）+ 前端 allow-overlap 断言 | ✅ |
| S32 变换边界进 G16 | point_symbol_obstacle_box → v1 障碍（Python 测试路径）；TS 同式 | ✅（编译面）|

## 执行记录（2026-11-15/16）

| 套件 | 命令要点 | 结果 |
|---|---|---|
| G40 后端（核心+导入+绑定+Round1 修复回归） | `pytest tests/cartography/test_g40_symbol_assets.py` | 48 passed |
| G40 目录 | `pytest tests/unit/test_g40_cartographic_asset_catalog.py` | 7 passed |
| G40 出版回读 | `pytest tests/cartography/test_g40_publication_readback.py` | 5 passed（含真实 PDF/PNG） |
| Pi 真实旅程（修复后复跑） | `PI_JOURNEY_E2E=1 ...`（vendor v1.5.2 pin） | 1 passed（10/10 verdict，28s） |
| cartography 全量（修复后复跑） | `pytest tests/cartography/` | 3655 passed, 3 skipped, 3 xfailed（deselect 1 既有基线：carto_ir_corpus scale_portrayal_cases —— 干净 base 复现同样失败） |
| unit 全量 | `pytest tests/unit/` | 既有基线：thematic_authoring/test_service.py 与 gis_harness/temporal_products/test_service.py 同名无 __init__ 收集冲突（干净 base 复现）；单进程全跑另有 ~104 order/环境敏感失败，未逐一归因 |
| unit 针对集（G40 触及模块的全部引用方，46 文件） | `pytest $(grep -rln 触及模块 tests/unit/ \| grep -v modelops)` | **831 passed**；5 failed 全部在干净 base 逐条复现（test_heat_matrix_svg…/test_harvest_prefers…/test_specialist_carto_auditor×3 = 既有基线）——本分支零回归 |
| 前端 | `vitest run lib/cartographic-assets/contracts.test.ts` | 15 passed（+F3 lattice 几何 oracle） |
| tsc | `npx tsc --noEmit` | 0 error |
| ruff | G40 触及文件 | All checks passed（print_layout 既有 F841/E741 为基线） |
| ADR 编号 | test_adr_numbering_contract | 0286/0287/0288 重号 = 既有基线（G34 ADR 已记录）；0292 无新增撞号 |

## 已知未测项（如实披露）

1. **真实浏览器 live 渲染**（S01/S15/S16/S17/S24 的像素级）——需要
   Playwright + 真后端 + 真登录会话；本任务以（a）MapLibre 6.10.0 .d.ts
   API 亲核、（b）runtime 投影单测/类型合同、（c）headless compile 的
   symbolAssets 结构断言、（d）Pi 真旅程（MapSpec 权威面）覆盖语义；
   浏览器像素级验证标记 **未运行**。
2. **S09 bearing 旋转实测**（viewport 对齐合同在真 map bearing 下的视觉
   验证）——合同以 icon-rotation-alignment=viewport 显式声明 + 文档化；
   未做浏览器实测。
3. **WeasyPrint 在生产镜像的可用性**——本机 venv 验证（70.0）；生产容器
   weasyprint 缺席时的降级路径复用既有 vector_pdf_unavailable 披露。
