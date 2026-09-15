# VLM Visual Critic Runtime — 接口规格书（agent/01）

- 状态：v1（随 ADR-0185 评审）
- 日期：2026-09-14
- 代码位置：`app/lib/harness/visual_judge/`
- 契约测试：`tests/unit/test_visual_critic_runtime.py`

本文是 ADR-0185 的实现级规格：模块 API、数据契约、JSON Schema、env 矩阵、
与 `PiAgentHarness` / `GisTraceChain` 的对接点。契约以本文件 + 契约测试为准。

## 1. 模块布局与公开 API

```
app/lib/harness/visual_judge/
├── __init__.py            # 公开面（下列全部符号）
├── contracts.py           # Pydantic 契约（extra="forbid"）
├── snapshot_extractor.py  # 截图解码/校验/哈希/灰度初筛
├── vlm_provider.py        # OpenAI 兼容 / Gemini 请求组装与客户端
├── critic_engine.py       # 评审编排（fail-closed / 记忆化 / 评分推导）
├── fake_vlm.py            # FakeVLMClient + 10 黄金样本元数据
└── golden_images.py       # 确定性缺陷渲染器（Pillow，无随机源）
```

```python
# contracts.py
class VisualDimension(str, Enum):    # 5 维白名单（严格枚举）
    READABILITY = "readability"
    COLOR_DISCRIMINABILITY = "color_discriminability"
    COMPOSITION_BALANCE = "composition_balance"
    INFORMATION_DENSITY = "information_density"
    SPATIAL_ALIGNMENT = "spatial_alignment"

class VisualBBox(BaseModel):         # [ymin, xmin, ymax, xmax]，百分比 0–100
    ymin: float; xmin: float; ymax: float; xmax: float
    # 校验：∈[0,100] 且 ymax>ymin ∧ xmax>xmin；接受 [a,b,c,d] 序列输入

class VisualCritiqueItem(BaseModel):
    dimension: VisualDimension
    severity: Literal["info", "warning", "error"] = "warning"
    confidence: float = 0.0          # [0,1]，非法→0.0
    suggestion: str                  # ≤300 字符截断
    evidence: str = ""               # ≤300 字符截断
    bbox: Optional[VisualBBox] = None
    defect_type: str = ""            # ≤60 字符（如 "overlapping_labels"）

class VisualDimensionScore(BaseModel):
    dimension: VisualDimension
    score: float                     # [0,10]，引擎推导
    confidence: float                # [0,1]
    rationale: str                   # ≤300 字符截断

class VisualJudgeReport(BaseModel):
    status: Literal["evaluated", "not_evaluated"]
    reason: str = ""                 # not_evaluated 时的机器可读码（§4）
    critiques: List[VisualCritiqueItem]
    dimension_scores: List[VisualDimensionScore]   # evaluated 时恰 5 条
    overall_score: float             # [0,10] 置信度加权
    overall_confidence: float
    has_blocking_defects: bool       # ∃ critique.severity == "error"
    session_id: str
    mapspec_fingerprint: str
    image_sha256: str
    image_width: int; image_height: int
    provider: str; model: str
    duration_ms: int
    mode: str                        # record_only | block（随 legacy 语义）
    pre_screen: Dict[str, Any]       # 灰度初筛 hints（有界）
    runtime: str = "visual_critic_v2"
    source: str = "visual_judge"     # L5 消费面兼容键（恒定）
    def to_summary(self) -> Dict[str, Any]   # 有界摘要 → cartography.visual_evidence
```

```python
# snapshot_extractor.py
class SnapshotError(Exception):
    reason: str   # image_empty | image_corrupt | image_oversized | image_too_small

@dataclass
class MapSnapshot:
    image_sha256: str
    width: int; height: int
    size_bytes: int
    mime: str                      # image/png | image/jpeg | image/webp
    data_url: str                  # 供 VLM 的 data URL（原始字节）
    pre_screen: Dict[str, Any]     # mean_luma/luma_std/p5_p95_spread/entropy/hints

class SnapshotExtractor:
    def __init__(self, *, max_bytes=4*1024*1024, min_edge=64, max_edge=8192)
    def extract(self, image: Union[bytes, str]) -> MapSnapshot   # 失败抛 SnapshotError
```

```python
# vlm_provider.py
CRITIC_OUTPUT_SCHEMA: Dict[str, Any]        # JSON Schema 单一事实源（§3）

@dataclass
class VLMRequest:
    provider: str; model: str
    image_data_url: str
    user_context: str = ""                  # 确定性摘要文本（有界）
    max_tokens: int = 1024; timeout_s: float = 20.0

def build_openai_request(req: VLMRequest) -> Dict[str, Any]   # chat/completions payload
def build_gemini_request(req: VLMRequest) -> Dict[str, Any]   # generateContent payload
def gemini_endpoint(base_url: str, model: str) -> str

class CriticProviderError(Exception): ...   # HTTP/协议失败
class CriticTimeout(CriticProviderError): ...
class CriticOutputError(Exception): ...     # 输出不可解析/不合 schema

class VLMClient(Protocol):
    provider: str; model: str
    async def critique(self, request: VLMRequest) -> str: ...

class OpenAICompatVLMClient:    # httpx 单次调用无重试
class GeminiVLMClient:          # x-goog-api-key 头

def parse_critic_output(text: str) -> List[Dict[str, Any]]
#   剥 ```json 围栏 → 提取最外层 JSON object → 返回 critiques 数组（每项 dict）
#   任何形状不合法抛 CriticOutputError
```

```python
# critic_engine.py
def visual_critic_runtime_enabled() -> bool
#   env CARTO_VISUAL_CRITIC_RUNTIME ∈ {1,true,yes}

class CriticMemoCache:   # (session_id, mapspec_fingerprint, image_sha256) → report，LRU 64
    def get(key) -> Optional[VisualJudgeReport]
    def put(key, report) -> None

class VisualCriticEngine:
    def __init__(self, client: Optional[VLMClient] = None, *,
                 extractor: Optional[SnapshotExtractor] = None,
                 cache: Optional[CriticMemoCache] = None)
    async def evaluate(self, *, session_id: str, mapspec_fingerprint: str,
                       image: Union[bytes, str],
                       deterministic_summary: Optional[Dict[str, Any]] = None,
                       mode: str = "record_only") -> VisualJudgeReport
    #   永不抛异常：一切失效归因为 not_evaluated report（ADR-0185 D3 矩阵）

def build_critic_engine() -> VisualCriticEngine
#   从 env 解析 provider/key/model/timeout；key 缺席或占位符 ⇒ client=None
#   （engine.evaluate 届时回 not_evaluated/no_api_key）

# fake_vlm.py / golden_images.py
GOLDEN_SAMPLES: Tuple[GoldenSample, ...]   # 恰 10 项（§6）
class FakeVLMClient:      # VLMClient 协议实现：sample 名路由 / 故障注入 / 外呼计数
def render_golden_image(name: str, size: (w,h)=(640,480)) -> bytes   # 确定性 PNG
```

## 2. 与既有设施的对接点

| 对接点 | 位置 | 契约 |
|---|---|---|
| PiAgentHarness L5 评审 | `app/lib/harness/pi_agent_harness.py:831` `attach_visual_judgement` | 开关开且无注入 judge ⇒ 引擎接管；证据行形状对齐 §5 |
| L5 goal_satisfaction | `visual_evaluator.derive_goal_satisfaction`（pi_agent_harness.py:1764、replay/replayer.py:513 消费） | 零改动；摘要保持 `source="visual_judge"` + `error_count` 键 |
| GisTraceChain / replay | `app/lib/harness/replay/replayer.py` 经同一 seam | 结论随 `visual_evidence` 与 VISUAL_* 检查行进入 trace，`runtime` 键区分代际 |
| W9 seam（01/02 线禁改域） | `app/services/gis_harness/visual_evaluator.py` | 不触碰 |
| legacy 内置 VLM judge | `visual_evaluator._vlm_judge` | 不动；优先级低于 v2（ADR-0185 D7） |

judge 解析优先级（`attach_visual_judgement` 内）：
`CARTO_VISUAL_JUDGE` 注入 > `CARTO_VISUAL_CRITIC_RUNTIME=1` 引擎 > legacy `CARTO_VISUAL_JUDGE_VLM=1`。

## 3. VLM 输出 JSON Schema（单一事实源 `CRITIC_OUTPUT_SCHEMA`）

```json
{
  "type": "object", "additionalProperties": false,
  "required": ["critiques"],
  "properties": {
    "critiques": {
      "type": "array", "maxItems": 12,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["dimension", "severity", "suggestion", "confidence"],
        "properties": {
          "dimension": {"enum": ["readability", "color_discriminability",
                                  "composition_balance", "information_density",
                                  "spatial_alignment"]},
          "severity": {"enum": ["info", "warning", "error"]},
          "suggestion": {"type": "string"},
          "evidence": {"type": "string"},
          "confidence": {"type": "number", "minimum": 0, "maximum": 1},
          "bbox": {"type": "array", "items": {"type": "number"},
                    "minItems": 4, "maxItems": 4},
          "defect_type": {"type": "string"}
        }
      }
    }
  }
}
```

OpenAI 方言：`response_format={type:"json_schema", json_schema:{name:"visual_critic", strict:true, schema:…}}`；
Gemini 方言：`generationConfig.response_mime_type="application/json"` + `responseSchema`（同构）。
消毒双保险：schema 之上，契约层 `extra="forbid"` + 维度白名单再过滤一遍；
改图意图字段（mutation/intent/spec/patch/layers…）命中 ⇒ 整条判废（legacy 同纪律）。

## 4. env 矩阵（全部可选；缺席 = 特性缺席，诚实降级）

| 变量 | 默认 | 语义 |
|---|---|---|
| `CARTO_VISUAL_CRITIC_RUNTIME` | 空（关） | `1/true/yes` ⇒ 引擎接管评审（优先级见 §2） |
| `CARTO_VISUAL_JUDGE_PROVIDER` | `openai_compatible` | `openai_compatible \| gemini` |
| `CARTO_VISUAL_JUDGE_BASE_URL` | settings `LLM_BASE_URL` | provider 端点基址 |
| `CARTO_VISUAL_JUDGE_API_KEY` | settings `LLM_API_KEY` | 缺席/占位符 ⇒ `no_api_key` |
| `CARTO_VISUAL_JUDGE_MODEL` | settings `LLM_MODEL` | 模型名（写入报告披露） |
| `CARTO_VISUAL_JUDGE_TIMEOUT_S` | `20` | 单次调用超时（无重试） |
| `CARTO_VISUAL_CRITIC_MAX_IMAGE_BYTES` | `4194304` | 截图字节上限（超 ⇒ `image_oversized`） |
| `CARTO_VISUAL_CRITIC_MIN_EDGE_PX` | `64` | 最小边长（低于 ⇒ `image_too_small`） |

不新增 `.env.example` 键（沿用 legacy judge 的配置家族；测试 env 卫生锁不受扰动）。

## 5. 证据行契约（`_apply_critic_report`，与 legacy 逐键对齐）

- `visual_evidence` 摘要：legacy `to_summary()` 全键 + 增列
  `dimension_scores`（5 条投影）、`overall_score`、`overall_confidence`、
  `pre_screen`、`provider`、`runtime:"visual_critic_v2"`；
  `source` 恒为 `"visual_judge"`、`status`/`reason`/`error_count`/`warning_count`
  语义与 legacy 一致（L5 消费面零改动）。
- 检查行：evaluated ⇒ 每批评一行 `rule=VISUAL_{DIMENSION 大写}`、
  `status= fail/warning/pass`（severity 映射）、`evidence_class="visual"`、
  `repairability`（error+color_discriminability ⇒ `auto_safe`，余 `not_repairable`，
  对齐 legacy rotate_palette 动作）、`suggested_fix`、`evidence`（含
  suggestion/confidence/detail/bbox/defect_type/mode/screenshot_digest/provider/model）、
  `message="Visual critique ({dim}): {suggestion}"`。
- not_evaluated ⇒ 单行 `VISUAL_ORACLE`（not_evaluated/info/not_repairable），
  `evidence.reason` = §4 机器码。

## 6. 黄金样本目录（`GOLDEN_SAMPLES`，恰 10 项）

| 样本名 | 缺陷 | 期望维度 | 期望 severity |
|---|---|---|---|
| `overlapping_labels` | 标注互相重叠不可读 | readability | error |
| `low_contrast_dark_theme` | 深色底图配深色图斑 | color_discriminability | error |
| `adjacent_palette_confusion` | 相邻分类色板难分辨 | color_discriminability | warning |
| `symbol_clutter_overdensity` | 符号过密不可辨 | information_density | error |
| `sparse_canvas_underdensity` | 画布过疏信息匮乏 | information_density | warning |
| `bottom_heavy_layout` | 版面重心下坠 | composition_balance | warning |
| `overlay_offset_misalignment` | 底图/矢量叠加错位 | spatial_alignment | error |
| `extreme_tilt_rotation` | 极端倾斜/旋转异常 | spatial_alignment | warning |
| `tiny_unreadable_text` | 字号过小不可读 | readability | warning |
| `clean_balanced_map` | 良图对照（零批评） | —（无批评） | — |

每样本：确定性渲染器（`golden_images.render_golden_image(name)`，同名字节级
可复现）+ canned JSON 响应（`FakeVLMClient(sample=name)`）+ 元数据断言
（维度/严重度/良图零批评）。契约测试逐样本走 全管线
（extract → fake VLM → 契约消毒 → 评分推导 → 报告）。

## 7. 验收口径（阶段四门禁）

1. `pytest tests/unit/test_visual_critic_runtime.py -v` 全绿；
2. 存量回归：`tests/cartography/test_visual_judge_selfheal.py`、
   `tests/unit/gis_harness/test_visual_observation_v6.py` 全绿（默认关闭 =
   逐字节等价）；

3. `ruff check app/lib/harness/visual_judge/ tests/unit/test_visual_critic_runtime.py`
   零告警（项目 select：E4/E7/E9/F）；
4. 新包覆盖率 ≥ 90%（`--cov=app/lib/harness/visual_judge`）；
5. fail-closed 矩阵全样本负路径测试（损坏图/超时/无 key/坏输出/超限）零
   `evaluated` 泄漏。
