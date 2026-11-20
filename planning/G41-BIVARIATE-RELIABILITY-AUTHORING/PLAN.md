# G41 PLAN — 实现路线

用户闭环（先打通一条真实纵链，再扩类型/失败面）：

> 在已有矢量面层上，用户（或 Pi）选两个数值字段 → 看到同版本真实成对分布 → 设每轴方法/断点/矩阵（或绑定估计+误差/区间）→ preview（零 mutation）→ 一次 apply → 地图/矩阵或可靠性图例/数值证据/SVG·PNG·PDF 同源同版本 → undo / 重开可复现。

## 纵向链（WP01–WP11 合并为可验收包）

| 包 | 交付 | 依赖 |
|---|---|---|
| P1 | `app/services/bivariate_reliability_authoring/` 新模块：`errors`, `contract`, `snapshot`, `stats`, `classify`, `reliability`, `legend`, `expression`, `cache`, `service` | 现有 `bivariate.py`, `uncertainty.py`, `session_data`, `distributed_lock`, `apply_gis_mutation`, `UpsertLayerIntent`, `thematic_spec.spec_to_paint` |
| P2 | `app/tools/bivariate_reliability_tools.py` + `app/api/routes/bivariate_reliability_authoring.py` + `app/tools/__init__.py` / `app/main.py` 加性接线 | P1 |
| P3 | 前端 `lib/bivariate-reliability/{contracts,api}.ts` + `components/hud/bivariate-reliability-panel.tsx` + i18n(zh/en) + 挂载点 | P2 |
| P4 | canonical 图例适配器：live `legends.tsx`、Canvas `export-chrome.ts`、矢量 SVG `vector-svg-export.ts`、服务端 `svg_marginalia.py`/`print_layout_authoring/legend.py`/`render_scene.py` | P1 |
| P5 | 测试：独立手算 oracle 单测、服务/授权/CAS/ABA 集成、前端 vitest、两模式 Pi journey、SVG/PNG/PDF 生产链独立回读 | P1–P4 |
| P6 | 独立 review → 修复 → 复审 → 最新 master 兼容性 → commit/push/PR | P5 |

## 关键设计决定（先裁决，避免返工）

1. **不新建框架**：新领域模块只做本域合同/统计/度量/图例/服务；写路径唯一 `apply_gis_mutation(UpsertLayerIntent)`；CAS 用 `_cartographic_mutation_revision`；checkpoint/undo 由引擎负责；paint 投影经 `thematic_spec.spec_to_paint`（bivariate）与 `match` StyleMethod（可靠性桶，live 与服务端 SVG 都已支持）。
2. **强版本同代冻结**（WP03/B15/B37/B38）：`snapshot.freeze_source()` 在 `session_lock` 内取 descriptor(rev0) → 单次 `get()` 全量 deepcopy（权威存储保证读取对象不可变）→ 再取 descriptor(rev1)，`rev0 != rev1` 即 typed 拒绝；ABA 由**单调 generation** 检测（不靠前后 fingerprint 相同）；`rev` 缺失/为 0/descriptor 缺席 → `NO_STRONG_REVISION` 拒绝，不 warning 后发布。冻结内容计算 `content_fingerprint` 作为辅助标识（非保证）。preview 与 apply 都绑定同一 `(ref_id, content_revision, content_fingerprint, plan_digest, algorithm_version)`；apply 前再次核对 live revision，漂移即 `STALE_SOURCE`。
3. **显式端点合同**：`boundary_rule ∈ {upper_inclusive, legacy_lower_inclusive}`，缺省 `upper_inclusive`（G33/MapLibre `step`）。旧 bivariate 层从不被本服务改写，兼容回归 B04 断言旧分类不变。
4. **矩阵只支持 n=2,3**；自定义非对称色阵必须给满 n² 个 `#rrggbb`，否则 `INSUFFICIENT_COLORS`；n=4 或前端夹取一律 typed 拒绝（B13）。`swap_axes` 同时转置联合计数、色阵、类索引与图例（B11）。
5. **派生展示副本**：从冻结源深拷贝生成新 FC，逐要素加 `__g41_pair_class` / `__g41_rel_bucket` 等**独立命名**字段；若源已存在同名用户字段则 `DERIVED_FIELD_COLLISION` 拒绝；源已有 `__biv_class` 只记录、绝不覆盖（B14）。新 ref 经 `session_data_manager.store`，与 layer 同一笔 `UpsertLayerIntent(source_data={"ref_id": derived_ref})` 原子提交；CAS 失败/幂等命中即 `delete_ref` 清理，不留半引用。证据块保存 `base_source_ref` + `base_content_revision`，后续重新作者始终从**基准源**冻结，避免派生字段累积。
6. **可靠性语义**：`measure.kind` 闭合词表；SE/CI/PI 与估计同单位、`variance` 为平方单位且必须非负；`level` 仅 CI/PI 必填且 `0<level<1`，不自动填 95%；`low>high` 拒绝不自动排序；区间不强制包含 estimate（`allow_noncentered`）；`source=author_declared` 明确标记声明而非独立验证推断。缺度量证据 → `NO_UNCERTAINTY_EVIDENCE` + 可行下一步（B23）。无效/缺失误差 → 独立 `unknown` 桶，不按 0（B18/B24）。两源无稳定行对应 → 不按位置 join（B26，本任务只接受同一已授权源内已对齐字段）。
7. **视觉映射**：`match` StyleMethod 把离散桶映射到有界 opacity / strokeWidth，含显式 `unknown` 输出；`clamp|reject` 政策；min/max 归一是视觉映射，不叫置信度。fill 用 opacity，circle 用 strokeWidth；层型不支持的通道直接拒绝（不静默删第二通道）。
8. **canonical 图例单源**：`legend.py` 产出机器可验 JSON（矩阵格含区间/计数/颜色/方向；可靠性含估计图例+度量名/单位/level/方向/缺失）。所有输出适配器只消费这份 JSON；G34 仍决定位置/尺寸/字体。
9. **边界/资源**：要素数、类表、散点/直方图样本、派生 ref 字节、导出页均有硬帽；超限明确拒绝，不静默截断后称完整。统计单遍，大扫描不在写锁/event loop 内做；缓存键含源版本/两字段/单位/度量类型/全部参数/算法版本。
10. **不做的**：第二套 agent loop / DAG / registry / MapSpec store / 全局 ID / 撤销栈 / 事件总线 / 证据库；不新增 G40 纹理、G38 join 引擎、G39 栅格采样、相关/因果统计、Monte Carlo、公式语言、属性写回。

## 验证层级（诚实标注，不互相冒充）

- L1 纯函数 oracle（手算 expected，绝不调生产 assign 生成 expected）
- L2 服务集成（真实 session store + 真实 `apply_gis_mutation` + CAS/undo/授权）
- L3 前端 vitest（组件/图例/导出 chrome/SVG）
- L4 真实 Pi 子进程 + 生产扩展 + 生产 dispatch + 本域服务（stub LLM，主链不 mock）
- L5 生产 SVG 编译 → rsvg-convert PNG → weasyprint PDF，逐格式独立回读
- L6 真实浏览器 E2E（若环境可用，否则保留未测/阻塞）

缺任何层就写未测/阻塞，不把分开通过的两段自动标为全链通过。
