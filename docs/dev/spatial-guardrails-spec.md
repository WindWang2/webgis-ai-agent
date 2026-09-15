# 空间反幻觉与地理红线安全守护引擎 技术规格书（spatial guardrails v1）

- ADR：`docs/adr/0195-spatial-anti-hallucination-guardrails.md`
- 分支：`agent/11-spatial-anti-hallucination-guardrails`
- 状态：v1（随 ADR-0195 评审）

本文是 ADR-0195 的可实现技术规格：模块接口、数据格式、算法、挂载点、
测试计划与性能预算。实现与测试以本文为准。

---

## 1. 模块布局

```
app/services/spatial_guardrails/
├── __init__.py                  # 公共 API：SpatialGuardrails / verdict / errors 再导出
├── types.py                     # GuardLevel / DefenseMode / GuardrailIssue / GuardrailVerdict / GuardrailConfig
├── errors.py                    # SpatialGuardrailError 及子类
├── geo_index.py                 # BboxSpatialHash 空间哈希 + 测距工具（等距圆柱近似）
├── data/
│   ├── __init__.py              # 惰性单例加载器 get_landmask_index() / get_admin_table()
│   ├── world_landmask.py        # 内嵌粗粒度陆块/水体多边形（纯 Python 元组表）
│   └── admin_divisions.py       # GB/T 2260 省级 + 地级内嵌表
├── latlon_inversion_detector.py # 倒置检测 + 置信度自适应纠偏
├── landmask_validator.py        # 海陆/水体掩膜校验 + 设施类型常识
├── admin_division_verifier.py   # 行政区划结构/表校验 + 模糊修正
├── redlines.py                  # Geofence 红线围栏 + 抓取预算
├── topology_checks.py           # L4 线/面几何自洽性
└── guardrail_middleware.py      # 编排网关 + 两个挂载点入口
```

依赖纪律：模块内**只依赖标准库**（math/json/os/dataclasses/typing）。
禁止网络 IO、禁止 numpy/geopandas/shapely。`guardrail_middleware`
对 `tool_dispatch_service` / `lifecycle_engine` 保持单向依赖（只被
它们 import，绝不反向 import）。

## 2. 核心类型（types.py）

```python
class GuardLevel(StrEnum):
    L1_FORMAT_CRS = "L1_FORMAT_CRS"
    L2_GEOGRAPHIC_BOUNDS = "L2_GEOGRAPHIC_BOUNDS"
    L3_LANDMASK_PLAUSIBILITY = "L3_LANDMASK_PLAUSIBILITY"
    L4_TOPOLOGY_CONSISTENCY = "L4_TOPOLOGY_CONSISTENCY"

class DefenseMode(StrEnum):
    BLOCK = "BLOCK"
    AUTO_FLIP = "AUTO_FLIP"
    WARN_DEGRADE = "WARN_DEGRADE"
    PASS = "PASS"            # 无发现时的显式通过态

@dataclass
class GuardrailIssue:
    level: GuardLevel
    mode: DefenseMode
    code: str                # 稳定机器码，如 "LATLON_INVERTED" / "OCEAN_POINT_ON_LAND_FACILITY"
    message: str             # 人读证据（中文，面向 LLM 与审查者）
    location: str            # JSONPath 式定位，如 "features[3].geometry.coordinates"
    evidence: dict           # 层级专属证据（原值/修正值/置信度/距离等）
    corrected: Any | None = None  # AUTO_FLIP 时提供修正后的值

@dataclass
class GuardrailVerdict:
    passed: bool                       # 无 BLOCK 即 True（WARN 仍算 passed）
    mutated: bool = False              # 是否发生了 AUTO_FLIP 纠偏
    issues: list[GuardrailIssue] = field(default_factory=list)
    duration_ms: float = 0.0
    def blocking(self) -> list[GuardrailIssue]
    def warnings(self) -> list[GuardrailIssue]
```

配置（`GuardrailConfig.from_env()`，全部带默认值，可 env 覆盖）：

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `SPATIAL_GUARDRAILS` | `1` | 总开关；`0` 时网关直通（kill switch） |
| `SPATIAL_GUARDRAILS_AUTO_FLIP_MIN_CONFIDENCE` | `0.60` | AUTO_FLIP 最低置信度 |
| `SPATIAL_GUARDRAILS_OCEAN_CONFIRM_KM` | `150` | 距陆地超过该距离才判"确信开阔水域" |
| `SPATIAL_GUARDRAILS_MAX_BBOX_KM2` | `250000`（~500km×500km） | 单次请求 bbox 面积预算 |
| `SPATIAL_GUARDRAILS_TELEPORT_KM` | `300` | 线要素相邻顶点跳变阈值 |
| `SPATIAL_GUARDRAILS_REDLINES_JSON` | `""` | 额外红线围栏 JSON 路径（可选） |

## 3. 错误契约（errors.py）

```python
class SpatialGuardrailError(Exception): ...        # 基类：携带 GuardrailIssue
class GeographicImpossibilityError(SpatialGuardrailError): ...   # L2/L3 物理不可能（深海设施等）
class LatLonInversionError(SpatialGuardrailError): ...           # L1 高危倒置且不可自动纠偏
class FabricatedAdminDivisionError(SpatialGuardrailError): ...   # L1/L3 虚构行政区划码
class GeofenceRedlineViolationError(SpatialGuardrailError): ...  # L2 红线越界
class TopologyImplausibilityError(SpatialGuardrailError): ...    # L4 严重几何不自洽
```

约定：**引擎内部校验函数直接抛错**（单元可断言异常类型）；编排网关
`guardrail_middleware` 捕获并折算为 `GuardrailVerdict`（BLOCK issue），
两个挂载点据此返回各自的错误载体（`ToolDispatchResult` /
`MapSpecResult`），不向调用方泄栈。

## 4. 空间索引与测距（geo_index.py）

- `BboxSpatialHash(cell_deg=10.0)`：`register(polygon_id, ring)` 注册；
  `candidates(bbox)` 返回与 bbox 所交 cell 挂钩的候选 polygon_id 集合；
  `neighbors(lng, lat)` 返回该点所在及相邻 9 cell 的候选（含缓冲带）。
- `point_in_ring(lng, lat, ring)`：ray-casting，环格式
  `[(lng, lat), ...]`，首尾可不闭合（内部闭合）。
- `approx_distance_km(a, b)`：等距圆柱近似
  `sqrt((dlat*111.32)² + (dlng*111.32*cos(mean_lat))²)`；
- `ring_min_distance_km(lng, lat, ring)`：点到环各边线段最小距离
  （线段投影夹取），供海区置信带判定。

## 5. 数据资产

### 5.1 `data/world_landmask.py`

纯 Python 常量表：`LAND_POLYGONS: list[tuple[name, tuple[(lng,lat),...]]]`
（约 30 个陆块：七大洲 + 格陵兰 + 主岛群）与
`WATER_POLYGONS`（大型内陆水体：里海、五大湖、贝加尔湖、青海湖、
洞庭湖、鄱阳湖、太湖等）。数据带 `LANDMASK_VERSION` 常量与精度声明。
顶点为手工简化的粗粒度轮廓（大陆级轮廓误差容忍 ~1–2°），
**只服务开阔水域判定，不服务海岸线判定**（D5 精度声明）。

### 5.2 `data/admin_divisions.py`

- `PROVINCES: dict[code, (name, aliases, center, bbox)]`——34 个省级行政区
  全量（11–82）；
- `PREFECTURES: dict[code, (name, parent_province)]`——地级市全量内嵌
  （含省直辖县级与直辖市市辖区段）；
- `ADMIN_TABLE_EDITION`：数据版本串。表中数据用于归属校验与名称
  模糊映射；**未收录但结构合法的 6 位码降级 WARN 而非 BLOCK**（D6 红线）。

## 6. 校验器接口

### 6.1 `latlon_inversion_detector.py`

```python
@dataclass
class InversionReport:
    is_inverted: bool
    confidence: float           # 0.0–1.0
    corrected: tuple[float, float] | None   # [lng, lat] 修正值
    evidence: dict              # hard_signal / as_given_zone / flipped_zone / scores

def detect_pair(pair: Sequence[float], *, context: str | None = None) -> InversionReport
```

- `context`：调用方语境提示（`"lat_first" | "lng_first" | None`），
  仅作为硬信号 tie-breaker；
- 算法（D4）：硬信号（值域矛盾，confidence=1.0）优先；软信号为
  两种解释的海陆评分对比（land=1.0 / coastal=0.5 / ocean=0.0，
  叠加 `|lat|>85` 惩罚与经度 `>180` wrap 证据），置信度在
  [0.6, 0.95] 内按证据强度插值；
- `auto_flip(pair)` 组合函数：detect → 置信度 ≥ 配置阈值 → 返回修正；
  否则原样返回并携带 WARN 证据。

### 6.2 `landmask_validator.py`

```python
class SurfaceZone(StrEnum): LAND / COASTAL_BAND / OCEAN_CONFIDENT / WATER_BODY

def classify_point(lng: float, lat: float) -> SurfaceZone
def validate_facility_point(lng, lat, *, facility_kind: str | None) -> None
    # raises GeographicImpossibilityError（OCEAN_CONFIDENT 上的陆上设施）
    # raises 亦抛 warn issue 由网关收集（WATER_BODY / COASTAL_BAND → WARN）
```

- `facility_kind` 词汇：`building / poi / road / school / hospital /
  None(未知)`。未知类型不判 L3，只判 L2（越世界 bounds）；
- 内陆水体（WATER_POLYGONS 内）上的设施 → WARN_DEGRADE（水库/湖心
  确有码头站点的极小概率，不硬杀）+ 湖面道路 → BLOCK 由 L4 场景
  （line 落水判定）承载 v1 仅 WARN，规格注记。

### 6.3 `admin_division_verifier.py`

```python
@dataclass
class AdminCheckReport:
    ok: bool                     # 结构合法
    known: bool                  # 表内收录
    name: str | None
    parent_chain: list[str]      # [省, 市]
    suggestion: str | None       # 模糊修正建议（名称/近似码/归属纠偏）
    issue_code: str | None       # FABRICATED_ADMIN_CODE / ADMIN_PARENT_MISMATCH / UNKNOWN_BUT_PLAUSIBLE

def verify_code(code: str, *, claimed_parent: str | None = None) -> AdminCheckReport
def resolve_name(name: str) -> str | None     # 名称/别名 → 代码（含简称匹配）
```

结构规则（第一道防线，零表依赖）：`^\d{6}$`、前两位 ∈ 省级集合、
第 3–4 位非 `00`（地级段，省级码本身除外）、第 5–6 位非 `00`
（县级段，省级/地级码本身除外）。虚构典型：`999999`、`190000`、
`010100`、`000000`、`123456`（省 12 合法但地级 34 未收录 → 走表
判定 → UNKNOWN_BUT_PLAUSIBLE WARN；`999999`/`190000` 直接 BLOCK）。

### 6.4 `redlines.py`

```python
@dataclass
class RedlineZone: name: str; ring: list; policy: Literal["no_fetch", "coarse_only"]

def check_bbox(bbox: list[float], *, action: str) -> None   # raises GeofenceRedlineViolationError
def check_area_budget(bbox: list[float]) -> None            # raises GeofenceRedlineViolationError
def check_radius(lng, lat, radius_km: float, *, action: str) -> None
```

内置预算围栏默认开启；区域围栏默认空清单 + `SPATIAL_GUARDRAILS_REDLINES_JSON`
可注入（`{"zones": [{"name", "ring": [[lng,lat]...], "policy"}]}`）。

### 6.5 `topology_checks.py`

```python
def validate_linestring(coords: list, *, teleport_km: float) -> None
    # raises TopologyImplausibilityError（相邻顶点跳变 > teleport_km）
def validate_polygon(rings: list) -> None
    # raises TopologyImplausibilityError（<3 顶点 / 唯一顶点占比 ≤50%）
    # 注：自交环（蝴蝶结）净鞋带面积可为 0 但并非退化，其 make_valid 修复
    # 归 quality gate（MAP_QUALITY_GATE_MODE）管辖，本层不越界拦截。
```

## 7. 编排网关与挂载点（guardrail_middleware.py）

```python
class SpatialGuardrails:
    def __init__(self, config: GuardrailConfig | None = None)
    def check_tool_args(self, tool_name: str, args: dict) -> GuardrailVerdict
    def check_intent(self, intent: Any) -> tuple[Any, GuardrailVerdict]   # (可能被纠偏的 intent, verdict)
    def check_geojson(self, obj: Any, *, location: str = "") -> GuardrailVerdict

_default = ...        # 进程内惰性单例
def get_guardrails() -> SpatialGuardrails
def enabled() -> bool  # SPATIAL_GUARDRAILS != "0"
```

- `check_tool_args`：递归遍历 args，抽取坐标形态值
  （二元数值数组对 / GeoJSON 对象 / `center`、`bbox`、`coordinates`、
  `lng/lat`/`lon/lat` 键名），逐点跑 L1→L2→L3、线面跑 L1→L2→L4；
  `bbox` 数组追加面积预算与红线检查；
- 行政区划扫描：`adcode`/`admin_code`/`city_code`/`province_code` 键名
  + 6 位数字串值 → `verify_code`；
- **异常委断**：网关任何内部异常（bug、数据缺角）→ 记 WARN 放行
  （fail-open），仅 BLOCK/AUTO_FLIP 判定本身可改变请求——守护网关
  绝不因自身故障瘫痪调度面。

### 7.1 ToolDispatchService 挂载（`app/services/tool_dispatch_service.py`）

位置：`dispatch()` 内、dedup 检查完成后、分析复用（V2 P10）之前：

```python
if spatial_guardrails_enabled():
    _g = get_guardrails()
    _parsed_args = ...  # 已解析 dict；解析失败跳过（保守放行）
    _verdict = _g.check_tool_args(tool_name, _parsed_args)
    if not _verdict.passed:
        return ToolDispatchResult(status="error", llm_payload=拦截文案+证据+correction_hint,
                                  slim_event={...}, raw_result={"success": False, ...},
                                  geojson_ref=None, error_msg=首条 BLOCK code)
    if _verdict.mutated:
        tc["function"]["arguments"] = json.dumps(_parsed_args, ensure_ascii=False)
        # dedup key 保持原参：同参倒置重复提交同样走纠偏，语义幂等
```

### 7.2 lifecycle_engine.apply_mutation 挂载

位置：`apply_mutation()` 入口、`session_lock_registry.lock` 之前：

```python
if spatial_guardrails_enabled():
    intent, _verdict = get_guardrails().check_intent(intent)
    if not _verdict.passed:
        return MapSpecResult(is_error=True, origin=origin,
                             error_msg=..., correction_hint=...)
```

AUTO_FLIP 已在 `check_intent` 返回的 intent 上生效（dataclass 替换：
`SetViewIntent(center=修正值)` / `UpsertLayerIntent(layer=修正后的 layer)`）；
verdict warnings 由调用侧留痕（本 ADR v1 仅日志 + llm_payload 可见）。

## 8. 观测

- `GuardrailVerdict.duration_ms` 记录单次校验耗时；
- BLOCK/AUTO_FLIP 事件在两条挂载点各自输出结构化日志
  （logger 名 `app.services.spatial_guardrails`），字段：
  `code / level / mode / location / tool|intent / duration_ms`。

## 9. 测试计划（tests/unit/test_spatial_guardrails.py，TDD 先行）

| 组 | 用例 | 断言 |
| --- | --- | --- |
| T1 | 20 组故意倒置坐标（北京/上海/成都/广州/纽约/伦敦…） | 全部检出、`corrected` 精确等于预期 `[lng,lat]`、置信度分档正确 |
| T2 | 深海建筑点（太平洋/大西洋/印度洋/南太平洋中部 × 学校/医院/POI） | `GeographicImpossibilityError`；网关折算 BLOCK verdict |
| T3 | 虚构行政区划（`999999`/`190000`/`000000`/`01`/`abc123` + 归属错配） | 第一道防线截获 `FabricatedAdminDivisionError`；名称模糊映射、近似码建议 |
| T4 | 完全离线守护 | socket 封禁 fixture 下全功能可用；模块无网络导入 |
| T5 | 性能预算 | 单点校验 p95 < 5ms（20 点批量均值断言，CI 安全余量） |
| T6 | 挂载点集成 | dispatch BLOCK 返回 error 结果不执行工具；AUTO_FLIP 改写 arguments；apply_mutation 锁前拦截 SetView/UpsertLayer |
| T7 | 红线与预算 | 超大 bbox BLOCK；注入围栏后命中策略 |
| T8 | L4 拓扑 | 瞬移线、零面积面拦截；正常几何放行 |
| T9 | kill switch 与 fail-open | `SPATIAL_GUARDRAILS=0` 零行为差异；网关内部异常放行 |

## 10. 性能预算

| 操作 | 预算 |
| --- | --- |
| 单点 L1+L2+L3 校验（含倒置检测） | < 5ms p95 |
| 空间哈希构建（进程一次） | < 50ms |
| GeoJSON FeatureCollection（100 features） | < 100ms |

实现手段：惰性单例 + 10° 网格 O(1) 候选定位 + 候选集内 ray-casting；
无锁读（数据进程内不可变）。

## 11. 边界与后续

- v1 不做：GCJ02/WGS84 坐标系转换、海岸线级精判、DEM 高程常识
  （山脊绝壁）、内陆水库级水体（数据粒度限制）、poi 数据库比对；
- 预留：landmask provider 抽象（未来接入高精掩膜数据源）、
  findings 面板化（对齐 ADR-0078 cartography_findings 呈现链路）。
