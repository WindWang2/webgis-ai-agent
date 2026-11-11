# G27 INTERFACES — 单一表达解析契约（实现者唯一依据）

## 0. 定位（避免"第二套 resolver"）

本模块 **不是** 新的 MapSpec 文档、不是第二套 visibility resolver、不是并行事实源。它是：

```
既有权威纯函数（scene_lod / scale_rules / label_plan / representation / visibility 语义孪生）
        ↓ 只读复用
ScalePortrayalPolicy（用户/Agent 显式、opt-in、有限词表）
        ↓ resolve_effective_scale_portrayal()
EffectiveScalePortrayal（有版本的运行时解释 + 有效档位）
        ├→ project_scale_portrayal_spec() → 既有 spec 字段（label.zoomBands / thresholds.maxFeatures）
        └→ to_bounded_dict() → UI / Agent / 遥测（有界）
```

- **MapSpec 仍是 desired-state 单一事实源**；解析结果是投影/解释，不落第二文档。
- **observed camera 不写 desired**：zoom 变化只调用本解析函数（纯读），`apply_mutation` 只在用户/Agent 显式修改 policy 时由既有链调用。
- **旧项目兼容**：spec 无 `scale_policy` 字段 → `mode="disabled"`、`applied=false`、默认渲染逐位不变（A01）。
- **unknown policy fail-safe**：词表外/非法 → typed 诊断 + `applied=false`，绝不静默当已应用（A08/A28）。

## 1. 模块 `app/lib/cartography/scale_portrayal.py`

### 常量（词表，跨语言锁定）

```python
SCALE_POLICY_VERSION = "g27-scale-portrayal/1.0.0"
SCALE_POLICY_MODES = ("auto_lod", "fixed_density", "disabled")
SCALE_POLICY_UNITS = ("zoom_level", "screen_px", "ground_m")
GEOMETRY_SCOPES = ("point", "line", "polygon", "raster", "unknown")
WEB_MERCATOR_CRS = ("EPSG:3857", "EPSG:900913", "EPSG:3785")
MAX_DIAGNOSTICS = 8
MAX_DISCLOSURES = 8
MAX_OVERRIDES = 8
MAX_ASSUMPTIONS = 8
MAX_EXPLANATION_BYTES = 4096
```

- `auto_lod`：按 `scene_lod.LOD_BANDS` 四档自动调标注密度/符号尺度/terrain 上限/抽稀预算。
- `fixed_density`：用户给定 `pinned_label_top_ratio`（可另给 `pinned_size_ratio`），LOD 不覆盖。
- `disabled`：不应用任何尺度策略（旧项目等价行为）。
- `unit` 只作**披露与适用范围校验**，不参与数值换算；`screen_px` 与 `ground_m` 不得混用（A14）。

### 数据模型（pydantic v2，`ConfigDict(frozen=True, extra="forbid")`）

```python
class ScalePolicyDiagnostic(BaseModel):
    code: str      # 稳定 reason code，如 SCALE.POLICY.UNKNOWN_MODE
    field: str     # 出错字段路径（"mode" / "priority_field" / "version" …）
    detail: str    # 有界人类可读说明（≤160 字符）

class ScalePortrayalPolicy(BaseModel):
    version: str = SCALE_POLICY_VERSION
    mode: Literal["auto_lod","fixed_density","disabled"] = "disabled"
    layer_id: str = ""
    hidden: Optional[bool] = None                  # 人工隐藏（等价 layout.visibility=none）
    pinned_size_ratio: Optional[float] = None      # 合法域 [0.1, 4.0]
    pinned_label_top_ratio: Optional[float] = None # 合法域 [0.0, 1.0]
    priority_field: str = ""                       # 必须存在于 available_fields（A27）
    unit: Literal["zoom_level","screen_px","ground_m"] = "zoom_level"
    geometry_scope: Literal[...GEOMETRY_SCOPES] = "unknown"

class EffectiveScalePortrayal(BaseModel):
    policy_version: str
    applied: bool                    # 策略是否真实生效（A28）
    effective_zoom: float
    tier: str                        # resolve_scale_tier_stable 结果（去抖显示用）
    band_id: str                     # lod-0..3 / fixed / disabled
    label_zoom_bands: List[Dict[str, float]]   # build_label_zoom_bands() 的 4 档投影
    active_band: Dict[str, float]    # 当前档 minZoom/maxZoom/topRatio/sizeRatio
    visibility: Literal["visible","hidden_by_user","gated_by_zoom","hidden_by_representation"]
    hidden_reason: str               # 稳定 reason code，visible 时为 ""
    label_top_ratio: float
    symbol_size_ratio: float
    decimate_budget: int             # LOD_BANDS.decimate_budget（显示预算，非统计样本）
    terrain_maxzoom: int             # terrain_maxzoom_for_zoom（a. 解释面；b. terrain 源兼容）
    user_overrides: List[str]        # ≤8
    diagnostics: List[ScalePolicyDiagnostic]  # ≤8
    disclosures: List[str]           # ≤8
    source_revision: str
    scale_denominator: Optional[int] # 非 Web Mercator / 缺纬度 → None
    scale_assumptions: List[str]     # ≤8
    render_thinned: Optional[int]    # None = 不精确/未知，不伪装准确
    render_thinned_exact: bool
```

### 函数签名（必须严格实现）

```python
def validate_scale_policy(
    policy: Any, *, available_fields: Sequence[str] = ()
) -> List[ScalePolicyDiagnostic]: ...

def resolve_effective_scale_portrayal(
    *,
    zoom: float,
    layer: Mapping[str, Any],
    policy: Any = None,
    available_fields: Sequence[str] = (),
    previous_tier: Optional[str] = None,
    source_revision: str = "",
    frame: Optional[Mapping[str, Any]] = None,
    crs: str = "EPSG:3857",
    target_dpi: float = 96.0,
) -> EffectiveScalePortrayal: ...

def project_scale_portrayal_spec(
    decision: EffectiveScalePortrayal, *, include_bands: bool = True
) -> Dict[str, Any]: ...

def physical_scale_context(
    *, zoom: float, frame: Optional[Mapping[str, Any]],
    crs: str, target_dpi: float
) -> Dict[str, Any]: ...

def bounded_explanation(decision: EffectiveScalePortrayal) -> Dict[str, Any]: ...
```

### precedence（实现顺序，不可调换）

1. **用户隐藏优先，且所有 zoom 保持**（A05）：`layer.layout.visibility == "none"` 或 `layer.visible is False` 或合法 `policy.hidden is True` → `visibility="hidden_by_user"`，`applied=false`（策略未生效，不显示"已生效"），`hidden_reason="SCALE.HIDDEN.USER"`。此分支**不因 zoom 放大恢复**。
2. **人工 pin/锁优先**（A06）：合法 `pinned_size_ratio` / `pinned_label_top_ratio` 覆盖 LOD 输出，记入 `user_overrides`；`fixed_density` 模式必须给 `pinned_label_top_ratio`，缺失 → typed 诊断 + 退回 `disabled`。
3. **结构 zoom 门（与显隐正交）**（A04）：复用 `representation.zoom_gate_of_layer`（前端 `visibility.ts` 的后端孪生）。半开区间 `[min, max)`；`min>=max` 病态门按 visibility.ts 语义**开门 + 披露**（不留死带、不黑图）。命中 → `gated_by_zoom`。
4. **表达窗口 hidden**：`representation.export_adjudicate_layer(dict(layer), zoom) == "hidden"` → `hidden_by_representation`（必带 reason）。
5. **自动 LOD**（A02/A03/A07）：`lod_for_zoom(zoom)` + `build_label_zoom_bands()`（完整 4 档）+ `terrain_maxzoom_for_zoom(zoom)` + `LOD_BANDS` 的 `decimate_budget`。**这三个此前零消费者的符号在此成为真实生产调用**。
6. **去抖分档**（A10）：`resolve_scale_tier_stable(zoom, previous_tier)` 只影响 `tier` 显示，**不改变门/策略数值**；同 zoom + 同 previous_tier 幂等。
7. **物理比例尺**（A15/A16）：仅 `crs ∈ WEB_MERCATOR_CRS` 且能取得有效中心纬度时调用 `scale_rules.scale_denominator(zoom, latitude, dpi=target_dpi)`；否则 `None` + `scale_assumptions` 披露"该 CRS 不套 Web Mercator 固定公式"。`target_dpi` **绝不**参与 1–6 的任何裁决。
8. **disclosures 固定包含**（缺一不可）：
   - 小尺度省略标注/符号是**显示策略**，不写回 source / 分析结果 / 统计 count（A11/A12/A13）；
   - `radius_px`（屏幕像素）≠ `bandwidth_m`（真实距离），跨 zoom 不保证同量纲，不伪称可直接比较（A14）；
   - 输出 DPI 只影响采样与物理尺寸，不等价于语义 zoom（A15）；
   - `decimate_budget` 是渲染显示预算，data-plane 传输 LOD 是 display-only，均非统计样本（A13）。

### `project_scale_portrayal_spec` 输出（既有字段，不新增 spec 字段）

```python
{
  "label": {"zoomBands": [ {minZoom,maxZoom,topRatio,sizeRatio} × 4 ]},
  "thresholds": {"maxFeatures": <参考档 decimate_budget>},
}
```

- `zoomBands` **直接取 `build_label_zoom_bands()`**（已是 camelCase，匹配 `frontend/lib/mapspec-runtime/label-layout.ts::normalizeBands`）。
- `applied=false` / `disabled` → 返回 `{}`（旧 spec 零改动）。
- 该投影由主 Agent 在 `analysis_cartography_converter.py` 的既有落笔面 opt-in 写入；本模块不直接改 MapSpec。

### `bounded_explanation`（A24）

- 所有数组截到各自上限；`detail`/字符串字段截 ≤160 字符；`source_revision` 截 ≤64。
- 序列化后字节数 > `MAX_EXPLANATION_BYTES` → 按 `disclosures → scale_assumptions → user_overrides → diagnostics` 顺序裁剪，并置 `truncated=true`（**保留 diagnostics 优先于 narratives**）。
- 不遍历/不上传任何要素属性；`render_thinned` 只在调用方传入精确计数时非 None，否则 `None` + `render_thinned_exact=false`。

## 2. 共享契约语料 `tests/cartography/carto_ir_corpus/scale_portrayal_cases.json`

```json
{
  "corpus_version": "g27-scale-portrayal/1.0.0",
  "policy_modes": ["auto_lod","fixed_density","disabled"],
  "units": ["zoom_level","screen_px","ground_m"],
  "cases": [ { "id", "zoom", "layer", "policy", "available_fields", "expected" } ]
}
```

`expected` 至少含 `applied / tier / band_id / visibility / label_top_ratio / symbol_size_ratio / diagnostics_contains`。

必须覆盖的 case（backend pytest 与 frontend vitest **消费同一份 JSON**）：
`lod-boundary-z3`、`lod-boundary-z6`、`lod-boundary-z10`、`lod-boundary-z14`、`lod-boundary-z19`、`legacy-no-policy`、`unknown-policy-mode`、`unknown-policy-version`、`invalid-pinned-ratio`、`ghost-priority-field`、`user-hidden-all-zooms`、`pinned-size-wins`、`fixed-density-missing-ratio`、`dpi-72-vs-300-same-visibility`、`non-mercator-crs`、`degenerate-zoom-gate`、`hidden-representation-window`。

## 3. 分工边界（避免写冲突）

| 文件 | Owner |
|---|---|
| `app/lib/cartography/scale_portrayal.py` | subagent `g27-implementer` |
| `tests/cartography/test_scale_portrayal_v1.py` | subagent `g27-implementer` |
| `tests/cartography/carto_ir_corpus/scale_portrayal_cases.json` | subagent `g27-implementer` |
| `frontend/lib/carto-ir/scale-portrayal.ts` + vitest | 主 Agent |
| `app/services/analysis_cartography_converter.py`（落笔补丁） | 主 Agent |
| `app/lib/cartography/publication_ir.py`（加性 scale viewport） | 主 Agent |
| `frontend/components/hud/*`（按尺度表达面板） | 主 Agent |
| ToolRegistry 工具 / UI 接线 / 文档 / PR | 主 Agent |
| `mapspec_schema.py`、`representation.py`、`visibility.ts`、`contract_manifest.json`、`contract-manifest.ts` | **双方均不整文件改写**；如需加词表，由主 Agent 做小型加性补丁并先核对最新主线 |

## 4. 实现纪律

- 纯函数、零 IO、零时钟、零随机；同输入恒同输出。
- 不改 `LOD_BANDS` / `SCALE_TIERS` / `DEFAULT_ZOOM_BANDS` 数值；不合并四表。
- 不引入新依赖（仅 pydantic + 仓库内既有模块）；不 import 服务层/DB/网络。
- 测试单 worker、BLAS/OMP/NumExpr=1；只跑新增测试文件，不跑全量 pytest、不启动服务/浏览器。
- 不 commit、不 push、不派生子 agent；完成后报告文件清单、测试命令与真实结果、未解决项。
