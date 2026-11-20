"""视觉上下文证据领域（G36 / ADR-0291）。

把「当前地图视口 / 框选局部 / 用户参考图」变成**可授权、可追溯、有界**
的聊天证据：evidence id 独立身份 + 内容寻址 blob + 会话绑定引用计数 +
turn 时刻解析为原生 Pi ``ImageContent``。

纪律（与 critic 截图链 ``visual_observation`` 的边界）：
- 图片是**观察/参考资料**，不是授权事实：MapSpec、数据 refs、权限与
  用户锁仍是权威；图片内文字（OCR/二维码/指令）不产生任何授权。
- 本模块不做视觉评估/评分（那是 ``visual_observation`` 的职责），只负责
  身份、授权、预算与送模通道。
- blob 复用 ``durable_blob_store`` 协议；引用计数仿 ``blob_refs`` 的
  vref JSON（独立 ``vevimg-``/``vevr-`` 前缀，不与 critic 的 vshot 混用）。
"""
from app.services.visual_context.evidence import (
    VisualEvidenceRecord,
    EvidenceRejected,
    SOURCE_TYPES,
    STATE_DRAFT,
    STATE_BOUND,
    STATE_REVOKED,
)
from app.services.visual_context.store import (
    register_evidence,
    resolve_evidence,
    revoke_evidence,
    bind_evidence_to_message,
    list_evidence,
    load_evidence_index,
)

__all__ = [
    "VisualEvidenceRecord",
    "EvidenceRejected",
    "SOURCE_TYPES",
    "STATE_DRAFT",
    "STATE_BOUND",
    "STATE_REVOKED",
    "register_evidence",
    "resolve_evidence",
    "revoke_evidence",
    "bind_evidence_to_message",
    "list_evidence",
    "load_evidence_index",
]
