# G40 Round-3 终审报告（评审对象 e459baf22）

## 逐项 verdict

**N1（P1）FIXED** — 四渲染面域一致，独立探针（非作者测试）全数通过：
- 导入域：`<svg viewBox="0 0 24 24">` 的 bar/circle 导入后保持 `M 2,4 / L 22,4`、`(cx,cy,r)=(12,16,4)`，`tile_viewbox=(24,24)`（`svg_import.py:866-870` root_matrix=None 属实）。
- Python pattern：dpi=1/scale=1 → `width="24.000"`、scale 组 `scale(1.000 1.000)`、body 坐标 2..22 原域；scale=2+dpi=2 → width 96、scale 组 4.000（周期与内缩放同步）。
- TS 孪生：临时 vitest 探针（已删）在 targetDpi=72 下 `width="24.000"`、`scale(1.0000 1.0000)`、body `M 2 4 L 22 4`；scale=2 → 48/2.0000。
- live：`rasterize.ts:146-158` `ctx.scale(w/tw, h/th)`、`w=round(tw·scale·ratio)`——viewBox 域图元直接映射，代码级一致（jsdom 无 canvas 后端，无法像素探针；比例式封闭无歧义）。
- swatch：Python `s=min(48/24)=2.000` 实测；TS 同式。内容尺度：20 单位 bar 占 24 周期的 83%，不再微点。

**N2（P1）PARTIAL（主修复真实，一处残留）** — `_emit` 的 `transform_of` 已彻底删除（grep 0 引用），`_walk` 组合改为 `parent∘own`（`svg_import.py:771-778`）。20 项独立探针 17 项 PASS，含全部非交换组合：own-translate-under-parent-scale=12（错序得 11）、own-scale-under-parent-translate=12、嵌套链=16、rotate(90)∘translate=(0,6)、transform 列表序=4、平移祖先回归=11、tile 内 g-scale=6,6；rect→polygon、ellipse→path（右端点起笔）为探针锚点假设误判，实现正确。**残留（新 finding R3-1，见下）**：use 的 x/y 隐式平移被组合在最外层（`svg_import.py:685` `_mat_mul(translate(ux,uy), m)` = x/y∘(ancestor∘own)），SVG 语义应为 ancestor∘x/y∘own。元素局部序（transform 属性先、x/y 后）与无祖先/平移祖先场景全部精确（P9=15,25、translate 祖先=13,0），作者测试恰只覆盖这些可交换情形。

**N7（P2）FIXED** — HEAD 自跑 `tests/quality/test_api_compatibility.py` + `tests/test_api_docs_drift.py`：**15 passed**。

**N3 FIXED** — line 帽独立探针：`x2=80000` → `SVG_COORD_OUT_OF_RANGE`；`use x="abc"` → `SVG_BAD_NUMBER` typed 拒绝（try/except 吞异常已移除，不再静默归零）。

**N4 FIXED（代码级）** — 兜底 `fallbackSize=max(8, min(256, round(d)))`（`rasterize.ts:239-241`）；非兜底路径 size 上界 `cap=min(256, d·40)`，d=512 时所有格点 size≥512>cap 必落兜底 256。docstring 已删"≤0.3°"不实承诺，改为诚实披露大间距量化退化。

**N5 PARTIAL** — patternTransform 三面全撤（Python/TS 孪生探针均无 `patternTransform`，live 分支本就不旋转）；Python 背景矩形改全周期 24×24 且在 scale 组外（探针实测）。**残留（R3-2）**：TS 孪生 svg_tile 的 bg 仍是 spacing 方形且在 scale 组内（vitest 探针实测：24 tile + bg → `<rect width="12.000" height="12.000">` 在 `scale(1)` 组内）——盖局部 + 被 dpi 双重缩放，与 Python/live 分歧。commit message 宣称“背景矩形全周期尺寸”双端，实际只改了 Python。

**N6 FIXED** — 独立对账（非作者测试）：9 个 builtin digest Python↔TS 全一致、`angle2_deg` 已从 TS 消失、`test_n6_builtin_ts_twin_no_drift` 漂移锁在套件内通过。

**N8 FIXED（代码推理，无自动化测试）** — `runtime.ts:1671-1672` 合并语义正确且无泄漏/无重复：重挂路径先 `removeLayerSafe`（1445 防御式 + recompile 上游），该函数**删除**注册表条目并级联删除全部已登记 id（1694-1705）→ 重新 add 时 priorSubs 只含本轮 texcat id，不会累积；`setSpec` 全清（360）；texcat 与 rep id 后缀模式不同不会碰撞。遗留：无该组合的前端回归测试（沿袭 Round-2 备注）。

## 新 findings

- **R3-1（P2，N2 残留）use x/y 在非平移祖先下组合次序错误——静默几何错位**。rsvg 实渲染 oracle：`<g transform="scale(8)"><use href="#p" x="3" y="3"/></g>`（#p = 0..1 线段）渲染于 x 24..32、y≈24；实现烘焙出 `M 3,3 L 11,3`（错域约 8 倍）。触发条件：`<use>` 带 x/y 且祖先链含 scale/rotate（图标 sprite 网格套缩放组的常见形态）。修复极小：x/y 平移需组合在祖先链之内（walker 侧先 own 后 x/y 再接 parent，或向 `_emit` 分开传递），并补 rsvg 同款 oracle 测试。平移祖先/扁平场景现值精确，不受影响。
- **R3-2（P3，N5 残留）TS 孪生 svg_tile 背景矩形**：`mapspec-to-svg.ts` svg_tile 分支仍用 spacing 尺寸 bgRect 且置于 scale 组内（双 dpi 缩放 + tile_viewbox>spacing 时只盖局部）；应改为全周期、组外（与 Python `bg_full` 对齐）。仅 bg_color 非透明的导入 tile 在前端静态 SVG 导出可见。
- 微备注：runtime.ts N8 注释缩进错位（纯观感）；`use`+scale 于 librsvg 有渲染怪癖（`use_own.png` 空输出），但同文档无 transform 的 use 正常，不影响上述 oracle 结论。

## 自跑结果（全部自跑，非转述）

- 后端：`tests/cartography/test_g40_symbol_assets.py` + `tests/unit/test_g40_cartographic_asset_catalog.py` + `tests/cartography/test_g40_publication_readback.py` + `tests/quality/test_api_compatibility.py` + `tests/test_api_docs_drift.py` → **67 passed**。
- 前端：`npx vitest run lib/cartographic-assets/contracts.test.ts` → **15 passed**；`npx tsc --noEmit` → **0 错误**。临时探针文件已删除，worktree 内仅剩作者的 planning 文件改动。

## 终结论：GO-WITH-FIXES

Round-2 两项 P1（N1、N2 核心）与 N7 修复全部真实成立，N3/N4/N6/N8 干净落地，全量指定回归绿。但本轮按任务书指定的“use m 组合次序”复核发现 **R3-1**：N2 的 use 分支在非平移祖先下仍有静默几何错位（rsvg oracle 实证），属 N2 同族、修复为 2 行 + 1 个 oracle 测试——建议在提 PR 时附带此修复（或 PR 首个 commit）；R3-2 为一行孪生对齐可同批或跟单。若团队判定 R3-1 触发面（use+x/y+缩放祖先）足够罕见，其降级为跟单 ticket 后本 verdict 即为 GO——除此之外无任何阻塞项。