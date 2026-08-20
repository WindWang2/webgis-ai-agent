# Runtime Scenario 作者指南

本指南面向需要新增头照式渲染校验场景的贡献者。Runtime Scenario 是「声明式夹具 + 头照探针」的组合，不改 harness 代码即可扩展；本文与 `CONTEXT.md` 的 Runtime Scenario / Runtime Probe 词条、`docs/adr/0065-runtime-probe-gate-assertion-and-two-phase-promotion.md` 一致。

## 1. 夹具布局

```
tests/fixtures/runtime/<scenario-name>/
  mapspec.json   # MapSpec 输入（sources + layers + view）
  probes.json    # 探针声明与期望
```

- 目录名即场景名（`kebab-case`），扫描式收集，无需注册表。
- `mapspec.json` 须含非空 `sources` 与 `layers`；`sources` 支持 `geojson`（`inlineData` / `url`）、`vector`（`tiles`）、`raster`（`imageRef` + `bounds`），字段定义见 `frontend/lib/mapspec-compiler/types.ts`。
- `probes.json` 顶层：

```json
{
  "expect": "pass",
  "probes": []
}
```

`expect` 为 `pass`（默认）或 `fail`（负例，见 §4）。

> 新增目录后，契约测试会自动覆盖（`tests/unit/test_runtime_fixture_contract.py`），无需额外 wiring。

## 1.1 夹具资产约定与 `__ORIGIN__` 占位符

部分场景需要随编译产物一同服务的静态资产（首例为 MVT 矢量瓦片）。约定如下：

- 夹具目录除 `mapspec.json`/`probes.json` 外可携带**可读资产源**：MVT 场景为 `points.geojson`（`FeatureCollection<Point>`，含类别属性）。
- `mapspec.json` 中的 `vector` 源 `tiles` 使用 `__ORIGIN__` 前缀引用资产，例如  
  `"tiles": ["__ORIGIN__/tiles/{z}/{x}/{y}.mvt"]`。永不硬编码端口/主机。
- 生成的 `index.html` 在 `new maplibregl.Map` 前执行  
  `JSON.parse(JSON.stringify(window.__MAPSPEC_STYLE__).replaceAll("__ORIGIN__", location.origin))`，  
  将占位符解析为当前静态服务器的 `origin`，使瓦片请求落在与 `dist/` 同源的 `tiles/` 子目录。
- **Runner 组装**：heavy runner（`tests/unit/test_runtime_validator.py` → `app/services/runtime_validator.py` → `app/services/runtime_asset_assembly.py`）在**编译之后、校验之前**检测 `points.geojson`，用仓内 `app/services/mvt.py` 的 `encode_tile` 将 z0–z2 覆盖数据 bbox 的所有瓦片现场生成到 `dist/tiles/z/x/y.mvt`（`application/vnd.mapbox-vector-tile`，validator 的 `..` 遍历防线保持不变）。无二进制 check-in，编码器回归会诚实地使场景变红。
- **契约**：`tests/unit/test_runtime_fixture_contract.py` 的 `test_fixture_asset_declarations` 无浏览器即校验 — `points.geojson` 须可解析为 `FeatureCollection`，`mapspec` 须含 `type:"vector"` 且 `tiles` 以 `__ORIGIN__` 开头，且每个 `__ORIGIN__/...` 路径必有对应的声明资产（`tiles/` ↔ `points.geojson`）。
- 栅格 `__ORIGIN__/raster/...` 资产与 `raster-overlay` 场景由 #698 扩展，约定相同。

> 本节由 #697 引入；#698 将补全栅格资产说明，届时两票统一在关票评论注明。

## 2. 三类探针

### 2.1 `layer-exists`

图层是否在渲染树中（`map.getLayer(id) != null`）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `"layer-exists"` | 是 | 探针类型 |
| `layer` | `string` | 是 | 待断言的图层 id |

```json
{ "type": "layer-exists", "layer": "eq" }
```

### 2.2 `feature-count`

对已渲染要素计数（`map.queryRenderedFeatures(undefined, { layers: [id], filter? })`）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `"feature-count"` | 是 |  |
| `layer` | `string` | 是 | 图层 id |
| `equals` | `int` | 否* | 精确等于 |
| `min` | `int` | 否* | 至少不少于 |
| `filter` | `any` | 否 | MapLibre filter 表达式，透传给 `queryRenderedFeatures` |

`*` `equals` 与 `min` 至少填一个；二者共存时为 `count == equals && count >= min`。

```json
{ "type": "feature-count", "layer": "eq", "equals": 3 }
{ "type": "feature-count", "layer": "pts-layer", "min": 1 }
```

### 2.3 `pixel-color`

将经纬度投影到屏幕像素，取 5×5 窗口的主色，与期望色做 ±16/通道 容差比对。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `"pixel-color"` | 是 |  |
| `layer` | `string` | 是 | 仅用于报告上下文，采样本身不按图层裁剪 |
| `at` | `[lng, lat]` | 是 | 取色点（WGS84） |
| `expect` | `string` | 是 | 期望色，`#RGB` 或 `#RRGGBB` |

容差：`colorWithinTolerance(dominant, expect, 16)`，逐通道差值 ≤16 即算通过；窗口内无不透明像素则直接失败。

```json
{ "type": "pixel-color", "layer": "eq", "at": [60, 0], "expect": "#de2d26" }
```

**添加原则**：`pixel-color` 断言的是「paint 真渲出来了」，不用于样式侧 compile 期断言（样式正确性由确定性 cartography 门负责）。

## 3. 远色规则（pixel-color 必读）

同一场景内若有 ≥2 个 `pixel-color` 探针，其 `expect` 颜色必须**两两可区分**：总通道距离 `Σ|R1-R2|+|G1-G2|+|B1-B2| > 48`。该约束由 `test_pixel_color_pairwise_distinguishable` 强制，目的是防止近似色在容差与抗锯齿下误判为通过。

> 例如 `#2ca25f` 与 `#de2d26` 总距离  = |44-222|+|162-45|+|95-38| = 352，远大于 48，符合要求；而 `#ff0000` 与 `#fe0000` 距离仅 1，会被契约测试拒绝。

## 4. `expect: fail` 与 infra-clean 约束

负例场景（如 `fault-wrong-color`、`fault-missing-source`）用于证明「错误渲染逃不过门」：`expect: "fail"` 表示**期望校验变红**。

`tests/unit/test_runtime_validator.py` 的 `expect == "fail"` 分支会额外断言「红的必须是探针，而非基础设施」：

- `fatalError == null`
- `mapLoaded == true && mapIdle == true`
- `pageErrors == [] && consoleErrors == []`
- `valid == false`
- `probeResults` 中至少有一个 `pass == false`

**含义**：若地图根本没加载、或有控制台/页面异常，测试会直接失败——负例必须在浏览器健康的前提下靠探针变红，否则无法证伪「探针会红」这件事。

**作者启示**：构造负例时，优先选择「编译通过、地图正常加载、但探针因语义错误而红」的变体。例如引用不存在的 `source` id 会在编译期即 `INVALID_SOURCE_REF` 而根本进不了 headless，需改用空 `FeatureCollection` 或可控的空数据源，使 `feature-count` 的 `min: 1` 去红。

## 5. 本地单跑与调试

### 单场景执行

```bash
# 单个场景（headless，需本地已装 Playwright）
.venv/bin/pytest tests/unit/test_runtime_validator.py -m heavy -q --no-cov -k <scenario-name>

# 例如
.venv/bin/pytest tests/unit/test_runtime_validator.py -m heavy -q --no-cov -k fault-missing-source
.venv/bin/pytest tests/unit/test_runtime_validator.py -m heavy -q --no-cov -k interpolate-circle
```

`REQUIRE_BROWSER=1` 仅在 nightly lane 设置；本地缺失浏览器时会 `SKIPPED`，而非误绿。

### 产物定位

每次运行会在 `BASE_STORAGE_DIR/<session_id>/` 下生成：

- `compiled/` — 编译输出（`style.json` / `index.html` / `compile-report.json`）
- `compiled/runtime/`（即 `runtime_dir`）— 运行期证据：
  - `map.png` — headless 截图（与 `pixel-color` 采样同源）
  - `trace.zip` — Playwright trace（可用 `npx playwright show-trace trace.zip` 回放）
  - `report.json` — 完整报告，含 `probeResults`（`expected` / `actual` 便于定位色差或计数偏差）与 `canvas` / `controls` / `eval_scores`

`report.json` 的 `probeResults` 为排障首选：`feature-count` 的 `actual` 为真实计数，`pixel-color` 的 `actual` 为窗口主色 hex（如 `#aabbcc`）或「无不透明像素」「投影越界」等诊断串。

### 常见失败

| 现象 | 排查 |
|------|------|
| `fatalError: missing compiled index.html` | `mapspec.json` 未通过编译，检查 `compile-report.json` 的 `errors` |
| `mapLoaded` 为 false 且 `trace.zip` 显示长时间 loading | MapLibre 网络或 `url` 源 404；改用 `inlineData` 避免网络依赖 |
| `feature-count` 计数为 0 但期望非 0 | 数据源为空或 `filter` 过严；确认 `inlineData` 的 `features` 与 `view` 覆盖范围 |
| `pixel-color` 采样到背景色 | `at` 点不在要素上或要素被裁剪；确认坐标与 `view`，或增大 `radius`/`strokeWidth` 提高可采样性 |
| 契约测试报远色距离 ≤48 | 更换对比更强的调色板，参考 `tests/unit/test_runtime_fixture_contract.py` 的阈值 |

## 6. 晋升与门控（作者无需手动操作）

所有场景默认仅在 nightly lane 运行。连续 **10 次 nightly 绿** 且**单次 < 30s** 的场景，由维护者打 `runtime_pr` 标记并同步修改 PR lane 选择器与 release-gate DAG，之后才成为 PR 必绿门。作者本地 <30s 是预检，不计入晋升计数（详见 ADR-0065）。

## 7. 参考

- 夹具示例：`tests/fixtures/runtime/interpolate-circle/`（正例）、`tests/fixtures/runtime/fault-wrong-color/`（负例）
- 契约：`tests/unit/test_runtime_fixture_contract.py`
- 重型 runner：`tests/unit/test_runtime_validator.py`
- 编译器类型：`frontend/lib/mapspec-compiler/types.ts`
- 决策：`docs/adr/0065-runtime-probe-gate-assertion-and-two-phase-promotion.md`
