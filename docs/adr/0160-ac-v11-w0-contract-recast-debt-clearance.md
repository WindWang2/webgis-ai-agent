# ADR-0160: V11 W0 — 契约重铸与债清（C2 IR、标注排版单点化、孤儿接线、兜底常量单点、门禁冒烟、44 码契约矩阵）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/v11-master（W0）
- 关联: ADR-0150~0159（V10 十线）、ADR-0081（整饰摆放语义）、ADR-0120/0126（导出孪生/标注避让分档）、ADR-0157（出版版面描述）、ADR-0159（质量基座）、cartographic-closed-loop.md

## 1. 背景与靶心

V10 十线（PR #1257–#1269）交付后系统存在 11 个结构缺口（任务书 §1.2）。W0 承担其中
可先行清偿的债：G1（组合选择纸面）、G3（三套整饰渲染无共享 IR）、G4（标注双实现）、
G7（ts_projection/golden_diff 孤儿）、G8（兜底字面量散落），并为 G10/W3、G5/W4 铺
契约地基。S1 只读债扫描（`docs/dev/ac-v11-debt-scan.csv`）与首轮基线
（`docs/dev/ac-v11-review-memo.md`）是本 ADR 的证据底座；对本任务书的修正按 §0.5
「以代码为准」登记于复核纪要 §5/§6。

## 2. 决策一：C2 `LayoutDescription` IR v2（契约冻结）

- `app/lib/cartography/layout_description.py` 升级为双层：既有 v1（页面级
  publication layout，`PUBLICATION_LAYOUT_VERSION=1`，ADR-0157，不动）之上新增
  **组件级共享版面描述 IR**（`LAYOUT_IR_VERSION=2`）：`canvas` / `layers`（z 升序）
  / `components[]`（kind×12、role×3、frame{anchor 九宫格,z}、constraints、style token、
  typography{wrapMode: none|cjk_char|latin_word|auto}）/ `degradations`。
- 前端镜像 `frontend/lib/layout/ir.ts`（`buildLayoutIr`/`validateLayoutIr` 逐语义对拍）。
- **同层并列合法**：z 不要求唯一，同层内序由 id 字典序稳定排（跨语言 tie-break 契约）；
  `validate_layout_ir` 只要求 z 为整数。
- parity 骨架（W0 定稿、W6 消费）：`tests/cartography/golden_corpus/layout_ir/basic.json`
  由 Python 权威实现冻结，pytest（`test_layout_ir_golden.py`，13 例）与 vitest
  （`ir.golden.test.ts`，5 例）双端消费同一 fixture。IR 承载决策不承载像素；三渲染器
  （React DOM / canvas / SVG）在 W6 全部改为从 IR 渲染，渲染器等价性测试 W6 落地。
- 载荷有界：组件 ≤32；渲染器对非法 IR fail-closed（枚举/出界/重复 id 拒绝）。

## 3. 决策二：标注排版原语单点化（G4，`label_typography.py`）

- 8 组重复符号（`_is_cjk`/`estimate_label_box`/`_keep_upright`/`_overlaps`/
  `_inside_viewport`/`_Grid(_Index)`/`_corner_box`/`_centered_box` + CJK 表 +
  `DECLUTTER` 表 + 文本常量 + fit/wrap）收敛到 `app/lib/cartography/label_typography.py`
  单一实现；`label_engine.py` 与 `label_collision.py` 改为薄适配层（公共名 re-export，
  行为零变化；两个旧模块的公共 API 与 golden corpus 全部保持）。
- **keep-upright 双语义诚实登记**（W4 C3 统一前不合并）：
  - `keep_upright`（引擎语义，双归一，输出恒 (-90,90]）——引擎沿线标注不变量；
  - `keep_upright_export_twin`（孪生语义，单次 fmod）——与 TS `keepUpright` 逐分支
    等价，被 parity corpus **冻结**（`line_keep_upright.json`：135°→315.0）。单侧改动
    会打破跨孪生 parity，统一动作归 W4（届时 TS 同步改 + corpus 重生成）。
- 依据：任务书「适配层过渡、禁止一次性删除」（§0.5）。

## 4. 决策三：孤儿接线（G1/G7）

- **composition_selection（G1）**：W0 定稿调用契约 `composition_alternatives_payload`
  （唯一许可调用形态：结构化 `TaskCartographyContext` 入、有界化载荷出、无 query 字符串）
  + golden fixture（`golden_corpus/composition_selection/school_distribution.json`）+
  契约测试（`test_composition_selection_wiring.py`）。W5 将其接入 component_composer
  主链路时必须经此契约（备选版面 ≥3 候选 + 评分）。
- **ts_projection（G7）**：新增 app 侧可编程钩子 `projection_drift()` /
  `regenerate_projection()`（幂等）；启动自检接入 `registry_validation` —— 生成物
  「存在但漂移」启动即报；「缺失」不判（打包部署无 repo 前端树，不可判 ≠ 漂移，
  不误报）。既有 byte 级漂移测试（ADR-0120）保持。
- **golden_diff（G7）**：新增 harness 层校验入口 `app/lib/harness/golden_validation.py`
  （`validate_golden_pair`：像素 diff + 取色点可分性 + 可选墨量带下限；不抛异常、
  reason 化）+ `self_check_golden_diff`（内存合成 PNG 自检，不落盘不起浏览器）注册进
  启动自检。校验常量仍单点在 golden_diff 模块。

## 5. 决策四：兜底常量单点（G8，`defaults.py`）

- `app/lib/cartography/defaults.py`：`DEFAULT_CLASS_COUNT=5`、
  `DEFAULT_CLASSIFICATION_METHOD="quantiles"`、`DEFAULT_PALETTE="YlOrRd"`、
  `DEFAULT_CATEGORICAL_PALETTE="Set2"`。业务代码兜底位全部改引常量
  （classify/cartography_service/tools/templates/thematic_spec/model_library.default_k/
  bivariate/composite_builder/template_schema，共 8 文件 15 处）。
- **圈定边界**：palettes/themes 注册表与 template_schema 种子 payload 中的字面量是
  权威内容本身，不属兜底；方法名分发比较（`method == "quantiles"`）是词表使用，豁免。
  selfheal_actions 的命中是 docstring 线间约定示例，不动。
- grep 断言（`test_defaults_single_source.py`）：清单文件内兜底形态（签名缺省/`or` 兜底/
  `get(key,default)`/裸 `k = 5`）归零，新违规即红；W3 阈值策略扩展清单。
- 常量值即既有对外缺省值，改动须走 ADR 并同步 golden（测试锁值）。

## 6. 决策五：门禁 --smoke（W0.5）

- `quality_gate_local.sh --smoke`：①覆盖率闸→仅 lane 红绿（不计量不设限）；
  ②golden→冒烟默认不起浏览器（`FORCE_BROWSER=1` 开启后仅验前 2 个 pr-blocking
  场景）；③ratchet→最近 3 次运行小窗口信号；④趋势→仅控制台渲染近 3 次。
  四个脚本各自具备 `--smoke`。**冒烟不伪造全量结论**：完整闸仍以无 `--smoke` 形态
  为准，PR 证据必须来自完整形态。

## 7. 决策六：44 码契约矩阵与 not_evaluated 化解表（W0.6）

- 冻结契约 `docs/dev/ac-v11-contracts/semantic-checks.v1.json`：38 大写码 + 6 点分码
  = 44；`CartographyCheck.to_dict` 键集与 status/severity 枚举冻结。
- 契约测试（`test_semantic_checks_contract.py`）：源码扫描 ↔ 契约 JSON 逐名对拍
  （静默加码/丢码即红）；blocking 三码（INVALID_SOURCE_REF / INVALID_STOPS_COUNT /
  NON_INCREASING_STOPS）定义于 lifecycle_engine（复核修正：不在 semantic_checks），
  与 44 码族不相交；缺证据永不读作 pass（三种 rollup 形态锁定）。
- **not_evaluated 化解表**（9 个出口码逐条）：VISUAL_OVERLAP→W7（本地确定性视觉判据
  + VLM，fail-closed 兜底）；STYLE_EXPRESSION_SUPPORT→W2；OPACITY_VALIDITY/
  GEOMETRY_LAYER_TYPE→W2；RESULT_VISIBILITY/RESULT_DATA_PRESENCE/BBOX_VALIDITY/
  CRS_EVIDENCE→W3（count/bbox/CRS 源元数据契约）；RESULT_MAP_PROVENANCE→W1。
  plan 落地后仍缺证据的场景（源真无 CRS/bbox/provenance）保留 not_evaluated 尾态，
  rollup 记 warning —— 禁止伪造 pass。

## 8. 首轮基线与本波验收对照

- 基线（本机 Windows/py3.13）：cartography 48.55%（8801 stmts）/ 合并 47.85% /
  lane 794 passed 21 skipped / ratchet 空库无劣化 / 门禁在覆盖步拦截（floor=50）。
  覆盖闸脚本头注的 50.1% 为作者环境值；W0 新增测试将覆盖抬回 ≥50（M1 门禁以
  floor=50 跑绿为准，只升不降纪律不变）。
- 验收对照：双实现收敛（§3）✅；IR 冻结 + parity 骨架（§2）✅；grep 断言（§5）✅；
  门禁可执行 + 冒烟形态（§6）✅；44 码契约全绿（§7）✅；孤儿接线（§4）✅。

## 9. 风险与回滚

- 行为零变化是本波红线：label 双语义、IR v1 不动、常量值冻结均有测试锁定；
  任何 golden 漂移即回滚。里程碑回滚点：tag `ac-v11-w0`。
- 本机覆盖基线与作者环境差 2 个百分点 —— 如实记录不调 floor；后续波次只升不降。
