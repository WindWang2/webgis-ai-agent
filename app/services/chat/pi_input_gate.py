"""Pi 边界 pre-dispatch 参数校验闸（ADR-0180 D3/D4）。

在 ``agent_pi_bridge._dispatch_tool_bound`` 中、ToolDispatchService.dispatch
**之前**运行：schema 非法调用在 dedup / wave 排队 / ref 解析之前被拒，
返回机器可读 typed error（``SCHEMA_VALIDATION_REJECTED``），不再伪装成
工具业务失败走完执行管线。

零漂移纪律（D4）：本闸不发明任何校验规则 ——
- 归一化复用 ``argument_normalization.normalize_tool_arguments``（dispatch
  内部同一函数，同一声明表）；
- 全量档直接 ``model_validate`` registry 注入的**同一** Pydantic model
  （``registry.args_model()``）；
- 分层规则只拒绝「dispatch 内部注定拒绝」的输入，宁可漏拒（registry
  仍是最终权威）绝不误拒（ref 游标/会话别名会把字符串叶替换成任意
  载荷，字符串值一律不做类型/enum 误判）。

fail-closed / fallback：本闸任何自身异常由调用方吞掉放行（闸是前置
快路径，不是替代；registry 校验在 dispatch 内保持原样）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

#: typed error 契约（T7）：details.error / details.code 固定值。
GATE_ERROR_CODE = "SCHEMA_VALIDATION_REJECTED"
GATE_ERROR_KEY = "schema_validation_rejected"

#: 无字符串叶升级档的参数树遍历节点预算（超大树直接跳过升级档 ——
#: 与 registry oversized 旁路同方向的保守行为，绝不 O(树) 深扫）。
_GATE_WALK_NODE_BUDGET = 4096


def _model_config_extra(model: type) -> Any:
    config = getattr(model, "model_config", None)
    if isinstance(config, dict):
        return config.get("extra")
    return getattr(config, "extra", None) if config is not None else None


def _has_string_leaves(node: Any, budget: list[int]) -> bool:
    """有界遍历：参数树是否含字符串叶（可能被 ref 解析替换）。

    预算耗尽按 True 处理（保守：放弃升级档，不误拒）。
    """
    if budget[0] <= 0:
        return True
    budget[0] -= 1
    if isinstance(node, str):
        return True
    if isinstance(node, dict):
        for v in node.values():
            if _has_string_leaves(v, budget):
                return True
        return False
    if isinstance(node, (list, tuple)):
        for v in node:
            if _has_string_leaves(v, budget):
                return True
    return False


_SCALAR_ONLY_ANNOTATION_TYPES = frozenset({str, int, float, bool})


def _scalar_only_annotation(ann: Any) -> bool:
    """注解是否只允许标量（str/int/float/bool/None 的组合）。

    保守：Union 混入任何非标量分支 / 未知形态 → False（宁漏拒不误拒）。
    """
    if ann in _SCALAR_ONLY_ANNOTATION_TYPES:
        return True
    origin = getattr(ann, "__origin__", None)
    if origin is Union:
        args = [a for a in getattr(ann, "__args__", ()) if a is not type(None)]
        return bool(args) and all(a in _SCALAR_ONLY_ANNOTATION_TYPES for a in args)
    return False


def _structural_mismatch_issues(model: type, args: dict) -> list[dict[str, Any]]:
    """容器值配标量注解：解析保持容器性 → registry 注定拒绝（确定性）。"""
    issues: list[dict[str, Any]] = []
    for fname, finfo in model.model_fields.items():
        if fname not in args:
            continue
        val = args[fname]
        if not isinstance(val, (dict, list, tuple)):
            continue
        if _scalar_only_annotation(finfo.annotation):
            issues.append(_issue(
                fname, val,
                str(getattr(finfo.annotation, "__name__", finfo.annotation) or "scalar"),
                "container value cannot be resolved into a scalar",
            ))
    return issues


def _probe_issues(model: type, args: dict) -> list[dict[str, Any]]:
    """确定性硬错探针：非字符串、子树无字符串叶的值 → registry 同款
    per-field TypeAdapter 探针（#1113 P3-3 bypass 路径的同一helper）。

    parity 论证：
    - 字符串值/含字符串叶的子树跳过 —— ref: 游标与会话别名都会把字符串
      叶替换成任意载荷，边界无法判定最终形态；
    - 其余值解析不 touching（只替换字符串叶）→ 值在校验时点不变；
      registry 的 field 校验（同一 TypeAdapter）必同样拒绝 —— 零漂移、
      零误拒，且覆盖 Optional/Union/Literal/ge/le 全部注解形态。
    """
    issues: list[dict[str, Any]] = []
    try:
        from app.tools.registry import _field_type_adapter
    except Exception:  # noqa: BLE001 — helper 缺席 = 探针层整体跳过（fail-open）
        return issues
    for fname, finfo in model.model_fields.items():
        if fname not in args:
            continue
        val = args[fname]
        if val is None or isinstance(val, str):
            continue
        if _has_string_leaves(val, [256]):
            continue
        ann = finfo.annotation
        if _annotation_is_any(ann):
            continue
        try:
            adapter = _field_type_adapter(model, fname, ann, tuple(finfo.metadata))
            adapter.validate_python(val)
        except Exception as probe_error:  # noqa: BLE001 — 探针失败按「无意见」放行
            if _is_validation_error(probe_error):
                _errors = probe_error.errors() or [{}]
                _first = _errors[0]
                loc = ".".join(str(i) for i in _first.get("loc", ()))
                issues.append(_issue(
                    loc or fname, val, str(getattr(ann, "__name__", ann) or "schema"),
                    f"field-local schema violation: {str(_first.get('msg', ''))[:120]}"
                    if _first.get("msg") else str(probe_error)[:120],
                ))
    return issues


def _is_validation_error(exc: Exception) -> bool:
    try:
        from pydantic import ValidationError

        return isinstance(exc, ValidationError)
    except Exception:  # noqa: BLE001
        return False


def _annotation_is_any(ann: Any) -> bool:
    try:
        from app.tools.registry import _annotation_is_any as _is_any

        return bool(_is_any(ann))
    except Exception:  # noqa: BLE001 — helper 缺席按保守（不跳过 → 走探针）
        return False


def _issue(path: str, value: Any, expected: str, message: str) -> dict[str, Any]:
    shown = repr(value)
    if len(shown) > 80:
        shown = shown[:77] + "..."
    return {
        "path": path,
        "message": message[:200],
        "expected": expected,
        "got_type": type(value).__name__,
        "got_value": shown,
    }


def validate_pi_tool_arguments(
    registry: Any,
    tool_name: str,
    arguments: Any,
) -> Optional[dict[str, Any]]:
    """校验 Pi 面工具调用参数。通过返回 ``None``；拒绝返回机器可读报告。

    报告形状（进 PiToolResponse.details，供模型自愈与 metrics 计数）::
        {"tool": str, "issues": [{path,message,expected,got_type,got_value}],
         "normalized_repairs": [str,...]}

    无 args model 的工具（registry 同样跳过校验）恒通过；归一化/探针
    自身异常恒通过（fail-open 到 registry 权威校验）。
    """
    try:
        model = registry.args_model(tool_name)
        if model is None:
            return None
        if not isinstance(arguments, dict):
            # 非 dict 参数：registry 会 json.loads / 报 VALIDATION_ERROR；
            # 本闸只处理 dict 形态（Pi 面进来的 arguments 已是 dict）。
            return None

        from app.tools.argument_normalization import normalize_tool_arguments

        args, repairs = normalize_tool_arguments(tool_name, arguments, model)

        issues: list[dict[str, Any]] = []
        # oversized 判据一次取定（review P1/P2-2）：registry #699 旁路对
        # oversized 载荷**跳过 #828 unknown-field 检查与全量 model_validate**
        # （kwargs 容忍签名如 heatmap_data 会带着多余键照常执行）——闸在
        # oversized 下必须豁免规则 1 与升级档 4，否则误拒 master 会执行的
        # 调用；规则 2（required）与 3a（结构错位）与 bypass 探针同语义，
        # 保留；规则 3b（field 探针）与 bypass 探针完全同款，跳过以免
        # 热路径双花成本（registry 会做同样的探针）。
        oversized = _args_oversized(args)

        # 1) unknown-field（#828 同语义：extra != "allow" 时显式拒绝）。
        #    归一化只折叠键名（kebab→snake、别名→声明字段），解析只换值
        #    不加键 —— 此处键面与 registry 校验时点一致。
        #    oversized 豁免：registry #699 旁路跳过 #828（见上）。
        if not oversized and _model_config_extra(model) != "allow":
            allowed = set(model.model_fields.keys())
            unknown = sorted(k for k in args.keys() if k not in allowed)
            if unknown:
                issues.append({
                    "path": "(root)",
                    "message": (
                        f"unknown parameter(s): {', '.join(unknown)}; "
                        f"allowed: {', '.join(sorted(allowed))}"
                    )[:400],
                    "expected": "documented parameters only",
                    "got_type": "unknown_fields",
                    "got_value": ", ".join(unknown)[:200],
                })

        # 2) required 缺失（解析不删键；capture_ref_of 只注入声明过的
        #    可选字段 —— required 缺失在 registry 校验时点同样缺失；
        #    bypass 探针同样强制 required，parity 成立）。
        for fname, finfo in model.model_fields.items():
            if finfo.is_required() and fname not in args:
                issues.append({
                    "path": fname,
                    "message": "required parameter missing",
                    "expected": str(getattr(finfo.annotation, "__name__", finfo.annotation)),
                    "got_type": "missing",
                    "got_value": None,
                })

        # 3) 确定性硬错探针：
        #    (a) 结构错位 —— 值是 dict/list 而注解只允许标量：解析只替换
        #        字符串叶、绝不把容器变成标量 → 无论嵌套字符串与否都注定
        #        无效（registry 同拒；O(#fields) 廉价，oversized 保留）；
        #    (b) field 探针 —— 非字符串且子树无字符串叶的值用 registry
        #        同款 TypeAdapter 探针；含字符串叶子树跳过（ref/别名可能
        #        重写，边界不抢答）。oversized 跳过：registry bypass 会对
        #        同一批字段做完全相同的探针（避免热路径双花）。
        if not issues:
            issues.extend(_structural_mismatch_issues(model, args))
            if not oversized:
                issues.extend(_probe_issues(model, args))

        # 4) 升级档：参数树无任何字符串叶（解析恒等）→ 与 registry 校验
        #    时点语义完全一致，直接全量 model_validate；oversized 跳过
        #    （registry 对 oversized 本就旁路深校验）。
        if not issues and not oversized:
            budget = [_GATE_WALK_NODE_BUDGET]
            if not _has_string_leaves(args, budget):
                try:
                    model.model_validate(args)
                except Exception as exc:  # ValidationError 家族
                    issues.extend(_pydantic_issues(exc))

        if not issues:
            return None
        return {
            "tool": tool_name,
            "issues": issues[:16],
            "normalized_repairs": [f"{r.kind}:{r.source}->{r.target}" for r in repairs][:8],
        }
    except Exception:  # noqa: BLE001 — 闸自身故障绝不阻断合法调用
        logger.debug(
            "[PiInputGate] gate skipped tool=%s", tool_name, exc_info=True
        )
        return None


def _args_oversized(args: dict) -> bool:
    try:
        from app.tools.registry import _is_args_oversized

        return bool(_is_args_oversized(args))
    except Exception:  # noqa: BLE001 — 判据缺席按非 oversized（保守降级到规则 1-3）
        return False


def _pydantic_issues(exc: Exception) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for error in getattr(exc, "errors", lambda: [])()[:16]:
        loc = ".".join(str(i) for i in error.get("loc", ()))
        issues.append({
            "path": loc or "(root)",
            "message": str(error.get("msg", ""))[:200],
            "expected": str(error.get("type", ""))[:64],
            "got_type": type(error.get("input")).__name__,
            "got_value": repr(error.get("input"))[:80],
        })
    return issues or [{
        "path": "(root)",
        "message": str(exc)[:200],
        "expected": "schema-conformant arguments",
        "got_type": "invalid",
        "got_value": None,
    }]


def gate_reject_response_text(tool_name: str, report: dict[str, Any]) -> str:
    """模型面简短文本（自愈向导）；机器可读细节在 details。"""
    parts = [f"{i['path']}: {i['message']}" for i in report.get("issues", ())[:6]]
    return (
        f"Arguments for '{tool_name}' rejected by schema validation "
        f"(not a tool failure — fix the arguments and retry): "
        + "; ".join(parts)
    )
