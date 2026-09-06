"""模型可见 schema 压缩（ADR-0101 Wave 3, §14）。

原则：**不隐藏任何校验性约束**（required / 类型 / enum 全保留），压缩的是
冗余文本与 pydantic 序列化噪音：

- description → 描述符 summary（作者声明的模型可见短摘要）优先，缺省用
  截断的完整描述（截断点尝试按句边界）；
- 参数描述同理有界化；
- pydantic 序列化噪音字段剥离（additionalProperties / $defs / title /
  default 展示形态不参与模型行为判定的可省字段按级别剥离）；
- 超 long enum 只保留前 N 项 + 「…共 N 项」提示（enum 是校验约束，不能
  乱删 —— 但 200 项的 enum 对模型是有害噪音，截断必须同时改写 description
  提示完整列表可经 list_available_tools 获取……不：enum 截断会改变校验
  语义 —— 因此本层**只**在 schema 附带 enum 时截断展示并在缺失项处保留
  计数提示；registry 校验仍按完整 enum 执行）。

级别：
- none   原样（注册 schema 本身）；
- compact  文本有界化 + 噪音剥离 + 长 enum 截断（默认）；
- minimal  compact 基础上再剥参数 default/描述（角色视图：subagent 等）。

压缩纯函数、确定性；字节度量走 registry.schema_size 的同款序列化约定。
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DESC_MAX_CHARS = 300
_PARAM_DESC_MAX_CHARS = 160
_ENUM_MAX_ITEMS = 12
_SUMMARY_MAX_CHARS = 160


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # 句边界优先（。/!/?/.）
    for sep in ("。", ".", "！", "!", "？", "?", "；", ";"):
        idx = cut.rfind(sep)
        if idx >= max_chars // 2:
            return cut[:idx + 1]
    return cut.rstrip() + "…"


def _shrink_enum(values: List[Any], max_items: int = _ENUM_MAX_ITEMS) -> Tuple[List[Any], int]:
    """展示截断 + 截断计数（提示进 description，绝不伪造 enum 成员 —— review R1
    MAJOR：模型按 schema 合法输出伪成员必然 VALIDATION_ERROR）。"""
    if len(values) <= max_items:
        return values, 0
    return list(values[:max_items]), len(values) - max_items


def compress_schema(
    schema: Dict[str, Any],
    level: str = "compact",
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    """返回压缩后的 schema（深拷贝，不修改注册原版）。"""
    if level == "none":
        # review R1 minor：注册 schema 是活引用 —— 按本函数契约返回深拷贝，
        # 防止投影消费方就地修改污染注册表。
        return copy.deepcopy(schema)
    out = copy.deepcopy(schema)
    fn = out.get("function")
    if not isinstance(fn, dict):
        return out

    if summary:
        fn["description"] = _truncate_text(summary, _SUMMARY_MAX_CHARS)
    else:
        fn["description"] = _truncate_text(str(fn.get("description") or ""), _DESC_MAX_CHARS)

    params = fn.get("parameters")
    if isinstance(params, dict):
        props = params.get("properties")
        if isinstance(props, dict):
            for pname, pval in list(props.items()):
                if not isinstance(pval, dict):
                    continue
                if level == "minimal":
                    pval.pop("description", None)
                    pval.pop("default", None)
                else:
                    d = pval.get("description")
                    if isinstance(d, str):
                        pval["description"] = _truncate_text(d, _PARAM_DESC_MAX_CHARS)
                # review R1 minor：minimal 继承 compact 的 enum 截断与噪音剥离
                # （此前 minimal 提前 continue，两者被跳过，违背自身契约）。
                enum = pval.get("enum")
                if isinstance(enum, list) and len(enum) > _ENUM_MAX_ITEMS:
                    shrunk, omitted = _shrink_enum(enum)
                    pval["enum"] = shrunk
                    if omitted:
                        note = f"(enum truncated; {omitted} more valid values exist server-side)"
                        existing = pval.get("description")
                        pval["description"] = (
                            f"{existing} {note}" if isinstance(existing, str) else note
                        )
                # pydantic 序列化噪音
                for noise in ("additionalProperties", "format"):
                    pval.pop(noise, None)
                # review R1 minor：$defs 仅在无残留 $ref 时可剥，否则跳过该级
                if "$defs" in pval and "$ref" not in pval:
                    pval.pop("$defs", None)
                if "allOf" in pval and len(pval) == 1 and isinstance(pval["allOf"], list):
                    # pydantic v2 $ref-free 单元素 allOf 常见于 Any/约束透传
                    inner = pval["allOf"][0] if pval["allOf"] else None
                    if isinstance(inner, dict):
                        pval.clear()
                        pval.update(inner)
        # 顶层噪音（$defs 同门：有 $ref 残留则保留）
        params.pop("additionalProperties", None)
        if "$defs" in params and "$ref" not in json.dumps(params, default=str):
            params.pop("$defs", None)
    # minimal 级别剥顶层 title 类噪音
    fn.pop("title", None)
    return out


def schema_bytes(schema: Dict[str, Any]) -> int:
    """与 registry.schema_size 同款序列化约定的字节度量。"""
    return len(json.dumps(schema, ensure_ascii=False))


def compression_report(schemas: List[Dict[str, Any]], level: str = "compact") -> Dict[str, int]:
    """压缩前后字节度量（评测/观测用）。"""
    before = sum(schema_bytes(s) for s in schemas)
    after = sum(schema_bytes(compress_schema(s, level=level)) for s in schemas)
    return {"before_bytes": before, "after_bytes": after}
