"""VisualEvidenceRecord —— 聊天图片证据的身份模型（G36 / ADR-0291）。

身份纪律：
- ``evidence_id``（``vev-<16hex>``）是**证据身份**；``derived_sha256`` 是
  送模字节的内容寻址。同字节不同地图版本/视口/用途 = 不同 evidence id
  （V18：blob 去重，身份不合并覆盖）；同一 evidence 的 origin 与 derived
  是两个 sha（origin=采集/上传字节，derived=送模派生字节，独立可审计）。
- 地图类证据（map_viewport / map_region）必须绑定 mapspec_revision +
  fingerprint + viewport identity；revision 相同但用户平移/缩放后视口
  变化 = 新证据（V04：不能拿旧视口冒充当前）。viewport_hash 是
  center/zoom/bearing/pitch 的稳定指纹，与 revision 分开判定。
- crop（map_region）记录的是**原图设备像素坐标系**里的矩形 + 采集时
  DPR；所有缩放/裁剪坐标链显式（CSS px → device px → derived px）。
- 参考图（reference_upload）不伪造地图 revision —— 这两项为空，来源
  声明恒为 reference，不进入「当前地图渲染证据」可信集合（V06）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 支持的来源类型。layout_preview 是条件支持：只有组合页链路真实接通并
#: 验收后才从 intake 白名单放行；v1 未接通 → intake 返回 typed
#: unsupported（V08），不得用视口冒充纸面。
SOURCE_TYPES = ("map_viewport", "map_region", "reference_upload", "layout_preview")
#: intake 白名单（layout_preview 未验证组合链路前不放行）。
INTAKE_SOURCE_TYPES = ("map_viewport", "map_region", "reference_upload")

STATE_DRAFT = "draft"      # 已上传未发送（有界清理）
STATE_BOUND = "bound"      # 已随消息发送（保留策略与消息一致）
STATE_REVOKED = "revoked"  # 显式撤销 tombstone（解析拒绝，记录保留）

#: 地图类来源（必须带 revision/viewport 身份）。
MAP_SOURCE_TYPES = ("map_viewport", "map_region", "layout_preview")

#: 单条证据元数据 JSON 上限（存进 session_data 的索引条目；防元数据膨胀）。
MAX_RECORD_JSON_BYTES = 4096


class EvidenceRejected(Exception):
    """证据登记/解析拒绝（reason 机器可读，message 面向用户）。"""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason


def _bounded_str(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _bounded_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def viewport_identity_hash(viewport: Optional[Dict[str, Any]]) -> str:
    """viewport 身份指纹：center/zoom/bearing/pitch 稳定序列化后 sha12。

    只取这四个自由度（MapLibre 视口状态），忽略无关键 —— 平移/缩放/旋转
    /倾斜任一变化 → 不同 hash（V04）。收敛浮点：六位小数内视为同一视口
    （浮点噪声不产生新身份）。
    """
    if not isinstance(viewport, dict) or not viewport:
        return ""
    center = viewport.get("center")
    try:
        c = [round(float(center[0]), 6), round(float(center[1]), 6)] if isinstance(center, (list, tuple)) and len(center) >= 2 else None
    except (TypeError, ValueError):
        c = None
    def _r6(v: Any) -> Optional[float]:
        try:
            return round(float(v), 6)
        except (TypeError, ValueError):
            return None
    payload = json.dumps(
        {"c": c, "z": _r6(viewport.get("zoom")), "b": _r6(viewport.get("bearing")), "p": _r6(viewport.get("pitch"))},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def mint_evidence_id() -> str:
    """``vev-<16hex>``：不可猜（secrets），不承担顺序语义。"""
    return f"vev-{secrets.token_hex(8)}"


@dataclass
class VisualEvidenceRecord:
    """单条视觉证据的持久元数据（索引条目；字节在 blob store）。"""

    evidence_id: str
    session_id: str
    source_type: str
    mime: str                          # derived mime（送模通道的媒体类型）
    origin_sha256: str                 # intake 原始字节 sha
    derived_sha256: str                # 送模派生字节 sha（blob 键）
    origin_size: int
    derived_size: int                  # 派生字节数（base64 前）
    width: int                         # derived 像素宽
    height: int                        # derived 像素高
    origin_width: int = 0
    origin_height: int = 0
    created_at: float = field(default_factory=time.time)
    # ── 地图类身份（reference_upload 恒空；V06 不伪造） ──
    mapspec_revision: Optional[int] = None
    mapspec_fingerprint: str = ""
    viewport: Dict[str, Any] = field(default_factory=dict)
    viewport_hash: str = ""
    dpr: float = 1.0                   # 采集时 devicePixelRatio
    crop: Optional[Dict[str, int]] = None   # 原图设备像素 {x,y,w,h}（map_region）
    parent_evidence_id: str = ""       # 派生自哪条证据（crop 链）
    # ── 展示/审计 ──
    label: str = ""
    state: str = STATE_DRAFT
    bound_message_id: Optional[int] = None
    # 诚实观察标记（解码面给出；不据此拒绝，但 UI/送模回执披露）
    observations: List[str] = field(default_factory=list)   # blank|transparent|…

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["VisualEvidenceRecord"]:
        """索引条目 → 记录；畸形条目诚实跳过（None），不抛。"""
        if not isinstance(data, dict):
            return None
        try:
            rec = cls(
                evidence_id=_bounded_str(data.get("evidence_id"), 40),
                session_id=_bounded_str(data.get("session_id"), 128),
                source_type=_bounded_str(data.get("source_type"), 32),
                mime=_bounded_str(data.get("mime"), 32),
                origin_sha256=_bounded_str(data.get("origin_sha256"), 64),
                derived_sha256=_bounded_str(data.get("derived_sha256"), 64),
                origin_size=_bounded_int(data.get("origin_size")),
                derived_size=_bounded_int(data.get("derived_size")),
                width=_bounded_int(data.get("width")),
                height=_bounded_int(data.get("height")),
                origin_width=_bounded_int(data.get("origin_width")),
                origin_height=_bounded_int(data.get("origin_height")),
                created_at=float(data.get("created_at") or 0.0),
                mapspec_revision=(None if data.get("mapspec_revision") is None
                                  else _bounded_int(data.get("mapspec_revision"))),
                mapspec_fingerprint=_bounded_str(data.get("mapspec_fingerprint"), 96),
                viewport=data.get("viewport") if isinstance(data.get("viewport"), dict) else {},
                viewport_hash=_bounded_str(data.get("viewport_hash"), 16),
                dpr=float(data.get("dpr") or 1.0),
                crop=(data.get("crop") if isinstance(data.get("crop"), dict) else None),
                parent_evidence_id=_bounded_str(data.get("parent_evidence_id"), 40),
                label=_bounded_str(data.get("label"), 120),
                state=_bounded_str(data.get("state"), 16) or STATE_DRAFT,
                bound_message_id=(None if data.get("bound_message_id") is None
                                  else _bounded_int(data.get("bound_message_id"))),
                observations=[_bounded_str(o, 32) for o in (data.get("observations") or [])][:8],
            )
        except (TypeError, ValueError):
            return None
        # 身份字段缺失/畸形 = 不可信条目（跳过，不让半条记录进解析链）
        if not rec.evidence_id.startswith("vev-") or rec.source_type not in SOURCE_TYPES:
            return None
        if not rec.derived_sha256 or rec.derived_size <= 0 or rec.width <= 0 or rec.height <= 0:
            return None
        return rec

    def validate_invariants(self) -> None:
        """登记期不变量；违反即拒绝（不落半条记录）。"""
        if self.source_type not in SOURCE_TYPES:
            raise EvidenceRejected("unsupported_source_type")
        if self.source_type in MAP_SOURCE_TYPES:
            # 地图类必须带 revision + viewport 身份（reference 不允许伪造）
            if self.mapspec_revision is None or self.mapspec_revision < 0:
                raise EvidenceRejected(
                    "map_evidence_requires_revision",
                    "地图证据必须绑定 mapspec revision。",
                )
            if not self.viewport_hash:
                raise EvidenceRejected(
                    "map_evidence_requires_viewport",
                    "地图证据必须绑定视口身份。",
                )
        if self.source_type == "reference_upload" and self.mapspec_revision is not None:
            # V06：参考图不允许伪造地图版本身份
            raise EvidenceRejected(
                "reference_must_not_claim_map_identity",
                "参考图不携带地图版本身份。",
            )
        if self.source_type == "map_region":
            crop = self.crop or {}
            # 非数值 crop 值（null/字符串等）按缺失处理 → typed 拒绝，
            # 不外溢 TypeError/ValueError（登记期只抛 EvidenceRejected）。
            for k in ("x", "y", "w", "h"):
                if _bounded_int(crop.get(k, -1), -1) < 0:
                    raise EvidenceRejected(
                        "region_requires_crop_rect",
                        "局部证据必须带完整裁剪矩形。",
                    )
            if _bounded_int(crop.get("w", 0), 0) <= 0 or _bounded_int(crop.get("h", 0), 0) <= 0:
                raise EvidenceRejected("region_crop_empty", "裁剪矩形为空。")
        if self.state not in (STATE_DRAFT, STATE_BOUND, STATE_REVOKED):
            raise EvidenceRejected("invalid_state")
        if len(json.dumps(self.to_dict(), ensure_ascii=False).encode("utf-8")) > MAX_RECORD_JSON_BYTES:
            raise EvidenceRejected("record_metadata_oversized")
