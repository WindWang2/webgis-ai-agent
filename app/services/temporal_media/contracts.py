"""G35 领域契约：关闭词表、资源上限、typed 错误、工具/路由请求模型。

设计约束（任务 §二/§三/§六）：
- 请求面只接受 refs 与有限枚举/整数；**不接受任意路径、URL、shell 参数、
  编码器选项或外部 provider**。所有文件名、编码参数、字体、工作目录由服务端决定。
- 三种身份严格分离：``observation``（真实观测桶）/ ``digital_frame``
  （渲染 PNG 证据）/ ``encoded_frame``（容器帧），由 manifest 显式映射。
- 展示时长、fps、数据时间单位互不等同；媒体时间一律量化为整数毫秒。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

#: 计划/清单/收据的独立版本面（与 MapSpec 文档版本解耦）。
SCHEMA_VERSION = "temporal_media.v1"

MEDIA_FORMATS: tuple[str, ...] = ("apng", "gif")
TIMING_POLICIES: tuple[str, ...] = ("equal", "proportional")
PLAYBACK_ORDERS: tuple[str, ...] = ("chronological", "reverse")
MISSING_DISPLAYS: tuple[str, ...] = ("skip", "empty", "hold")
TIME_DISPLAY_MODES: tuple[str, ...] = ("label", "label_window", "none")
PARTIAL_POLICIES: tuple[str, ...] = ("blocking", "allow_partial")
SEGMENT_KINDS: tuple[str, ...] = ("observation", "held", "empty")
JOB_PHASES: tuple[str, ...] = (
    "queued", "freezing", "rendering", "encoding", "verifying",
    "publishing", "completed", "cancelled", "failed",
)
JOB_STATUSES: tuple[str, ...] = ("pending", "running", "completed", "cancelled", "failed")

# ── 资源预算（独立于 MAX_TEMPORAL_FRAMES=512 的数据契约上限）──────────────
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
MIN_WIDTH, MAX_WIDTH = 320, 1920
MIN_HEIGHT, MAX_HEIGHT = 240, 1080
MAX_OBSERVATIONS = 120            # 独立观测画面上限（不是容器帧数）
MAX_SEGMENTS = 240                # 含缺期显示段
MAX_ENCODED_FRAMES = 240          # 容器帧硬帽
MIN_DWELL_MS, MAX_DWELL_MS = 100, 10_000
MAX_TOTAL_DISPLAY_MS = 120_000    # 总展示时长硬帽
MAX_FPS = 24                      # 0 = 每段一张编码帧（停留复用，不重复渲染）
#: 按真实间隔展示时允许参与缩放的最大**数据间隔**（10 年）；超过则压缩并披露。
#: 单位是数据时间毫秒，不是展示时长毫秒 —— 年度资料不会被压成同一时长。
MAX_PROPORTIONAL_GAP_MS = 10 * 365 * 24 * 3600 * 1000
#: 编码期常驻原始像素预算（APNG RGBA 4B/px、GIF P 1B/px）。
#: APNG 走 streaming writer（单帧常驻）；GIF 走 Pillow append_images，
#: 由预检按此上限折算最大帧数，禁止「传 iterator 就宣称常量内存」。
MAX_ENCODE_RAW_BYTES = 320 * 1024 * 1024
MAX_TEMP_BYTES = 512 * 1024 * 1024
MAX_FRAME_CACHE_ENTRIES = 512
MAX_FRAME_CACHE_BYTES = 256 * 1024 * 1024
MAX_SVG_BYTES = 8 * 1024 * 1024
RSVG_TIMEOUT_S = 60
ENCODE_TIMEOUT_S = 120
PROBE_TIMEOUT_S = 8
#: manifest/state JSON 上限（240 段 + 240 数字帧证据仍在预算内）。
MAX_MANIFEST_CHARS = 16 * 1024 * 1024
MAX_FRAME_IDS = MAX_OBSERVATIONS

#: 媒体能力合同里的渲染器标识（冻结进计划，换渲染器即换缓存键）。
RENDERER_ID = "mapspec-to-svg+rsvg-convert"
RENDERER_VERSION = "1"

#: 视频 codec 由宿主真实 probe；部署未落实时不开放（不按扩展名猜编码成功）。
VIDEO_FORMATS: tuple[str, ...] = ("mp4", "webm")
VIDEO_DEPLOYMENT_FLAG = "WEBGIS_TEMPORAL_MEDIA_ENABLE_VIDEO"


class TemporalMediaError(Exception):
    """typed 领域错误（HTTP / Pi / UI 共用，不泄漏路径或命令）。"""

    def __init__(self, code: str, message: str, *, status_code: int = 422,
                 details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_payload(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "success": False,
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            out["details"] = self.details
        return out


class _Closed(BaseModel):
    """关闭词表基类：未知字段一律拒绝（不接受任意编码参数/路径/URL）。"""

    model_config = ConfigDict(extra="forbid", strict=False)


class CapabilitiesArgs(_Closed):
    session_id: str = Field(default="", max_length=128)
    expected_revision: Optional[int] = Field(default=None, ge=0)


class MediaPlanArgs(_Closed):
    """预览/提交共用：用户看到的计划与实际编码采用的计划是同一份。"""

    session_id: str = Field(default="", max_length=128)
    expected_revision: Optional[int] = Field(default=None, ge=0)
    #: 观测子集。frame_ids 优先；否则 from/to 为按真实时间排序的闭区间。
    from_frame_id: Optional[str] = Field(default=None, max_length=64)
    to_frame_id: Optional[str] = Field(default=None, max_length=64)
    frame_ids: Optional[List[str]] = Field(default=None, max_length=MAX_FRAME_IDS)
    order: Literal["chronological", "reverse"] = "chronological"
    #: equal = 每个真实观测等时长；proportional = 按真实观测间隔缩放展示。
    timing_policy: Literal["equal", "proportional"] = "equal"
    dwell_ms: int = Field(default=1000, ge=MIN_DWELL_MS, le=MAX_DWELL_MS)
    #: 0 = 一个停留只编码一张帧（时长写在容器里）；>0 = 拆成多张相同编码帧。
    fps: int = Field(default=0, ge=0, le=MAX_FPS)
    #: 缺期只影响展示提示/空态/沿用，绝不插值新数值或几何。
    missing_display: Literal["skip", "empty", "hold"] = "skip"
    time_display: Literal["label", "label_window", "none"] = "label"
    width: int = Field(default=DEFAULT_WIDTH, ge=MIN_WIDTH, le=MAX_WIDTH)
    height: int = Field(default=DEFAULT_HEIGHT, ge=MIN_HEIGHT, le=MAX_HEIGHT)
    format: Literal["apng", "gif"] = "apng"
    loop: bool = True
    partial_policy: Literal["blocking", "allow_partial"] = "blocking"


class SubmitArgs(MediaPlanArgs):
    """提交 = 预览请求 + 明确的计划版本（未知/漂移版本拒绝）。

    ``plan_ref`` 是 preview 返回的 16 位短句柄：生产 dispatch 会为上下文预算
    截断长字符串，64 位 ``plan_hash`` 不保证完整抵达模型；短 ref 由服务端
    owner 作用域解析为完整冻结计划，仍能拒绝未知/漂移版本。二者皆可直接用，
    但不可同时省略。
    """

    plan_hash: Optional[str] = Field(default=None, min_length=64, max_length=64,
                                     description="完整预览计划指纹（服务端/UI 可用）")
    plan_ref: Optional[str] = Field(default=None, min_length=16, max_length=16,
                                    description="preview 返回的 16 位短计划句柄（Pi/LLM 推荐）")


class JobRefArgs(_Closed):
    session_id: str = Field(default="", max_length=128)
    job_id: str = Field(..., min_length=1, max_length=64)


class ResultArgs(JobRefArgs):
    """读取成果（授权 ref，不带媒体字节）。"""

    refresh_urls: bool = Field(default=True)


__all__ = [
    "SCHEMA_VERSION", "MEDIA_FORMATS", "TIMING_POLICIES", "PLAYBACK_ORDERS",
    "MISSING_DISPLAYS", "TIME_DISPLAY_MODES", "PARTIAL_POLICIES",
    "SEGMENT_KINDS", "JOB_PHASES", "JOB_STATUSES", "VIDEO_FORMATS",
    "VIDEO_DEPLOYMENT_FLAG", "RENDERER_ID", "RENDERER_VERSION",
    "DEFAULT_WIDTH", "DEFAULT_HEIGHT", "MIN_WIDTH", "MAX_WIDTH",
    "MIN_HEIGHT", "MAX_HEIGHT", "MAX_OBSERVATIONS", "MAX_SEGMENTS",
    "MAX_ENCODED_FRAMES", "MIN_DWELL_MS", "MAX_DWELL_MS",
    "MAX_TOTAL_DISPLAY_MS", "MAX_FPS", "MAX_PROPORTIONAL_GAP_MS",
    "MAX_ENCODE_RAW_BYTES", "MAX_TEMP_BYTES", "MAX_FRAME_CACHE_ENTRIES",
    "MAX_FRAME_CACHE_BYTES", "MAX_SVG_BYTES", "RSVG_TIMEOUT_S",
    "ENCODE_TIMEOUT_S", "PROBE_TIMEOUT_S", "MAX_MANIFEST_CHARS",
    "MAX_FRAME_IDS", "TemporalMediaError",
    "CapabilitiesArgs", "MediaPlanArgs", "SubmitArgs", "JobRefArgs", "ResultArgs",
]
