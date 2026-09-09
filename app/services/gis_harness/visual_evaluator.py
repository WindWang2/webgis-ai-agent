"""Visual Evaluation Seam（V6 Wave 9）—— 软视觉评估的可控扩展点。

定位（03-visual-observation.md / Prompt §13/§40/§42）：

- 只有**确定性规则无法回答**的视觉问题（visual hierarchy / balance /
  clutter / color harmony / figure-ground clarity…）才交给视觉评估器；
  确定性布局检查（overlap/offscreen/chart 数据态）永不经此路径
  （``render_observation.derive_component_layout_findings`` 已是硬证据）。
- 评估器**只输出 VisualFinding[]**（UnifiedFinding domain="visual"），
  **不得修改 MapSpec** —— 本模块结构上不收 mapspec 参数，评估输出经
  白名单校验，任何非 finding 字段/越界条目一律丢弃（结构性保证，
  不是约定）。
- 默认关闭：``GIS_VISUAL_EVALUATOR="module:callable"`` 未配置 →
  ``get_visual_evaluator`` 返回 None → 系统行为与无此特性完全一致
  （deterministic observation 独立工作）。
- 隐私（§42）：snapshot 是本地渲染产物；是否外发由评估器实现的
  provider/privacy 策略决定，seam 不静默上传任何东西；评估失败/缺席
  时 deterministic observation 不受影响（调用方 try/except 语义同
  其他增值披露）。
- 触发策略（§40）：``should_run_visual_evaluation`` 白名单 —— 只有
  finalization / major_layout_change / map_model_change / visual_repair /
  user_request 触发；pan/zoom/普通 mutation 不触发（不每轮截图）。
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Callable, Dict, List, Optional

from app.services.gis_harness.completion.unified_findings import (
    UnifiedFinding,
)

logger = logging.getLogger(__name__)

#: 评估器注入点（与 TOOL_RETRIEVAL_SEMANTIC 同一 hook 模式）。
_EVALUATOR_SPEC = os.getenv("GIS_VISUAL_EVALUATOR", "").strip()

#: 允许触发视觉评估的事件白名单（§40：不默认每轮截图）。
VISUAL_EVALUATION_TRIGGERS = frozenset({
    "finalization",          # 成图终验
    "major_layout_change",   # 重大布局变更（组件增删/排布改写）
    "map_model_change",      # 地图模型切换
    "visual_repair",         # 视觉 finding 修复后的复验
    "user_request",          # 用户显式请求
})

_MAX_VISUAL_FINDINGS = 12
_VISUAL_DOMAINS = {"visual"}
_SEVERITIES = {"info", "warning", "error"}


def should_run_visual_evaluation(trigger: str) -> bool:
    """触发白名单（§40）：只有列名事件才做视觉 snapshot/评估。"""
    return str(trigger or "") in VISUAL_EVALUATION_TRIGGERS


def get_visual_evaluator() -> Optional[Callable[..., Any]]:
    """加载 ``GIS_VISUAL_EVALUATOR="module:callable"``；未配置/加载失败 → None。"""
    if not _EVALUATOR_SPEC or ":" not in _EVALUATOR_SPEC:
        return None
    module_name, func_name = _EVALUATOR_SPEC.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        fn = getattr(module, func_name, None)
        return fn if callable(fn) else None
    except Exception:  # noqa: BLE001 — 加载失败 = 特性缺席（诚实降级）
        logger.warning("[VisualEvaluator] load failed for %r", _EVALUATOR_SPEC,
                       exc_info=True)
        return None


def _sanitize_finding(raw: Any) -> Optional[UnifiedFinding]:
    """评估器输出白名单校验：只接受 finding 形状，强制 domain=visual。

    任何 Mapspec mutation 意图字段（mutation/intent/spec/patch/layers…）
    直接判废 —— 评估器改图的结构性防线（§13：VisualFinding[] 是唯一
    合法输出）。
    """
    if isinstance(raw, UnifiedFinding):
        item = raw.to_dict()
    elif isinstance(raw, dict):
        item = dict(raw)
    else:
        return None
    forbidden = {"mutation", "mutations", "intent", "intents", "mapspec",
                 "spec", "patch", "layers", "components_patch", "apply"}
    if any(k in item for k in forbidden):
        return None
    code = str(item.get("code") or "")
    if not code:
        return None
    severity = str(item.get("severity") or "warning")
    if severity not in _SEVERITIES:
        severity = "warning"
    return UnifiedFinding(
        domain="visual",
        code=code,
        severity=severity,
        source=str(item.get("source") or "visual_evaluator"),
        scope=str(item.get("scope") or "map"),
        affected_entity=str(item.get("affected_entity") or ""),
        evidence=str(item.get("evidence") or ""),
        repair_class=str(item.get("repair_class") or ""),
        retryable=False,   # 软评估发现不直接驱动自动重试（W10 分类裁决）
        blocks_completion=False,  # 软评估默认只降级披露（degradation）——
        degradation_only=True,    # 硬阻断仍归 deterministic 层
    )


def run_visual_evaluation(
    evaluator: Optional[Callable[..., Any]],
    snapshot: Dict[str, Any],
) -> List[UnifiedFinding]:
    """执行一次视觉评估（有界、可关停、失败诚实降级为空）。

    ``snapshot`` 由调用方组装（渲染快照引用/字节、deterministic evidence、
    scene oracle 摘要）——本函数不读取任何会话状态、不触碰 MapSpec。
    evaluator 为 None（未配置）或抛错 → 空列表（deterministic 层不受影响）。
    """
    if evaluator is None:
        return []
    try:
        raw = evaluator(snapshot)
    except Exception:  # noqa: BLE001 — 评估失败 = 无视觉发现（诚实降级）
        logger.warning("[VisualEvaluator] evaluation failed", exc_info=True)
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[UnifiedFinding] = []
    for item in raw[:_MAX_VISUAL_FINDINGS]:
        uf = _sanitize_finding(item)
        if uf is not None:
            out.append(uf)
    return out


__all__ = [
    "VISUAL_EVALUATION_TRIGGERS",
    "get_visual_evaluator",
    "run_visual_evaluation",
    "should_run_visual_evaluation",
]
