"""安全表达式解析与编译（G19 / WP02）。

两条输入路径汇入**同一个** ``validate_expr`` → 同一个 ``ExprNode``：
UI 构造器提交 IR dict、用户/模型输入文本公式 —— 不存在两套公式系统。

安全边界：
- 手写 tokenizer + 递归下降，**只产出 dict**；无 ``eval`` / ``exec`` /
  ``compile`` / ``Function`` / ``getattr`` / SQL 拼接 / 字符串插值；
- 字段引用是「按名字查 dict」，``{中文 字段名}`` 与空格名原样取值；
- 词表外节点/函数/算子、超预算、未知字段、保留字段、冲突列、循环/前向
  依赖、不可兼容单位一律在计算前以 ``CompileError(code, position, hint)`` 拒绝。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Sequence, Tuple, Union

from app.lib.gis.measurement import CANONICAL_UNITS

from app.services.data_preparation.field_compute.contracts import (
    BINARY_OPS, LITERAL_DTYPES, MAX_CASE_BRANCHES, MAX_COLUMNS,
    MAX_EXPR_DEPTH, MAX_EXPR_NODES, MAX_FIELD_NAME_LEN, MAX_LITERAL_STRING,
    NODE_KINDS, RESERVED_FIELD_NAMES, SAFE_INTEGER_MAX, UNARY_OPS,
    CompileError, ComputeColumn, ExprNode,
)
from app.services.data_preparation.field_compute.functions import (
    DATETIME_PARTS as PARTS, FUNCTIONS, node_count, node_depth,
    static_type, static_unit,
)

# ── Tokenizer ───────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(
    r"(?P<ws>\s+)"
    r"|(?P<braced>\{[^}]*\})"
    r"|(?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(?P<string>'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")"
    r"|(?P<ident>[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*)"
    r"|(?P<op>==|!=|<=|>=|&&|\|\||[+\-*/%()<>,!])",
    re.UNICODE,
)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    text: str
    pos: int


def _decode_string(raw: str) -> str:
    body = raw[1:-1]
    out: List[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(body):
            out.append("\\")
            break
        nxt = body[i + 1]
        if nxt == "u" and i + 5 < len(body) + 1:
            hexpart = body[i + 2:i + 6]
            if len(hexpart) == 4 and all(c in "0123456789abcdefABCDEF" for c in hexpart):
                out.append(chr(int(hexpart, 16)))
                i += 6
                continue
        out.append(_ESCAPES.get(nxt, nxt))
        i += 2
    return "".join(out)


def tokenize(text: str) -> List[Token]:
    """文本 → token 列表（不执行任何代码；未知字符立即报错并带位置）。"""
    if not isinstance(text, str):
        raise CompileError("INVALID_LITERAL", "expression must be a string")
    if len(text) > 8192:
        raise CompileError("BUDGET_EXCEEDED", "expression text exceeds 8192 chars",
                           position=8192, hint="请缩短表达式或拆成多列")
    tokens: List[Token] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if m is None:
            raise CompileError("UNKNOWN_NODE",
                               f"非法字符 {text[pos]!r}（只允许数字/字符串/字段名/受限算子）",
                               position=pos, hint="字段名含特殊字符请用 {字段名} 包裹")
        kind = m.lastgroup or ""
        raw = m.group()
        if kind != "ws":
            tokens.append(Token(kind, raw, pos))
        pos = m.end()
    tokens.append(Token("eof", "", len(text)))
    return tokens


class _Parser:
    def __init__(self, tokens: Sequence[Token]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> Token:
        return self.tokens[self.i]

    def take(self) -> Token:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def accept(self, text: str) -> bool:
        if self.peek().text == text:
            self.take()
            return True
        return False

    def expect(self, text: str) -> Token:
        tok = self.peek()
        if tok.text != text:
            raise CompileError("UNKNOWN_NODE", f"期望 {text!r}，实际 {tok.text!r}",
                               position=tok.pos, hint="检查括号与逗号")
        return self.take()

    def parse(self) -> Dict[str, Any]:
        node = self.parse_or()
        tail = self.peek()
        if tail.kind != "eof":
            raise CompileError("UNKNOWN_NODE", f"多余内容 {tail.text!r}",
                               position=tail.pos, hint="表达式结尾有多余 token")
        return node

    def _binary(self, sub, ops: Dict[str, str]):
        node = sub()
        while self.peek().text in ops:
            tok = self.take()
            right = sub()
            node = {"kind": "binary", "op": ops[tok.text],
                    "left": node, "right": right}
        return node

    def parse_or(self):
        return self._binary(self.parse_and, {"or": "or", "||": "or"})

    def parse_and(self):
        return self._binary(self.parse_comparison, {"and": "and", "&&": "and"})

    def parse_comparison(self):
        return self._binary(self.parse_additive, {
            "==": "eq", "!=": "ne", "<=": "le", ">=": "ge",
            "<": "lt", ">": "gt",
        })

    def parse_additive(self):
        return self._binary(self.parse_multiplicative, {"+": "add", "-": "sub"})

    def parse_multiplicative(self):
        return self._binary(self.parse_unary, {"*": "mul", "/": "div", "%": "mod"})

    def parse_unary(self):
        tok = self.peek()
        if tok.text in ("not", "!"):
            self.take()
            return {"kind": "unary", "op": "not", "arg": self.parse_unary()}
        if tok.text == "-":
            self.take()
            arg = self.parse_unary()
            if arg.get("kind") == "lit" and arg.get("dtype") == "number":
                return {"kind": "lit", "dtype": "number",
                        "value": -arg["value"]}
            return {"kind": "unary", "op": "neg", "arg": arg}
        return self.parse_primary()

    def parse_primary(self) -> Dict[str, Any]:
        tok = self.peek()
        if tok.text == "(":
            self.take()
            node = self.parse_or()
            self.expect(")")
            return node
        if tok.kind == "braced":
            self.take()
            name = tok.text[1:-1]
            if not name or len(name) > MAX_FIELD_NAME_LEN:
                raise CompileError("UNKNOWN_FIELD", "花括号字段名为空或过长",
                                   position=tok.pos, hint=f"字段名 ≤{MAX_FIELD_NAME_LEN} 字符")
            return {"kind": "field", "name": name}
        if tok.kind == "number":
            self.take()
            value: Union[int, float] = float(tok.text) if any(
                c in tok.text for c in ".eE") else int(tok.text)
            if isinstance(value, int) and abs(value) > SAFE_INTEGER_MAX:
                raise CompileError("UNSAFE_INTEGER",
                                   "整数字面量超过 JS 安全整数范围（2^53-1）",
                                   position=tok.pos,
                                   hint="请用字符串字面量 + to_integer()，或改用字段引用")
            return {"kind": "lit", "dtype": "number", "value": value}
        if tok.kind == "string":
            self.take()
            return {"kind": "lit", "dtype": "string",
                    "value": _decode_string(tok.text)}
        if tok.kind == "ident":
            self.take()
            low = tok.text.lower()
            if low == "true":
                return {"kind": "lit", "dtype": "boolean", "value": True}
            if low == "false":
                return {"kind": "lit", "dtype": "boolean", "value": False}
            if low == "null":
                return {"kind": "lit", "dtype": "null", "value": None}
            if low in ("and", "or", "not"):
                raise CompileError("UNKNOWN_NODE", f"逻辑关键字 {tok.text!r} 位置不合法",
                                   position=tok.pos, hint="逻辑运算需要左右操作数")
            if self.peek().text == "(":
                self.take()
                args: List[Dict[str, Any]] = []
                if not self.accept(")"):
                    while True:
                        args.append(self.parse_or())
                        if self.accept(","):
                            continue
                        self.expect(")")
                        break
                return {"kind": "call", "name": tok.text, "args": args}
            return {"kind": "field", "name": tok.text}
        if tok.kind == "op" and tok.text == "=":
            raise CompileError("UNKNOWN_OPERATOR", "单等号不是合法算子（比较请用 ==）",
                               position=tok.pos, hint="使用 == / !=")
        raise CompileError("UNKNOWN_NODE", f"无法解析 {tok.text!r}",
                           position=tok.pos, hint="检查表达式语法")


def parse_expression(text: str) -> Dict[str, Any]:
    """文本公式 → 未校验 IR dict（不执行任何代码）。"""
    return _Parser(tokenize(text)).parse()


# ── 编译上下文与语义校验 ───────────────────────────────────────────────────

@dataclass(slots=True)
class CompileContext:
    available_fields: Tuple[str, ...] = ()
    field_types: Dict[str, str] = dc_field(default_factory=dict)
    field_units: Dict[str, str] = dc_field(default_factory=dict)  # user-declared
    computed_columns: Tuple[str, ...] = ()  # 本列之前已声明的计算列
    timezone: str = "UTC"
    date_format: str = ""
    crs: str = ""

    @property
    def known_fields(self) -> set[str]:
        return set(self.available_fields) | set(self.computed_columns)


def _err(code: str, detail: str, *, pos: int = -1, hint: str = "") -> CompileError:
    return CompileError(code, detail, position=pos, hint=hint)


def validate_expr(data: Any, ctx: CompileContext) -> ExprNode:
    """IR dict → 通过语义/预算/词表校验的 ``ExprNode``（fail-closed）。"""
    if not isinstance(data, dict):
        raise _err("UNKNOWN_NODE", "expression node must be a dict")
    kind = str(data.get("kind") or "")
    if kind not in NODE_KINDS:
        raise _err("UNKNOWN_NODE", f"unknown node kind: {kind!r}",
                   hint=f"允许的节点：{sorted(NODE_KINDS)}")
    try:
        node = ExprNode.model_validate({
            "kind": kind,
            "dtype": data.get("dtype") or "",
            "value": data.get("value"),
            "name": str(data.get("name") or ""),
            "op": str(data.get("op") or ""),
            "arg": data.get("arg"),
            "left": data.get("left"),
            "right": data.get("right"),
            "args": tuple(data.get("args") or ()),
            "branches": tuple((c, v) for c, v in (data.get("branches") or [])),
            "default": data.get("default"),
        })
    except Exception as exc:  # noqa: BLE001
        raise _err("UNKNOWN_NODE", f"malformed {kind} node: {exc}"[:240],
                   hint="检查节点字段与嵌套结构") from exc

    if kind == "lit":
        _validate_literal(node)
    elif kind == "field":
        _validate_field(node, ctx)
    elif kind == "unary":
        if node.op not in UNARY_OPS:
            raise _err("UNKNOWN_OPERATOR", f"unknown unary op: {node.op!r}")
        if node.arg is None:
            raise _err("UNKNOWN_NODE", "unary node requires arg")
        node.arg = validate_expr(node.arg.model_dump(exclude_none=True)
                                 if isinstance(node.arg, ExprNode) else node.arg, ctx)
    elif kind == "binary":
        if node.op not in BINARY_OPS:
            raise _err("UNKNOWN_OPERATOR", f"unknown binary op: {node.op!r}")
        if node.left is None or node.right is None:
            raise _err("UNKNOWN_NODE", "binary node requires left/right")
        node.left = validate_expr(node.left.model_dump(exclude_none=True)
                                  if isinstance(node.left, ExprNode) else node.left, ctx)
        node.right = validate_expr(node.right.model_dump(exclude_none=True)
                                   if isinstance(node.right, ExprNode) else node.right, ctx)
        _validate_binary_types(node)
    elif kind == "call":
        node.args = tuple(validate_expr(
            a.model_dump(exclude_none=True) if isinstance(a, ExprNode) else a, ctx)
            for a in node.args)
        _validate_call(node, ctx)
    else:  # case
        if len(node.branches) > MAX_CASE_BRANCHES:
            raise _err("BUDGET_EXCEEDED", f"case branches > {MAX_CASE_BRANCHES}")
        if node.default is None:
            raise _err("UNKNOWN_NODE", "case node requires default")
        pairs = []
        for cond, val in node.branches:
            c = validate_expr(cond.model_dump(exclude_none=True)
                              if isinstance(cond, ExprNode) else cond, ctx)
            v = validate_expr(val.model_dump(exclude_none=True)
                              if isinstance(val, ExprNode) else val, ctx)
            if static_type(c, ctx.field_types) not in ("boolean", "any"):
                raise _err("TYPE_MISMATCH", "case condition must be boolean")
            pairs.append((c, v))
        node.branches = tuple(pairs)
        node.default = validate_expr(node.default.model_dump(exclude_none=True)
                                     if isinstance(node.default, ExprNode)
                                     else node.default, ctx)

    _validate_budget(node)
    info = static_unit(node, ctx.field_units)
    if info.status == "incompatible":
        raise _err("UNIT_INCOMPATIBLE",
                   "加减/分支两侧单位量纲不兼容",
                   hint="用 convert_unit() 显式换算，或在 field_units 中声明单位")
    if node.kind == "binary" and node.op in ("add", "sub") \
            and node.left is not None and node.right is not None:
        lu = static_unit(node.left, ctx.field_units)
        ru = static_unit(node.right, ctx.field_units)
        if lu.unit and ru.unit and lu.unit != ru.unit:
            raise _err("UNIT_INCOMPATIBLE",
                       f"加减两侧单位不同（{lu.unit} vs {ru.unit}）",
                       hint="同量纲不同标尺也必须先用 convert_unit() 显式换算")
    return node


def _validate_literal(node: ExprNode) -> None:
    if node.dtype not in LITERAL_DTYPES:
        raise _err("INVALID_LITERAL", f"unknown literal dtype: {node.dtype!r}")
    v = node.value
    if node.dtype == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise _err("INVALID_LITERAL", "numeric literal must be int/float (not bool)")
        if isinstance(v, float) and not math.isfinite(v):
            raise _err("INVALID_LITERAL", "NaN/Infinity 不是合法 JSON 数字")
        if isinstance(v, int) and abs(v) > SAFE_INTEGER_MAX:
            raise _err("UNSAFE_INTEGER", "整数字面量超过 2^53-1",
                       hint="用字符串 + to_integer() 保持精度")
    elif node.dtype == "string":
        if not isinstance(v, str):
            raise _err("INVALID_LITERAL", "string literal must be str")
        if len(v) > MAX_LITERAL_STRING:
            raise _err("BUDGET_EXCEEDED", f"string literal > {MAX_LITERAL_STRING}")
    elif node.dtype == "boolean":
        if not isinstance(v, bool):
            raise _err("INVALID_LITERAL", "boolean literal must be bool")
    else:
        if v is not None:
            raise _err("INVALID_LITERAL", "null literal must have value=None")


def _validate_field(node: ExprNode, ctx: CompileContext) -> None:
    name = node.name
    if not name or len(name) > MAX_FIELD_NAME_LEN:
        raise _err("UNKNOWN_FIELD", "字段名为空或过长")
    if name in RESERVED_FIELD_NAMES or name.startswith("__"):
        raise _err("RESERVED_FIELD", f"保留字段不可引用/写入：{name!r}")
    if name not in ctx.known_fields:
        raise _err("UNKNOWN_FIELD", f"字段 {name!r} 不在输入 schema 或已声明计算列中",
                   hint=f"可用字段：{sorted(ctx.known_fields)[:12]}")


def _validate_binary_types(node: ExprNode) -> None:
    # 只拒绝「静态上不可能成立」的组合；字段类型在运行时按实际值严格判定。
    if node.op in ("add", "sub", "mul", "div", "mod"):
        for side in (node.left, node.right):
            if side is not None and side.kind == "lit" and side.dtype in ("boolean", "null", "string"):
                raise _err("TYPE_MISMATCH",
                           f"算术算子 {node.op} 不接受 {side.dtype} 字面量",
                           hint="用 to_number() 显式转换；布尔/空串不会自动变数值")
    if node.op in ("and", "or"):
        for side in (node.left, node.right):
            if side is not None and side.kind == "lit" and side.dtype != "boolean":
                raise _err("TYPE_MISMATCH", "逻辑算子只接受布尔")


def _validate_call(node: ExprNode, ctx: CompileContext) -> None:
    spec = FUNCTIONS.get(node.name)
    if spec is None:
        raise _err("UNKNOWN_FUNCTION", f"未注册函数：{node.name!r}",
                   hint="函数词表见 field_compute_catalog；禁止 import/文件/任意调用")
    n = len(node.args)
    if n < spec.min_args or n > spec.max_args:
        raise _err("INVALID_ARITY",
                   f"{node.name} 需要 {spec.min_args}–{spec.max_args} 个参数，实际 {n}")
    if n > 4:
        raise _err("BUDGET_EXCEEDED", "参数量超限")
    # 编译期字面量参数（单位/时间 part/格式与时区）
    if node.name == "convert_unit":
        for idx, role in ((1, "from_unit"), (2, "to_unit")):
            arg = node.args[idx]
            if arg.kind != "lit" or arg.dtype != "string":
                raise _err("INVALID_LITERAL", f"{role} 必须是字面量字符串")
            if str(arg.value) not in CANONICAL_UNITS:
                raise _err("UNKNOWN_UNIT", f"未注册单位：{arg.value!r}",
                           hint=f"可用单位：{sorted(CANONICAL_UNITS)[:12]}")
        from_unit, to_unit = str(node.args[1].value), str(node.args[2].value)
        if dimension_of(from_unit) != dimension_of(to_unit):
            raise _err("UNIT_INCOMPATIBLE", f"{from_unit} 与 {to_unit} 量纲不同",
                       hint="convert_unit 只做同量纲换算")
    if node.name in ("min", "max"):
        lu = static_unit(node.args[0], ctx.field_units)
        ru = static_unit(node.args[1], ctx.field_units)
        if lu.unit and ru.unit and (lu.dimension != ru.dimension or lu.unit != ru.unit):
            raise _err("UNIT_INCOMPATIBLE", "min/max 两侧单位不同，先换算")
    if node.name == "datetime_part":
        arg = node.args[1]
        if arg.kind != "lit" or arg.dtype != "string" or str(arg.value) not in PARTS:
            raise _err("INVALID_LITERAL", "part 必须是字面量且属于 DATETIME_PARTS",
                       hint=f"可用 part：{sorted(PARTS)}")
    if node.name == "parse_datetime":
        from app.services.data_preparation.field_compute.interpreter import (
            is_supported_timezone,
        )
        if n == 1 and (not ctx.date_format or not ctx.timezone):
            raise _err("AMBIGUOUS_DATETIME",
                       "单参数 parse_datetime 需要计划级 date_format + timezone",
                       hint="用 parse_datetime(s, '格式', '时区') 或在计划里声明")
        if n == 2:
            raise _err("AMBIGUOUS_DATETIME", "格式与时区必须同时声明（2 参不合法）")
        fmt = str(node.args[1].value) if n == 3 else ctx.date_format
        tz = str(node.args[2].value) if n == 3 else ctx.timezone
        if "%m" in fmt and "%d" in fmt and "%Y" not in fmt and "%y" not in fmt:
            raise _err("AMBIGUOUS_DATETIME", "格式缺少年份，月日顺序含糊")
        if not is_supported_timezone(tz):
            raise _err("UNKNOWN_TIMEZONE", f"时区 {tz!r} 未声明或本机不可用",
                       hint="使用 UTC / +08:00 / -05:30 等显式时区")
        if "%z" in fmt or "%Z" in fmt:
            raise _err("AMBIGUOUS_DATETIME", "格式内嵌时区不允许；用显式 tz 参数")
    if spec.requires_datetime_context and node.name != "parse_datetime":
        if not ctx.timezone:
            raise _err("AMBIGUOUS_DATETIME", "缺少声明时区")


def _validate_budget(node: ExprNode) -> None:
    # 每次递归都检查（超深/超节点在最外层也会命中，这里尽早拒绝）。
    if node_depth(node) > MAX_EXPR_DEPTH:
        raise _err("BUDGET_EXCEEDED", f"AST 深度 > {MAX_EXPR_DEPTH}",
                   hint="拆分到多个计算列")
    if node_count(node) > MAX_EXPR_NODES:
        raise _err("BUDGET_EXCEEDED", f"AST 节点 > {MAX_EXPR_NODES}",
                   hint="拆分到多个计算列")


def dimension_of(unit: str) -> str:
    entry = CANONICAL_UNITS.get(unit)
    return entry.dimension.value if entry is not None else ""


_OP_SYMBOLS = {
    "add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%",
    "eq": "==", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">=",
    "and": "and", "or": "or",
}


def render_expression(node: ExprNode) -> str:
    """IR → 人类可读公式摘要（UI/Pi/收据共用；不是可执行代码）。"""
    if node.kind == "lit":
        if node.dtype == "string":
            return json.dumps(node.value, ensure_ascii=False)
        if node.dtype == "null":
            return "null"
        if node.dtype == "boolean":
            return "true" if node.value else "false"
        return str(node.value)
    if node.kind == "field":
        return "{" + node.name + "}"
    if node.kind == "unary":
        inner = render_expression(node.arg) if node.arg is not None else "?"
        return f"(-{inner})" if node.op == "neg" else f"not {inner}"
    if node.kind == "binary":
        lhs = render_expression(node.left) if node.left is not None else "?"
        rhs = render_expression(node.right) if node.right is not None else "?"
        return f"({lhs} {_OP_SYMBOLS.get(node.op, node.op)} {rhs})"
    if node.kind == "call":
        return f"{node.name}(" + ", ".join(render_expression(a) for a in node.args) + ")"
    parts = [f"if {render_expression(c)} then {render_expression(v)}"
             for c, v in node.branches]
    parts.append(f"else {render_expression(node.default) if node.default else 'null'}")
    return "case " + "; ".join(parts)


def compile_expression(source: Union[str, Dict[str, Any], ExprNode],
                       ctx: CompileContext) -> ExprNode:
    """文本公式 / UI 构造器 IR dict / 已解析节点 → **同一个** ExprNode。"""
    if isinstance(source, ExprNode):
        data = source.model_dump(exclude_none=True)
    elif isinstance(source, dict):
        data = source
    else:
        data = parse_expression(str(source))
    return validate_expr(data, ctx)


def compile_columns(raw_columns: Sequence[Any], ctx: CompileContext
                    ) -> Tuple[Tuple[ComputeColumn, ...], Tuple[str, ...]]:
    """多列编译：声明顺序即确定计算顺序；只允许引用**之前**的列。

    返回 ``(columns, order)``。自引用 → CYCLIC_DEPENDENCY；引用后续列 →
    FORWARD_REFERENCE（策略确定，重放不歧义）。
    """
    if not raw_columns:
        raise _err("EMPTY_PLAN", "至少需要一个计算列")
    if len(raw_columns) > MAX_COLUMNS:
        raise _err("BUDGET_EXCEEDED", f"计算列 > {MAX_COLUMNS}")
    if ctx.timezone:
        from app.services.data_preparation.field_compute.interpreter import (
            is_supported_timezone,
        )
        if not is_supported_timezone(ctx.timezone):
            raise _err("UNKNOWN_TIMEZONE", f"计划时区 {ctx.timezone!r} 不可用",
                       hint="使用 UTC / +08:00 等显式时区；不猜本地时区")
    existing = set(ctx.available_fields)
    declared: List[str] = []
    columns: List[ComputeColumn] = []
    for idx, raw in enumerate(raw_columns):
        name = str(raw.get("name") or "").strip() if isinstance(raw, dict) else ""
        if not name:
            raise _err("RESERVED_FIELD", "计算列缺少 name")
        if name in RESERVED_FIELD_NAMES or name.startswith("__"):
            raise _err("RESERVED_FIELD", f"保留字段名：{name!r}")
        if name in existing:
            raise _err("FIELD_EXISTS", f"目标字段 {name!r} 已存在（原件不可变）",
                       hint="换一个新字段名；本任务只产出新列")
        if name in declared:
            raise _err("DUPLICATE_COLUMN", f"重复计算列：{name!r}")
        declared_unit = str(raw.get("declared_unit") or "")
        if declared_unit and declared_unit not in CANONICAL_UNITS:
            raise _err("UNKNOWN_UNIT", f"未注册输出单位：{declared_unit!r}")
        col_ctx = CompileContext(
            available_fields=ctx.available_fields,
            field_types=ctx.field_types,
            field_units=ctx.field_units,
            computed_columns=tuple(declared),
            timezone=ctx.timezone, date_format=ctx.date_format, crs=ctx.crs,
        )
        expr_src = raw.get("expression") if isinstance(raw, dict) else None
        node = compile_expression(expr_src, col_ctx)
        deps = set(d for d in _deps(node))
        if name in deps:
            raise _err("CYCLIC_DEPENDENCY", f"计算列 {name!r} 引用了自身")
        later = deps & set(str(r.get("name") or "") for r in raw_columns[idx + 1:])
        if later:
            raise _err("FORWARD_REFERENCE",
                       f"计算列 {name!r} 前向引用 {sorted(later)}",
                       hint="把被依赖列排在前面（声明顺序即计算顺序）")
        if declared_unit:
            # 已声明列的单位可参与后续列的单位传播
            ctx.field_units = dict(ctx.field_units)
            ctx.field_units[name] = declared_unit
        columns.append(ComputeColumn(
            name=name, expression=node, declared_unit=declared_unit,
            description=str(raw.get("description") or "")[:200]))
        existing.add(name)
        declared.append(name)
    return tuple(columns), tuple(declared)


def _deps(node: ExprNode) -> Tuple[str, ...]:
    from app.services.data_preparation.field_compute.functions import dependencies
    return dependencies(node)


__all__ = [
    "Token", "tokenize", "parse_expression", "CompileContext",
    "validate_expr", "compile_expression", "compile_columns", "dimension_of",
    "render_expression",
]
