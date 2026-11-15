# G40 LOOP_LEDGER — 执行轮账本

## 轮 1（2026-11-15）：基线 + 排重 + 领域核心 + 出版 spike

- base=1276692737（fetch 后与快照一致）；worktree/分支已建（见 BASELINE）。
- 两探索报告（前端/后端）已消化 → DEDUP_MATRIX.md / PLAN.md 定稿。
- 已完成：
  - `app/lib/cartography/symbol_asset.py`：规范形 symbol_asset.v1（9 内置资产、
    typed 参数上下限、digest、anchor_offset、保守外接框）；烟雾测试通过。
  - `app/lib/cartography/svg_import.py`：受控 SVG 子集导入器（defusedxml、
    path 全命令展平、transform 数值展开、defs/use 循环检测）；S05–S08
    恶意输入矩阵手工验证通过（script/外链/css url/style/onclick/doctype/
    零 viewBox/NaN/未知 use/clip/text 全部 typed 拒绝）。
  - `app/models/cartographic_symbol_asset.py` + 迁移
    `g40_cartographic_symbol_assets`（head=0106；照 0098 先例）。
  - mapspec_schema.py：`symbol_asset` additive 绑定块（extra=forbid、
    category typed key、prev_paint_snapshot）；绑定/类别校验通过。
  - `app/services/cartographic_assets/catalog.py`：资产生命周期（照
    composition_assets 纪律：scope 404/403、版本不可变、retire 软退役、
    restore 新 revision、resolve 钉版本+digest 对账、BlobStore 内容、
    S18 引用扫描+删除检查）。
  - `app/services/cartographic_assets/binding.py`：inspect/preview/apply/
    unbind（单笔 UpsertLayerIntent CAS；锁由引擎 guard_intent_locks 处理；
    prev_paint_snapshot 快照/恢复；S20 CAS 冲突 409）。import 烟雾通过。
  - `app/lib/cartography/symbol_asset_svg.py`：SVG 投影纯函数（defs/use
    变换链、pattern（hatch/cross_hatch/dots/svg_tile）、swatch、障碍盒）。
- **关键 spike 结论（实证，/tmp/g40-spike）**：
  1. rsvg-convert 2.63.4：pattern+patternTransform+evenodd 洞+use 变换在
     **内在尺寸**渲染全部正确；但 CLI `-w/-h` 缩放与内在尺寸不一致时
     **pattern 内容丢失**（librsvg 回归；v2b vs spike2 实证）。
     → workaround：栅格化前改写 SVG 根 width/height 为目标采样像素
     （viewBox 不变），按内在尺寸渲染。3x 缩放实证通过（洞/纹理/旋转 use）。
  2. WeasyPrint 70.0（worktree venv 安装，系统 pango 1.58）：嵌入 SVG 的
     pattern（含 patternTransform）/use 变换/evenodd 洞全部渲染（像素
     计数实证：hatch 311px、tri 374px）。PDF 通道用 SVG 原生机制可行。
  3. MapLibre 6.10.0 .d.ts：addImage(id, image, {pixelRatio,sdf})@14622、
     setMissingStyleImageResolver@14632、hasImage@14693；无 addImagePattern
     （icon 与 pattern 共用 addImage 名称空间）；icon-image/fill-pattern
     均可引用注册图像。
- 下一步：mapspec_to_svg.py 消费（defs/use/pattern + 障碍 adapter + 出口
  链资产解析注入 + PNG workaround 落地）→ TS 孪生 → 图例 → API/工具/UI →
  测试 → review。

## 资源纪律执行记录

- venv：worktree 独立 `.venv`（uv，requirements.txt）；weasyprint 仅装进
  本 worktree venv（可选依赖，不碰共享环境）。
- 重阶段锁尚未触发（未跑全量测试）；后续全量/E2E 前取
  webgis-heavy-stage.lock。

## 轮 2（2026-11-16）：实现完成 + 测试 + 提交

- 实现全链完成（详见 INTERFACES.md）：后端 8 新模块 + 15 共享文件加性接线；
  前端 7 新模块 + 10 文件接线；i18n；ADR-0292；visual_variables texture→partial。
- 测试：后端 G40 40 passed + cartography 全量 3654 passed（1 既有基线 deselect，
  干净 base 复现）+ Pi 真实旅程 1 passed（10/10 verdict；vendor v1.5.2 pin 构建）
  + 出版回读 5 passed（真实 PNG/PDF 像素 oracle）+ 前端 13 passed + tsc 0 +
  ruff G40 文件全绿。unit 全量存在既有 test_service.py 同名收集冲突（干净
  base 复现；拆分跑通过）。
- 修复过程中发现并处理：mapspec store 读取 API 误用（改 G33 同款）、
  session_data 修订读取（同款）、诊断死码门（登记发射点）、TS projection
  再生成（SCHEMA_EXPORT_MODELS +3 类型）、librsvg CLI 缩放 pattern 丢失
  （实证 + workaround）、anchorOffset 默认值镜像、_export_pdf 签名漏参。
- commit 9abcd60bb（59 文件 +9801/-58）。PR_BODY.md 已写。
- 独立 reviewer（槽 3，未参与实现）已派出 Round 1 —— 等待报告。
- 待办：review→修复→复审→fetch 最新 master 复核→push→PR。

## 轮 3（2026-11-16）：Round-1 评审 → 修复 → Round-2 复审

- Round 1 报告：GO-WITH-FIXES（3P1/7P2/11P3）—— 关键 P1：DB 资产 live 不可
  渲染（F1）、导入 M 多坐标对静默丢几何+尾随 Z 丢失（F2）、live 纹理预旋转
  与 patternTransform 语义分歧（F3）。
- 修复 commit 83009c320：F1-F13/F17/F19 全部（详见 REVIEW.md 表）；F3 用
  有理角格点（tanθ=p/q 连分数逼近 → 构造性无缝 + 间距亚像素保真）。
- 复跑：后端 48✓ / cartography 3655✓ / Pi journey 1✓（28s）/ 前端 15✓ +
  tsc 0 / unit 针对集 831✓（5 失败=干净 base 既有）/ ruff✓ / types 再生成。
- Round 2 复审（reviewer 槽位复用，携 Round-1 报告）已派出 —— 等待结论。

## 轮 4（2026-11-16）：Round-2 复审 → N1-N8 修复 → Round-3 终审

- Round 2：GO-WITH-FIXES —— F1-F3 修复确认（F3 无缝性独立 oracle 验证），
  但发现两个 pre-existing 同级盲区：N1（svg_tile 域不匹配 → 导入纹理渲染
  为微点）、N2（transform 双应用 + 组合序颠倒）+ N7（OpenAPI 快照 2 红）。
- 修复 commit e459baf22（N1/N2/N7 必修 + N3/N4/N5/N6/N8 + use x/y typed）。
- 复跑：cartography 3659✓ / Pi journey 1✓（29s）/ 前端 15✓ + tsc 0 /
  API 快照+文档 15✓ / ruff✓；N1/N2/N3/N6 新增 oracle。
- Round 3 终审（reviewer 槽位复用）进行中。

## 轮 5（2026-11-16）：Round-3 终审 GO → 收尾

- Round 3：GO-WITH-FIXES —— N1/N7/N3/N4/N6 FIXED、N2 主修复成立；新发现
  R3-1（P2，use x/y 组合次序，rsvg oracle 实证）+ R3-2（P3，TS tile bg）。
  Reviewer 明示附带修复后即 GO。
- 附带修复 commit 7781081cc（R3-1/R3-2 + oracle 测试）。
- 最终回归：cartography 3660✓ / Pi journey 1✓（29s）/ 前端 15✓ + tsc 0。
- REVIEW.md 终结论：GO，可提交 PR。剩余：fetch master 复核 → push → PR。

## 轮 6（2026-11-16）：合流 + 终稿

- 合流 master 34f4e3670（#2117-#2122：G36/G37/G38/G39/G41 + 迁移头收拢）：
  additive 冲突 5 文件解决（main/tools/legend/panel/api-docs）；迁移改接
  bcf4e5b51f43 单头；快照+文档再生成（2ed97076a）。
- 事故与恢复：误弹共享库旧 stash（codex 遗留）→ reset+checkout 恢复，
  零损失（旧 stash 原样保留在共享库）。
- 合流后回归：Pi journey 1✓（31s）/ cartography 3660✓ / API 快照+文档 15✓；
  tsc 仅 2 个纯 master 既有错误（origin/master 复现，非本分支文件）。
- 状态：终审 GO + 合流完成 → push + PR。
