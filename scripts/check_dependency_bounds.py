"""依赖上界守护（#1512 回归防线）。

复刻 #1512 的击穿形态并钉成常驻门禁：pandas 必须 <3.0、sqlalchemy 必须 <2.1、
greenlet 必须可解析、lock 不得与约束漂移。纯文本/数值比较 —— 不跑 pip、不联网，
标准库实现，``--no-cov`` 友好。

用法::

    python scripts/check_dependency_bounds.py [repo_root]

退出码：0 = 干净；1 = 存在击穿/漂移/缺失。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# #1512 两个根因包：已知不安全版本线（首个不安全版本 → 该线实测击穿版本）。
UNSAFE_LINES: dict[str, tuple[str, str]] = {
    "pandas": ("3.0.0", "3.0.6"),
    "sqlalchemy": ("2.1.0", "2.1.1"),
}
# 必须作为直接依赖出现在 requirements 中（lock 有 pin 而 requirements 缺失 → 依赖面失控）。
REQUIRED_DIRECT: tuple[str, ...] = ("geopandas",)


def _cmp(a: str, b: str) -> int:
    """数值化版本比较；缺段补 0（``2.0.54`` vs ``2.1`` → ``2.1`` 视作 ``2.1.0``）。"""
    pa = [int(m) if m else 0 for m in re.findall(r"\d+", a)][:8] or [0]
    pb = [int(m) if m else 0 for m in re.findall(r"\d+", b)][:8] or [0]
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return (pa > pb) - (pa < pb)


def _segment_admits(seg: str, version: str) -> bool | None:
    """单段约束是否放行 version；无法解析的段返回 None（调用方按不放行处理）。"""
    seg = seg.strip()
    if not seg:
        return None
    if seg.startswith("~="):
        ref = seg[2:].strip()
        try:
            parts = [int(x) for x in ref.split(".")]
        except ValueError:
            return None
        if len(parts) < 2:
            return None
        if _cmp(version, ref) < 0:
            return False
        ver_parts = [int(x) for x in re.findall(r"\d+", version)][: len(parts) - 1]
        ver_parts += [0] * (len(parts) - 1 - len(ver_parts))
        return ver_parts == parts[: len(parts) - 1]
    m = re.match(r"^(>=|<=|==|!=|>|<)\s*(.+)$", seg)
    if not m:
        return None
    op, ref = m.group(1), m.group(2).strip()
    try:
        c = _cmp(version, ref)
        rv = [int(x) for x in re.findall(r"\d+", ref)]
        vv = [int(x) for x in re.findall(r"\d+", version)]
    except ValueError:
        return None
    if op == ">=":
        return c >= 0
    if op == "<":
        return c < 0
    if op == "<=":
        return c <= 0
    if op == ">":
        return c > 0
    if op == "==":
        n = max(len(rv), len(vv))
        return (vv + [0] * (n - len(vv))) == (rv + [0] * (n - len(rv)))
    # !=
    n = max(len(rv), len(vv))
    return (vv + [0] * (n - len(vv))) != (rv + [0] * (n - len(rv)))


def admits(spec: str, version: str) -> bool:
    """约束串（逗号分隔，AND 语义）是否放行 version。

    任一段无法解析 → 按不放行处理（宁可误报也不漏报）。
    """
    for seg in spec.split(","):
        r = _segment_admits(seg, version)
        if r is None or r is False:
            return False
    return True


_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")


def _clean_line(line: str) -> str:
    """去注释、去行尾续行符。"""
    if "#" in line:
        line = line.split("#", 1)[0]
    return line.strip()


def parse_requirements(path: Path, _seen: set[Path] | None = None) -> dict[str, str]:
    """解析 requirements 文件 → {name: spec}。支持 -r 递归、extras/markers 剥离、续行拼接。"""
    seen = _seen if _seen is not None else set()
    seen.add(path.resolve())
    specs: dict[str, str] = {}
    logical: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.rstrip()
        if s.endswith("\\"):
            logical.append(s[:-1])
            continue
        logical.append(s)
        line = _clean_line("".join(logical))
        logical = []
        if not line or line.startswith("-") and not line.startswith("-r"):
            continue
        if line.startswith("-r"):
            inc = line[2:].strip()
            inc_path = (path.parent / inc).resolve()
            if inc_path not in seen:
                specs.update(parse_requirements(inc_path, seen))
            continue
        if line.startswith("-"):
            continue
        m = _NAME_RE.match(line)
        if not m:
            continue
        name, _, rest = m.groups()
        spec = rest.split(";", 1)[0].strip()
        if spec:
            specs[name] = spec
    return specs


def parse_lock(path: Path) -> dict[str, str]:
    """解析 pip-compile 风格 lock：列首 ``name==version`` 为 pin，缩进行（# via）跳过。"""
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw[0] in " \t#":
            continue
        line = _clean_line(raw)
        if "==" in line:
            name, _, ver = line.partition("==")
            pins[name.strip()] = ver.strip()
    return pins


def check(req_paths: list[Path], lock_path: Path) -> list[str]:
    """门禁求值 → 错误列表（空 = 干净）。后者文件的同名约束覆盖前者（dev 覆盖 prod）。"""
    specs: dict[str, str] = {}
    for p in req_paths:
        if p.exists():
            specs.update(parse_requirements(p))
    pins = parse_lock(lock_path) if lock_path.exists() else {}
    errors: list[str] = []

    for name, (first, breaker) in UNSAFE_LINES.items():
        spec = specs.get(name)
        if spec is None:
            continue
        if admits(spec, first) or admits(spec, breaker):
            errors.append(
                f"{name}: 约束 '{spec}' 放行击穿版本（首个不安全版本 {first}；"
                f"#1512 实测 {breaker}）— 必须收紧上界"
            )

    for name in REQUIRED_DIRECT:
        if name not in specs:
            errors.append(f"{name} 未在 requirements 声明（lock pin: {pins.get(name, '无')}）— 直接依赖缺失")

    if "greenlet" not in specs and "greenlet" not in pins:
        errors.append(
            "greenlet 未在任何 requirements 声明且 lock 无 pin"
            "（#1512：SQLAlchemy 2.1 将其移入 asyncio extra，干净环境 import 即炸）"
        )

    for name, ver in pins.items():
        spec = specs.get(name)
        if spec is not None and not admits(spec, ver):
            errors.append(f"{name}=={ver} 与约束 '{spec}' 漂移（lock 版本不被约束容忍，lock 陈旧）")

    return errors


def main(root: Path | None = None) -> int:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    reqs = [root / "requirements.txt", root / "requirements-dev.txt"]
    errors = check([p for p in reqs if p.exists()], root / "requirements.lock")
    if errors:
        for e in errors:
            print(f"[dependency-bounds] {e}", file=sys.stderr)
        print(f"[dependency-bounds] FAIL: {len(errors)} 处击穿/漂移/缺失", file=sys.stderr)
        return 1
    print("[dependency-bounds] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
