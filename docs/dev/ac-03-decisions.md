# AC-03 决策日志（docs/dev/ac-03-decisions.md）

逐条记录本线的关键实施决策（含被否方案），配合 ADR-0152 阅读。
日期：2026-09-13 · 分支 `adaptive-cartography/03-adaptive-symbology` · 基线 `09d839d3`

## 决策 1：ADR 编号 0152 维持不变

§0.3 要求确认 watermark 后使用 ADR-0152。实测 origin/master watermark = 0147，
0152 未被占用 → 按契约使用 0152（不前移，避免与并行线 0148–0151 潜在占用冲突）。

## 决策 2：环境按仓库文档装法搭建（偏离任务书 §0.1 一行）

`pip install -e .` 在本仓不可行：pyproject.toml 无 [build-system]/包发现配置，
flat-layout 多顶层包（app/perf/agent/…）导致 setuptools 拒绝。README/docs 的
标准装法是 `pip install -r requirements-dev.txt`（tests 从仓库根运行，app 天然可导入）。
另：默认清华镜像 403，改用 `-i https://pypi.org/simple`。pytest-xdist 不在
requirements-dev 中，为 `-n 2` 门禁安装到 venv（不写入依赖清单——CI 无此需求时
单进程亦可全绿，见门禁证据）。

## 决策 3：模板计数以 SEED_TEMPLATES 实测为准 = 62（任务书正确，P0 初稿误为 63）

`grep '"id": "tmpl_'` 命中 63 处，其中 1 处为非列表项文本；`len(SEED_TEMPLATES)==62`
（28 thematic = 27 choropleth + 1 heatmap）。勘察文档与盘点均已按 62 口径修正。

## 决策 4：choose_classification 近均匀空池分支修正（复用 ≠ 照搬 bug）

其文档与 ADR-0073 均承诺"近均匀 → equal_interval/quantiles"，但空推荐池时
candidates=["natural_breaks"] 导致近均匀数据也落 natural_breaks。本线把它修正为
空池时候选 ["equal_interval","quantiles"]（既有测试只锁定有池场景，无破坏）。
这是复用前提下的最小修正，不是重写。

## 决策 5：色带可分辨上限采用「候选 × 最大可分级 k」联合裁决

初版先按基准 k 校验色带、再单独下修 k，出现两处缺陷：(a) 未夹逼的显式 k=10
让所有色带在探测期全灭、错误跳到 Viridis；(b) k 下修与色带落选互相掩盖
（separability 驱动的 k 分支成死码）。重构为：每个候选色带计算上下文门限内的
最大可分辨 k（k_cap），偏好序首个 k_cap≥3 者胜出，k 以 k_cap 封顶——偏好保留
最大化、每步留痕。RdBu（5 色）k=6 采样必重复 → 封顶 5，即"由 min_adjacent_delta_e
反推上限"的字面落地。

## 决策 6：ΔE00 门限 = 10.0（screen），由数据校准而非拍脑袋

实测 18 条 COLOR_PALETTES 在 k=5 中点采样下：screen ΔE∈[10.8, 30.6]（全过），
cvd_deuteranopia∈[8.2, 19.7]、cvd_protanopia∈[?, ?]，print 灰度 ΔL 除
Set2(0.054)/Dark2(0.049) 外全部 ≥0.06。取 10.0：screen 零默认行为变化；
CVD 下 ColorBrewer sequential 自然落选、感知均匀族入选——"CVD 优先"由阈值
涌现而非特判。projector +2（环境光冲淡）。全矩阵见
docs/dev/ac-03-palette-context-matrix.csv（18×6）。

## 决策 7：模板审美偏好「恒居次席」，族不匹配只披露不降序

初版把"推荐色带与数据类型族不匹配"（如 sequential 数据 + RdBu 推荐）降序处理，
导致 tmpl_th_temperature（quantiles/RdBu）的 RdBu 被换成 YlOrRd——模板观感
全变，超出生态改造的可接受面。改为：推荐色带恒居次席（仅显式更高），上下文
硬约束（CVD/print 可分辨门限）是唯一能移走它的力量，族不匹配仅在 reasons 披露。

## 决策 8：CVD/print 移走显式/推荐色带必须 rejected 留痕（09 线契约）

CVD 上下文把 colorblind_safe=False 色带（RdYlGn/Set1/Pastel1）后置时，若被
后置者是显式/推荐选择，必须写入 rejected[]（"非色盲安全——无障碍是硬约束"），
保证自愈动作清单完整可解释。

## 决策 9：heatmap_data 的接线走「family ↔ 规范色带 id」映射

热力族（classic/magma/viridis/thermal，透明首停靠点）不是分级色带注册表成员。
方案：HEATMAP_LEGEND_PALETTE_KEY 把 family 映射为规范 id 后作为显式偏好交引擎；
引擎换带时反向映射回热力族（无对应族→保留请求族+披露）。screen 上下文恒等
映射 → **默认渲染零变化**，而 CVD/print 上下文的校验能力真实接入。
heatmap 可分辨校验使用不透明停靠点（heatmap_legend_colors）。

## 决策 10：build_graduated_spec / build_thematic_style 签名默认 None = 裁决

原 quantiles/5/YlOrRd 签名默认是硬编码回潮的温床。改为 Optional[None]：
缺省 → resolve_symbology；显式传参 → 行为与 v1 一致（既有测试零破坏的关键）。
工具层（create_thematic_map 的 ThematicMapArgs）同步把 k/palette 默认改 None，
使 agent 缺省调用直接走裁决。

## 决策 11：log 策略只在「全正 + 跨 ≥4 个数量级 + 非重尾」时触发

重尾数据优先 head_tail（分类法本身吸收长尾）；log 针对"低偏度但跨数量级"的
形态（如线性铺满 5 个数量级的数据）。breaks 在 log10 空间分级后指数回原域，
legend 的 breaks/labels 保持在数据原单位。

## 决策 12：legend_spec v2 升级独立成 upgrade_legend_spec_v2

normalize_legend_spec 被语义检查/converter 多处消费，直接注入 v2 字段会放大
形状变化面。v1 归一语义保持不动（`v1 字段语义零变化`的强保证），升级路径由
新的 upgrade_legend_spec_v2 承担（内部先 normalize 再补 v2 缺省）。

## 决策 13：LISA 五色 / hillshade 灰度 / 光谱连续带 = C 级例外，登记不迁移

LISA 的 HH/LL/HL/LH/NS 五色是制图学固定语义色（非分布色带裁决对象）；
hillshade 必须灰度；光谱引擎连续带是光谱语义。三者登记进
scripts/symbology_audit.py 的 FILE_ALLOWLIST（附理由），杜绝后续误"修复"。
app/services/gis_harness/**（01/02 线领地）在 audit 中只报告不拦截。

## 决策 14：一致性矩阵的 5 入口等价实现方式

- h3_binning 的分类对象是网格聚合值：测试用独立坐标点 + stat_method='sum'，
  使网格值无损等于数据集值（1000 点对角线坐标会因 lng>180°环绕碰撞——改紧凑
  2D 网格布点后消除，测试内断言无损性防止回归）。
- heatmap_data native 守卫确定性拒绝多边形（#690），端到端同数据不可行；
  其裁决经由与工具共享的 _adjudicate_heatmap_palette 断言等价（语义字段全等）。
- apply_template 用中性探针模板（无任何偏好键）参与矩阵，带偏好模板的对照
  由 test_template_preference_contract 逐条锁定（59 条）。
