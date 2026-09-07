# V3 新算法垂直切片模式（实现 agent 必读）

每个新算法 = 6 件套，严格复刻现有模式（参考切片：`sar.speckle_filter` / `remote.pca`）。

## 1. 实现 — `app/lib/geo_analysis/<domain>.py`
- 纯 numpy/scipy（可用 sklearn），零 IO、零工具层依赖。
- 顶部模块级 scale guard 常量 + 显式守卫：超限抛
  `ResourceScaleMismatch(估算值, 上限, correction_hint)`（app/lib/gis/scientific_errors.py）。
- nodata 语义：NaN 或显式哨兵数组；统计全部 nan-aware；全无效 → `NoValidObservations`。
- 确定性：所有随机用 `np.random.default_rng(seed)`，模块级 `_FIXED_SEED = 42`。
- 返回 `(dict[str, np.ndarray] | dict, meta)`；meta 含公式/假设/披露（中文，与现库一致）。
- 近似必须显式披露在 meta + 描述符 limitations，绝不静默。

## 2. 工具 — `app/tools/<domain>_tools.py`
- 在既有 `register_*_tools(registry)` 内加 `@tool(registry, name=..., description=..., tier=2, domains=[...], param_descriptions={...})`。
- description 结构：一句能力 → `\n何时用：…` → `\n何时不用：…` → `\n关键约束：…`（照抄现有风格）。
- 签名内联数组用 `List[List[float]]`/嵌套；先过规模闸 `_TOOL_ARRAY_MAX_VALUES = 4_000_000` 语义（照抄 remote_sensing.py）。
- `apply_contract("<contract_id>", {...})` 先归一化参数。
- 响应：`{"success": True, ...统计..., "array"/"values": .round(6).tolist(), "meta": ...}`，
  最后经 `_attach_science_evidence(payload, "<algo_id>", tool=<tool_name>, parameters_applied=..., input_facts={"feature_count": N}, warnings=..., diagnostics=...)`。
- 栅格规模相关算法加 `_backend_selection_diagnostic(algo_id, cells)`（remote_sensing.py:87 模式）。
- 若新文件：注册进 `app/tools/__init__.py` `_TOOL_MODULES`；优先塞进现有工具文件避免注册面膨胀。

## 3. 描述符 — `app/lib/gis/algorithms/<domain>.py`
- 追加到 `ALGORITHMS` 列表；id 用 `<domain>.<name>`（如 `remote.mnf`、`sar.coherence`）。
- 必填 VNext 字段：`algorithm_family`、`assumptions`（≤8×160ch）、`limitations`（≤8×160ch）、
  `crs_class`（栅格一律 RASTER_GRID）、`scientific_preconditions`（仅用词表已有 id：
  band_semantics_required / raster_band_required:N / min_numeric_samples:N / binary_field_required 等）、
  `numerical_tolerance`、`scientific_status`（新实现=VALIDATED 须有 conformance；探索性=EXPERIMENTAL）、
  `conformance_tests`（≤8 个真实 pytest node id——registry 会 AST 验证存在！先写测试再填）、
  `parameter_contract_ref`、`method_references`（仅用 method_references.py 已注册 id；需要新引用就先在
  该文件登记，格式照抄现有条目）、`random_seed_policy`（确定性=deterministic；seeded=fixed_seed）。
- 有真实双路径才声明 `backend_variants`（≤4，BACKEND_VOCABULARY 内）。
- `min_features` 是方法论样本下限（resolver 硬闸），勿与 BackendVariant 窗口混淆。

## 4. 契约 — 同文件 `PARAMETER_CONTRACTS`
- `ParameterContract(id="<name>_analysis", version=1, parameters=[ParameterSpec(...)])`。
- 工具签名参数名必须包含契约全部 required 参数（parity 门硬校验）。
- 枚举用 type="enum"+enum_values；窗口/档位用字符串枚举（"3"/"5"/"7" 模式）；单位用
  PARAM_UNIT_VOCABULARY（meters/kilometers/degrees/pixels/ratio/unitless/…）。

## 5. 能力 — `app/lib/gis/capabilities/<domain>.py`
- 仅当新算法确实构成新能力族才加 `CapabilityDescriptor`；能归入既有能力的映射过去。
- input/output artifact 类型必须是 artifacts.py 已注册 20 类（raster_surface/terrain_surface/
  stats_table/hotspot_result/point_feature_set/...——先查再写）。
- `purpose_template` 短语与 category 沿用域惯例。
- 禁止把同一语义拆成两个能力凑数。

## 6. 测试 — `tests/unit/lib/test_<topic>_v3.py`
- 手算黄金值（精确小 fixture）+ 性质测试（对称/单调/边界）+ nodata/退化 + 守卫 + 确定性重放。
- 参考 lib 对照（esda/scipy/sklearn）在可用处做 conformance（rtol 1e-8~1e-10）。
- 文件顶部不要 import app.tools（避免拉起重依赖；工具层测试单列或注明）。
- 每个 conformance test 必须真实存在并可独立运行：`pytest tests/unit/lib/test_x.py::test_y -q`。

## 全局禁令
- 不改中央文件（algorithm_registry.py 除 Phase 1 授权修复外）不动 `docs/science/ALGORITHM_CATALOG.md`（由脚本再生成）。
- 不新建第二套 registry/tool 面；新工具优先并入现有域工具文件。
- planned 能力必须诚实拒绝运行；绝不 fake-native。
- pytest 单测禁 wall-clock 断言；perf 相关标 `-m perf` 约定（本次不写 perf 用例，除非 count-based）。
