# G34 PLAN — 专业纸面版式与图册编排工作台

## 用户闭环（先打通一条纵向链，再扩数据类型与失败面）

> 用户打开 Studio → 进入「纸面版式」→ 选 A4 → 把标题框设为 x=15mm/宽=180mm、图例改两列 → Pi 或 UI 提出同一变更 → 预览看到真实纸面（含安全区、地图槽、字体状态）→ 应用（一次 CAS）→ 保存重开仍在 → 导出 PDF/SVG/PNG → 独立 oracle 从产物量回 15mm/180mm、字号、行数、图例条目、比例尺。

## 分层与所有权

```
┌─ UI  frontend/components/workbench/print-layout-panel.tsx
│      frontend/lib/print-layout/{contracts,state,api,units}.ts
│      frontend/components/sidebar/map-studio-tab.tsx（薄 launcher）
├─ HTTP app/api/routes/print_layout.py（owner 校验；UI 预览/草稿）
├─ Pi   app/tools/print_layout_tools.py（ToolRegistry → webgis_execute/registered surface）
│        └─ 共用 ↓
├─ 领域 app/services/print_layout_authoring/
│      units.py fonts.py legend.py template.py validate.py operations.py
│      pages.py compile.py service.py
├─ 契约 app/lib/cartography/print_layout_schema.py（print_layout.v1）
│      MapSpecLayoutConfig.print_layout（additive, extra=forbid）
│      SetLayoutBody / SetLayoutIntent / _handle_setlayout / _codec_set_layout
│      ts_projection → types.generated.ts
└─ 真实绘制（薄适配，不重做算法）
       app/lib/cartography/publication_ir.py  PageLayoutIR.print_layout
       app/services/publication_export.py     compile_publication_page / assemble_pages_pdf / _svg_to_page_html
       app/services/mapspec_to_svg.py         compile_*_detailed / project() / _render_chrome_groups
       app/services/mapspec_svg_chrome_layout.py  _render_title_chrome（typography）
       app/lib/cartography/svg_marginalia.py  render_legend_box_authored（分栏/续排）
       app/services/mixed_map_export.py      三分支透传 plan
       app/services/atlas_batch/{page_compiler,planner}.py  页级 plan + fingerprint
```

**硬纪律**：opt-in。旧 spec 无 `layout.print_layout` → 所有既有绘制路径与字节完全不变（回归 oracle 锁定）。
不改 G15/G25/G16/G26/G27/G33/G35 所有权；不建第二 MapSpec store / undo 栈 / event bus / 证据库 / frame runtime。

## 工作包 → 迭代顺序（每轮一个可检验 Done）

| 轮 | 工作包 | Done 条件（可证伪） |
|---|---|---|
| L1 | WP01 + WP02 + 单位 corpus | 能力矩阵落盘；`print_layout.v1` 模型可 validate/reject 未知版本；`set_layout` additive 写入并随 map-state 往返；旧 spec 字节不变 |
| L2 | WP03 + WP11（核心纵向链） | A4 页 + 标题 x=15mm/宽=180mm → 真实 SVG `<g>` 几何 oracle 在 96/150/300 DPI 下都量回 15mm/180mm；PDF `@page` mm 一致；无 plan 时字节与 base 相同 |
| L3 | WP05 + WP06 | 长中文+拉丁长词真实 lines/overflow；缺字/weight fallback 明确；两列含 nodata/范围外零 slice；三数量级比例符号共享比例；PDF 字体不被全局 CSS 覆盖 |
| L4 | WP04 + WP07 | 三组件对齐/等距一次原子 + undo 全恢复；锁组件不动；模板应用前列覆盖、复位只清本领域覆写、动态文本缺值可见 |
| L5 | WP08 + WP03 地图槽 | 三页图册重排/关页后标题与数据绑定不串页、目录/页码/总数按最终清单；map slot contain/crop 有效 extent 可查且非等比拉伸不出现 |
| L6 | WP09 + WP10 + Pi 旅程 | Studio 薄入口 + 独立面板四态；Pi 标准工具经真实 subprocess + 生产扩展 + dispatch + 本域服务完成「标题两行/图例两列/保留锁定比例尺/预览后导出」 |
| L7 | WP12 + review/fix/rereview | 取消/字体缺失/导出依赖缺失终态；独立 reviewer 无 P0/P1；修复复审；合流最新 master；push + PR |

## 资源与失败面

- 统一锁 `/home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock`；重阶段（Pi 构建、真实 PDF、全量测试）取锁；默认 `-j2`、pytest 单进程 `--no-cov`、Vitest 1–2 workers、Playwright 1 worker、`OMP_NUM_THREADS=1`。
- 独立环境：端口 3340、临时目录 `planning/G34-.../evidence/`、独立 `DATA_DIR` / Redis 前缀 `g34:` / DB schema `g34_*`；不杀他人服务、不 flushall。
- 失败先分类（新增/既有/环境）；「既有失败」必须在同条件干净 base 最小复现，不能凭文件未修改推断。
- 不删测试、不放宽断言、不随意刷 golden；契约确变才按生成流程更新并说明兼容性。
- 真实验证层级分别记录：Pi 子进程 / 后端 / 浏览器是否同链联跑；缺任一层保留 `未测/阻塞`，不把分段通过标成全链通过。

## 明确不做

CMYK/ICC 印前认证、任意字体上传、任意复杂文字系统 shaping、第二套混合渲染引擎、route corridor/网格分幅新算法、G33 分类断点编辑、G35 媒体编码、修改 vendor/pi 或 Pi RPC 内核、修改 CI/审批配置。
