"""SRE 有界指标（Quality V3 W11，Epic 10 / 架构 §I）。

对齐 auth_metrics.py 模式：注册进 prometheus_client 默认 REGISTRY，
随 instrumentator 的 /metrics 一起暴露。**封闭标签词表**——component
只允许 SRE_COMPONENTS（防高基数攻击，auth_metrics 同款纪律）。

刷新语义（M-4：指标必须被常驻路径刷新，否则告警永不着火——#473 变种）：

- 值由 ``app/api/routes/health.py`` 的鉴权 SRE status 端点刷新
  （运维轮询即刷新）；
- ``sre_health_refresh_timestamp_seconds`` 在每次刷新时写当前时间；
  告警侧用 staleness 规则（刷新滞后/序列缺席）兜底"陈旧 ok 掩盖故障"
  ——这是标准 SRE 组合，不许"全绿假象"。
- 组件值词表：1=ok, 0.5=degraded, 0=down。导入时初始化为 1 是**临时
  值**（进程刚起、尚未首次探测），staleness 告警覆盖这一窗口。
"""
import logging

from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

#: 组件词表（封闭；SRE status 端点与告警规则共用此口径）
SRE_COMPONENTS = ("db", "redis", "llm", "worker", "object_store")

#: 组件状态 → gauge 值
STATUS_OK = 1.0
STATUS_DEGRADED = 0.5
STATUS_DOWN = 0.0

_SRE_COMPONENT_STATUS = Gauge(
    "sre_health_component_status",
    "Component health (1=ok, 0.5=degraded, 0=down). Provisional 1 until "
    "first refresh; alert on staleness via "
    "sre_health_refresh_timestamp_seconds.",
    ["component"],
)

#: 最近一次组件探测的 wall time（time.time()）。staleness 告警的锚点。
_SRE_HEALTH_REFRESH_TS = Gauge(
    "sre_health_refresh_timestamp_seconds",
    "Unix time of the last SRE component probe refresh. "
    "Stale value (>120s) means the health pipeline is not running.",
)

_SRE_STUCK_JOBS = Gauge(
    "sre_stuck_jobs",
    "Jobs in running/cancelling without heartbeat beyond the stale "
    "threshold (bounded count).",
)

#: 出口时长（export pipeline 观测面；无业务 label，防高基数）
_SRE_EXPORT_DURATION = Histogram(
    "sre_export_duration_seconds",
    "Export pipeline duration in seconds.",
    buckets=(0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)

_SRE_REFRESH_TOTAL = Counter(
    "sre_health_refresh_total",
    "Total SRE component probe refreshes.",
)

#: 已知 export 计数（与 /metrics 分离的进程内轻量观测点；export 管线
#: 接入时调用 observe_export_duration）
_SRE_EXPORT_DURATION_EXPOSED = _SRE_EXPORT_DURATION


def set_component_status(component: str, value: float) -> None:
    """写组件状态（词表外 component 显式拒绝——不静默扩张标签空间）。"""
    if component not in SRE_COMPONENTS:
        raise ValueError(f"unknown sre component: {component!r}")
    if value not in (STATUS_OK, STATUS_DEGRADED, STATUS_DOWN):
        raise ValueError(f"invalid sre status value: {value!r}")
    _SRE_COMPONENT_STATUS.labels(component=component).set(value)


def set_refresh_timestamp() -> None:
    import time

    _SRE_HEALTH_REFRESH_TS.set(time.time())
    _SRE_REFRESH_TOTAL.inc()


def set_stuck_jobs(count: int) -> None:
    _SRE_STUCK_JOBS.set(max(0, int(count)))


def observe_export_duration(seconds: float) -> None:
    _SRE_EXPORT_DURATION_EXPOSED.observe(max(0.0, float(seconds)))


def seed_defaults() -> None:
    """导入期初始化：全部组件临时 ok（文档化临时值）+ 时间戳置 0。

    时间戳 0 → staleness 告警立即成立，直到首次真实刷新——这就是
    "临时值不会被当成健康事实" 的结构性保证（M-4）。
    """
    for component in SRE_COMPONENTS:
        _SRE_COMPONENT_STATUS.labels(component=component).set(STATUS_OK)
    _SRE_HEALTH_REFRESH_TS.set(0)


seed_defaults()

__all__ = [
    "SRE_COMPONENTS",
    "STATUS_OK",
    "STATUS_DEGRADED",
    "STATUS_DOWN",
    "set_component_status",
    "set_refresh_timestamp",
    "set_stuck_jobs",
    "observe_export_duration",
    "seed_defaults",
]
