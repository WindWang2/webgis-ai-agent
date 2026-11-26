"""融合域文本相似度原语（纯函数、零 I/O、可独立 oracle 验证）。

设计约束：
- 不引入新外部依赖（无 rapidfuzz/jellyfish）——归一化 Levenshtein 用
  带早退的两行 DP；blocking 把候选量限制在预算内，O(len²) 只作用于
  候选对而非全表；
- 相似度语义诚实：空对空 = missing（None，由调用方决定跳过语义），
  不折算成 0 或 1；
- CJK 名称场景：字符级相似度为主；空格分词场景（拉丁/地址）提供词元
  Jaccard；两者取 max 并在 evidence 中分项记录，可解释。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional, Sequence


_BRACKET_RE = re.compile(r"[（\(【\[〔][^）\)】\]〕]*[）\)】\]〕]")
_PUNCT_CACHE: dict = {}


def normalize_text(text: str, normalizers: Sequence[str]) -> str:
    """按计划声明顺序应用归一化管线（封闭词表；显式、可入 digest）。"""
    out = text
    for n in normalizers:
        if n == "trim":
            out = out.strip()
        elif n == "casefold":
            out = out.casefold()
        elif n == "collapse_spaces":
            out = " ".join(out.split())
        elif n == "strip_punctuation":
            out = "".join(ch for ch in out if not _is_punct(ch))
        elif n == "remove_bracketed":
            out = _BRACKET_RE.sub("", out)
        else:  # pragma: no cover — 词表校验在 plan 层 fail-closed
            raise ValueError(f"unknown normalizer: {n!r}")
    return out


def _is_punct(ch: str) -> bool:
    cls = _PUNCT_CACHE.get(ch)
    if cls is None:
        cls = unicodedata.category(ch)
        _PUNCT_CACHE[ch] = cls
    return cls.startswith("P")


def _levenshtein_bounded(a: str, b: str, max_dist: int) -> int:
    """两行 DP Levenshtein；超过 max_dist 提前放弃返回 max_dist+1。

    长度差剪枝：len 差 > max_dist 时必然超界。
    """
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return max_dist + 1
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        lo = max(1, i - max_dist)
        hi = min(lb, i + max_dist)
        if lo > 1:
            cur[lo - 1] = max_dist + 1
        best = max_dist + 1
        for j in range(lo, hi + 1):
            cost = 0 if ca == b[j - 1] else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur[j] = v
            if v < best:
                best = v
        for j in range(hi + 1, lb + 1):
            cur[j] = max_dist + 1
        if best > max_dist:
            return max_dist + 1
        prev = cur
    return prev[lb]


#: 相似度求值的字符上限（对抗巨型字段：10 万字符字段的无界 DP 会
#: 挂死执行线程）。超限截断求值 —— 有界求值是标准做法，调用方在
#: evidence 中如实披露 ``truncated_eval``。
MAX_SIM_EVAL_CHARS = 512


def char_similarity(a: str, b: str) -> float:
    """归一化编辑距离相似度 ∈ [0,1]（1 = 全等；空串对非空 = 0）。

    超长输入（> ``MAX_SIM_EVAL_CHARS``）截断求值（对抗巨型字段，
    O(n·band) 保证有界）。
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    a = a[:MAX_SIM_EVAL_CHARS]
    b = b[:MAX_SIM_EVAL_CHARS]
    max_len = max(len(a), len(b))
    # 带宽取可接受差异上限的 2 倍即可判定 <0.5 分支，超出直接给低分
    band = max(2, int(max_len * 0.5))
    d = _levenshtein_bounded(a, b, band)
    if d > band:
        return max(0.0, 1.0 - band / max_len)
    return 1.0 - d / max_len


def token_similarity(a: str, b: str) -> float:
    """空白词元 Jaccard ∈ [0,1]（无词元 = 0；双方各一词元退化为全等判断）。"""
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union


def name_similarity(
    norm_a: str, norm_b: str
) -> dict:
    """名称相似度（分项记录，可解释）。

    返回 ``{"value": float, "char": float, "token": float}``；
    双方归一化后均空 = missing（value=None）。
    """
    if not norm_a and not norm_b:
        return {"value": None, "char": None, "token": None}
    truncated = (len(norm_a) > MAX_SIM_EVAL_CHARS
                 or len(norm_b) > MAX_SIM_EVAL_CHARS)
    c = char_similarity(norm_a, norm_b)
    t = token_similarity(norm_a[:MAX_SIM_EVAL_CHARS * 4],
                         norm_b[:MAX_SIM_EVAL_CHARS * 4])
    v = max(c, t)
    out = {"value": v, "char": c, "token": t}
    if truncated:
        out["truncated_eval"] = True
    return out


def _num(v: object) -> Optional[float]:
    """宽容数值解析（bool 不当数值；非数值返回 None —— 缺失语义）。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def field_values_equal(va: object, vb: object) -> Optional[bool]:
    """输出字段同值判定（None = 至少一侧缺失，不参与判定）。

    规则：字符串 trim+casefold 后比较；数值带 1e-9 相对容差；
    其余类型严格相等。绝不把缺失折算成空串/零。
    """
    if va is None or vb is None or va == "" or vb == "":
        return None
    na, nb = _num(va), _num(vb)
    if na is not None and nb is not None:
        return abs(na - nb) <= max(1e-9, 1e-9 * max(abs(na), abs(nb)))
    sa = str(va).strip().casefold()
    sb = str(vb).strip().casefold()
    return sa == sb


def parse_temporal(v: object) -> Optional[float]:
    """时间字段 → epoch 秒（比较用）。不可解析 = None（诚实缺失）。

    - 数字：秒（<1e11）/ 毫秒（1e11–1e14）/ 微秒（>1e14）三档判别
      （秒级上限 1e11 ≈ 公元 5138 年；毫秒 1e14 ≈ 公元 3366 年 ——
      各档在「现实年份」内无歧义，微秒 1.7e15 不再千倍偏差）；
    - ISO-8601：**完整串解析，显式 offset 优先**（绝不先截断再兜底 ——
      截断会丢掉 "+08:00" 把带时区值当 UTC，newest_temporal 裁决可被
      倒置）；无时区按 UTC（计划声明负责时区语义，本域不做隐式本地化）。
    """
    n = _num(v)
    if n is not None:
        if n > 1e14:
            return n / 1e6
        if n > 1e11:
            return n / 1e3
        return n
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        from datetime import datetime, timezone
        cand = s.replace("Z", "+00:00").replace("z", "+00:00")
        # 完整串优先（offset 在后缀 —— 先截断必丢偏移）
        try:
            dt = datetime.fromisoformat(cand)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
        for cut in (19, 16, 10):
            try:
                dt = datetime.fromisoformat(cand[:cut])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except ValueError:
                continue
    return None
