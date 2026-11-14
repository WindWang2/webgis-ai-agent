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
