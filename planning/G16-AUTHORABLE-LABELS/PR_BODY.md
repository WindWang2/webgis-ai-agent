# feat(labels): G16 要素级标注人工控制与混合几何避障 — 覆写/固定/移位/文字/恢复自动 + 线面障碍（schema v2.1）

**任务号 G16-AUTHORABLE-LABELS**。Base `816705218`（PR 前复核 origin/master 未移动）。用户可以对地图说「保留这三所学校的名称、把重叠的两个分开、隐藏重复名、别压主干道和行政边界」——鼠标拖动、键盘微调与 Pi 指令操作**同一份持久化标注状态**，导出保留这些决定。

## 用户获得什么

- **要素级覆写持久化**：隐藏/固定/手工偏移/显式文字/受控引线/优先级，按稳定要素身份存进 `layer.label.authoring`（≤256/层），刷新仍在；reset 按 visibility/position/text/all 四作用域互斥（解除隐藏绝不清文字/位置）。
- **拖拽与键盘**：`编辑标注` 工具 → 点标签拖动（本地预览，pointerup 一笔 revision，绝不每 move 一次提交）、方向键 ±0.25em（Shift×4）、Enter 提交 / Esc 取消零写入；「修改标签文字」面板明确不改数据字段。
- **线面避障（geometry_v1 显式策略）**：文字避让实际 stroke 宽的线墨迹与面边界墨迹（逐段 AABB，长线不整线 bbox）；面填充不拦区内标签；显式禁入面；页面组件遮挡；自身线/面墨迹不拦自身标签；跨层绝不豁免；预算公平交错 + 截断披露。
- **user-wins**：固定标签先于全部自动项放置、恒不被自动解冲突移动；冲突如实报告（可选解锁，不偷偷动）。迟到工具结果经 CAS 不能覆盖用户最新编辑。
- **三端一致**：固定/隐藏/文字覆写/偏移在 live MapLibre（auto + pinned + callout 子层）、SVG、PDF（同一 SVG）、PNG（live 画布）保留；偏移 canonical 单位 = em —— DPI/缩放不改变相对位置语义，绝不持久化绝对屏幕像素。
- **Pi 同源**：`webgis_label_inspect/author` 与 UI 同一意图/CAS/撤销栈；「为什么这个标签没显示」只给**有来源**的理由（hidden_by_user/below_scale/field_missing/stale_override 带 authoring_policy/data/identity 源），placement 族显式 not_evaluated —— 离线预测不冒充 live 观察。

## 实现边界与诚实披露

- **兼容**：无 authoring 键 / 无 obstaclePolicy 的旧 spec 输出**逐字节不变**（实测对拍 + golden corpus 锁定）；schema 纯 additive（v2.1；2.0 号位留给 G15 #2076，恒等上船，两种合并顺序均收敛）。诊断词表 +5 已登记 EMITTER_REGISTRY。
- **失效**：orphan（目标要素不存在 → 不应用、绝不漂移到别的要素）、stale（数据 revision 变化但身份未断 → 照常应用 + 待复核披露）、unverified（ref 源不可复核）。不可见/zoom 门外层不误报。
- **身份**：FEATURE_ID_KEYS 链 + `_wfid`（G14）同源消费；`1` ≡ `"1"`（str 归一，表达式侧经 `to-string`，真实 style-spec 求值器验证）；裸数据集无 id → 拒绝持久覆写。
- 已知限制：live callout 引线仅 Point 锚；拖拽 px→em 用 spec label.size（非 zoom 缩放后字号，em 语义保持成比例）；覆写 `origin` 保留首作者；live 与离线求解不承诺逐像素相等（统一的是身份/文字/优先级/锁/隐藏/单位语义）。
- 有界：覆写 256/层、ops 200/笔（原子 —— 任一拒绝整笔失败附 per-op findings）、文字 120 code point（非 BMP 不半截）、偏移 ±24em、段 64/要素 320/层、禁入面 96 格（放大保铺满）、求解障碍 200（公平交错 + 截断披露）。

## 已执行验证（本地真实执行）

- 后端：`pytest tests/cartography/ tests/unit/test_label_authoring_*.py` → **3482 passed**（唯一失败 = `test_catalog_docs_fresh` theme-palette 漂移，**master 干净基线复现**，与本分支无关）；mutation parity corpus +2 case golden 再生成。
- 前端：`vitest run` → **5090+ passed**（唯一失败 = `tests/e2e-lanes` j13，干净树复现，G14 #2075 同报）；`tsc --noEmit` 0 error；`eslint --max-warnings 0` 全绿。
- 求解器双孪生：golden corpus +4 authoring fixtures 差分锁定；真实 `@maplibre/maplibre-gl-style-spec` 求值器验证数值身份匹配（含原缺陷对照组）。
- 导出实拍：固定/隐藏/覆写文字/callout `<line>`/障碍避让在 SVG 内容断言；96/300 DPI 偏移比例一致性。
- 性能（同机）：1800 点 + 40 长线 + 21 覆写 dense 图 legacy 96ms / geometry_v1 88ms（障碍抽取有界，无可测开销）。
- 映射矩阵：`planning/G16-AUTHORABLE-LABELS/TEST_MATRIX.md`（验收场景 → 测试逐条）。

## 独立评审

未参与实现的独立 reviewer 全量评审（R1：2×P1 + 10×P2 + 9×P3，含真实复现）→ 全部 P1/P2 + 7×P3 修复于 `bf47d432c`（真实接线 e2e、求值器级回归、UI 取消/双计回归）→ 同一 reviewer 复审结论见 planning/G16-AUTHORABLE-LABELS/REVIEW.md。

## 与其他 PR 的关系

- 消费不修改：G14 稳定身份（#2075）、G13 能力面（#2073）、数据刷新（#2022）、MapSpec 撤销（#2035）、Pi-only（#2036）。
- **G15 #2076**：schema 版本号与 `mapspec_schema.py`/carto_ir 语料钉存在**文本冲突**（语义可并：2.0=G15、2.1=本线，双方恒等 upgrader 任一顺序收敛）；`MapSpecLayerLabel`/`MapLabelConfig` 与 `MapSpecFrame` 各改各的模型。
- **#2032 UI 重设计**：接触面 = `map-toolbar-hud.tsx`（一个按钮组）、`map-panel.tsx`（1 行挂载 + 点选门控分支）、`agent-run-panel.tsx`（工具名映射 +1 键）—— 均最小加法。
- ADR-0289（G15 分支占 0288）；前端新增 `@maplibre/maplibre-gl-style-spec` devDependency（仅测试用真实表达式求值器）。

## 迁移/回退

纯 additive：旧 spec（无新键）行为逐字节不变；回退 = revert 本 PR（无数据迁移；authoring 只存在于 spec 内嵌 `layer.label.authoring`，随 spec 版本管理/回滚）。schema v2.0 恒等上船登记保证 G15 先合/后合都收敛。

## 未测限制

- PDF 未做像素级打开验证（PDF 由同一 SVG labels 组经 svg2pdf 生成，SVG 内容断言 + 既有 PDF 回归覆盖）；浏览器端 label-editor 真实指针旅程为组件级测试（jsdom 事件序列），未跑 Playwright 旅程。
- live 与导出的自动标签布局差异（MapLibre 逐帧贪心 vs 离线确定性求解）保持既有口径 —— 用户固定项在两端一致（em canonical）。
