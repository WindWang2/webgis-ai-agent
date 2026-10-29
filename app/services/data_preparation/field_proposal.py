"""字段角色候选提案引擎（M3；M2 契约 §2）。

回答的问句：「这份数据每个字段最可能是什么角色？证据是什么？还有哪些
竞争候选被拒、为什么？」

输入**已就位的画像证据**（FieldProfile —— app/lib/data/profile.py 产出），
零 IO、纯函数、确定性：同输入恒同提案。绝不自行扫描数据本体（与
quality.py 消费 profile 的纪律一致）。

与 derive_semantic_profile（app/lib/gis/semantic_profile.py）的分工：
- derive_semantic_profile 产出**判定结果**（roles + 分级置信），供语义闸
  消费；单一角色集合，无候选/被拒概念；
- 本引擎产出**候选提案**（有序候选 + 数值置信度 + 原因码 + 被拒候选 +
  needs_clarification），供用户复核 UI 与 requires_input 工具态消费；
  细粒度坐标角色（lon/lat/x/y 分立）与 address 仅存在于提案层。

置信度口径（vocabulary.py 文档节；确定性算术，不做概率装饰）：
    用户提示                       → 1.0（user_declared，不可被降权）
    整词命中 + dtype 一致 + 值域一致 → ≥ 0.90
    整词命中 + 单一佐证             → 0.75–0.85
    子串命中（弱）                  → 0.55–0.70
    仅值域证据（无名称证据）         → ≤ 0.70（坐标角色专用）
冲突处理：值域反证（如 lat 候选遇 |值|>90）→ 该候选被拒（RANGE_CONFLICT），
不参与排序 —— **绝不把名称猜测凌驾于值域事实之上**。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, field_validator

from app.lib.data.profile import FieldProfile
from app.services.data_preparation.vocabulary import (
    ADMIN_NAME_TERMS,
    NUMERIC_PROPOSAL_ROLES,
    PROPOSAL_ROLES,
    ROLE_SYNONYMS,
    ReasonCode,
)

#: 提案阈值（低于此置信度的候选不进入 candidates；vocabulary 文档口径）。
MIN_CANDIDATE_CONFIDENCE = 0.5
#: 自动采纳阈值（≥ 此值且无旗标冲突时可自动执行，UI 可折叠展示）。
AUTO_ADOPT_CONFIDENCE = 0.85
#: 竞争判定带（top1 与 top2 差距 < 此值且双方 ≥ 提案阈值 → 澄清）。
AMBIGUITY_GAP = 0.2

#: 有界上限（与既有契约同量级）。
_MAX_CANDIDATES = 4
_MAX_REASONS = 6
_MAX_REJECTED = 4
_MAX_FIELDS_PROPOSED = 64

_NUMERIC_DTYPES = {"number", "integer", "float", "int", "double",
                   "float64", "float32", "int64", "int32", "int16", "int8"}
_STRINGISH_DTYPES = {"string", "boolean", "mixed", "unknown", ""}

_LAT_ABS_MAX = 90.0
_LON_ABS_MAX = 180.0
#: 投影量级阈值：|值| > 180 视为投影坐标候选（CGCS2000/UTM 米制量级）。
_PROJECTED_ABS_MIN = 180.0


class RoleCandidate(BaseModel):
    """单角色候选（confidence + 原因码）。"""

    role: str
    confidence: float
    reasons: Tuple[str, ...] = ()

    @field_validator("role")
    @classmethod
    def _role_in_vocab(cls, v: str) -> str:
        if v not in PROPOSAL_ROLES:
            raise ValueError(f"unregistered proposal role: {v!r}")
        return v

    @field_validator("confidence")
    @classmethod
    def _conf(cls, v: float) -> float:
        return round(min(max(float(v), 0.0), 1.0), 4)

    @field_validator("reasons")
    @classmethod
    def _reasons(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(str(r)[:48] for r in v[:_MAX_REASONS])


class FieldProposal(BaseModel):
    """单字段提案（候选按 confidence 降序；rejected = 字段级被拒候选）。

    无候选（全部低于阈值或被值域反证拒绝）时 candidates 为空、
    needs_clarification 为 True —— 拒绝理由在 rejected 里，供 UI 展示。
    """

    field: str
    dtype: str = ""
    candidates: Tuple[RoleCandidate, ...] = ()
    rejected: Tuple[Tuple[str, str], ...] = ()
    needs_clarification: bool = False

    @field_validator("field")
    @classmethod
    def _field(cls, v: str) -> str:
        return str(v)[:128]

    @field_validator("rejected")
    @classmethod
    def _rejected(cls, v: Tuple[Tuple[str, str], ...]) -> Tuple[Tuple[str, str], ...]:
        return tuple((str(r)[:32], str(c)[:48]) for r, c in v[:_MAX_REJECTED])

    @property
    def top(self) -> Optional[RoleCandidate]:
        return self.candidates[0] if self.candidates else None


def _norm(name: str) -> str:
    """归一化：去空白/下划线/连字符、小写 —— 「School_Name」「school name」
    「school-name」同词；中文原样（无大小写问题）。"""
    return re.sub(r"[\s_\-]+", "", str(name or "")).lower()


def _name_hit(field_norm: str, role: str) -> Tuple[Optional[ReasonCode], int]:
    """词表匹配 → (原因码, 命中词长)。三档：

    - 整词：归一化字段名 == 同义词（「经度」「name」）；
    - 词尾：字段名以同义词结尾 —— 合成名词的**中心语**在尾部（中英通用：
      「学校名称」= 学校的名称、「school_name」同理、「学校编号」→ id 而非
      name）。词尾命中按近整词计分，解决「学校编号 同时含 学校(名称词)/编号
      (id 词)」的等权并列；
    - 子串：弱命中（前缀/中缀修饰语）。
    命中词长用于特异性排序（长词优先）。"""
    best: Optional[Tuple[ReasonCode, int]] = None
    _rank = {ReasonCode.SYNONYM_EN: 0, ReasonCode.SYNONYM_ZH: 0,
             ReasonCode.NAME_SUFFIX: 1, ReasonCode.NAME_EXACT: 2}
    for lang in ("zh", "en"):
        for term in ROLE_SYNONYMS[role][lang]:
            t = _norm(term)
            if not t:
                continue
            if field_norm == t:
                code = ReasonCode.NAME_EXACT
            elif field_norm.endswith(t):
                code = ReasonCode.NAME_SUFFIX
            elif t in field_norm:
                code = ReasonCode.SYNONYM_ZH if lang == "zh" else ReasonCode.SYNONYM_EN
            else:
                continue
            cur = (code, len(t))
            if best is None or _rank[cur[0]] > _rank[best[0]] or (
                _rank[cur[0]] == _rank[best[0]] and cur[1] > best[1]
            ):
                best = cur
    return (best[0], best[1]) if best else (None, 0)


def _admin_name_hit(field_norm: str) -> bool:
    return any(_norm(t) in field_norm for t in ADMIN_NAME_TERMS)


def _range_of(fp: FieldProfile) -> Optional[Tuple[float, float]]:
    if fp.min is None or fp.max is None:
        return None
    lo, hi = float(fp.min), float(fp.max)
    if hi < lo:  # 防御：画像脏数据不构成值域证据
        lo, hi = hi, lo
    return (lo, hi)


def _range_in(rng: Tuple[float, float], lo: float, hi: float) -> bool:
    return rng[0] >= lo and rng[1] <= hi


def _propose_one(
    fp: FieldProfile,
    *,
    user_hints: Mapping[str, str],
    row_count: int = 0,
) -> FieldProposal:
    """单字段 → 提案（全部判定确定性、可解释）。"""
    fname = _norm(fp.name)
    dtype = str(fp.dtype or "unknown")
    rng = _range_of(fp)
    is_numeric = dtype in _NUMERIC_DTYPES
    is_stringish = dtype in _STRINGISH_DTYPES

    scores: Dict[str, float] = {}
    reasons: Dict[str, List[ReasonCode]] = {}
    rejected: List[Tuple[str, str]] = []

    def _add(role: str, conf: float, *why: ReasonCode) -> None:
        # 用户提示（1.0）不可被佐证改写也不受 0.98 封顶 —— 满置信豁免。
        if scores.get(role, 0.0) >= 1.0:
            return
        capped = conf if conf >= 1.0 else min(conf, 0.98)
        scores[role] = max(scores.get(role, 0.0), capped)
        bucket = reasons.setdefault(role, [])
        for w in why:
            if w not in bucket:
                bucket.append(w)

    # 0) 用户/Agent 提示：最高优先且不降权（但仍经值域反证 —— 提示错了
    #    要能被看见，被拒时进 rejected 而非静默采纳）。
    hint = str(user_hints.get(fp.name) or "").strip().lower()
    if hint in PROPOSAL_ROLES:
        if hint in NUMERIC_PROPOSAL_ROLES and not is_numeric:
            rejected.append((hint, ReasonCode.RANGE_CONFLICT.value))
        else:
            _add(hint, 1.0, ReasonCode.USER_HINT)

    # 1) 名称证据（特异性序：admin_code / 坐标对 先于泛角色）。
    #    admin_code：整词/长词优先 —— 防「学校代码」误入（代码已不入 id 词表，
    #    行政区划代码只归 admin_code）；行政区名称列同样归 admin_code。
    #    词尾命中（NAME_SUFFIX）按近整词计分 ——「学校编号」的 id 证据（词尾
    #    「编号」=中心语）必须压过「学校」（name 词、修饰语）的子串证据。
    ordered_roles = ("admin_code", "lon", "lat", "x", "y", "address", "denominator",
                     "weight", "time", "name", "category", "value", "id")
    for role in ordered_roles:
        code, term_len = _name_hit(fname, role)
        if code is None:
            continue
        # dtype 闸（与 semantic_profile._dtype_allows 同纪律）。
        if role in NUMERIC_PROPOSAL_ROLES and not is_numeric:
            rejected.append((role, ReasonCode.RANGE_CONFLICT.value))
            continue
        if code == ReasonCode.NAME_EXACT:
            base = 0.80
        elif code == ReasonCode.NAME_SUFFIX:
            base = 0.76
        else:
            base = 0.62
        conf = base + min(term_len, 10) * 0.008  # 长词（更特异）微加成
        _add(role, conf, code)

    # 行政区名称列（「所属区县」「district_name」）无 code 词命中时补 admin_code。
    if "admin_code" not in scores and _admin_name_hit(fname) and is_stringish:
        _add("admin_code", 0.68, ReasonCode.SYNONYM_ZH)

    # 2) 值域/统计佐证（只对已有名称证据或坐标/键候选加成；纯值域也可以
    #    独立提出坐标候选 —— CSV 无语义列名时的兜底）。
    if is_numeric and rng is not None:
        lat_ok = _range_in(rng, -_LAT_ABS_MAX, _LAT_ABS_MAX)
        lon_ok = _range_in(rng, -_LON_ABS_MAX, _LON_ABS_MAX)
        projected = abs(rng[0]) > _PROJECTED_ABS_MIN or abs(rng[1]) > _PROJECTED_ABS_MIN
        # lat：名称证据 + 值域一致 → high；名称 lat 但值域反证 → 拒。
        if "lat" in scores:
            if lat_ok:
                _add("lat", scores["lat"] + 0.15, ReasonCode.VALUE_RANGE_LAT)
            else:
                rejected.append(("lat", ReasonCode.RANGE_CONFLICT.value))
                scores.pop("lat", None)
                reasons.pop("lat", None)
        elif lat_ok and not lon_ok and "x" not in scores and "y" not in scores:
            # 无名称证据、值域只容 lat（不含完整 lon 带）→ 弱候选。
            _add("lat", 0.58, ReasonCode.VALUE_RANGE_LAT)
        if "lon" in scores:
            if lon_ok:
                _add("lon", scores["lon"] + 0.15, ReasonCode.VALUE_RANGE_LON)
            else:
                rejected.append(("lon", ReasonCode.RANGE_CONFLICT.value))
                scores.pop("lon", None)
                reasons.pop("lon", None)
        elif lon_ok and "x" not in scores and "y" not in scores and rng != (0.0, 0.0):
            _add("lon", 0.56, ReasonCode.VALUE_RANGE_LON)
        # x/y：投影量级佐证（名称 x/y + 值域呈投影量级 → 加成）。
        for axis in ("x", "y"):
            if axis in scores and projected:
                _add(axis, scores[axis] + 0.12, ReasonCode.RANGE_PLANE_PROJECTED)

    # id：唯一性佐证（字符串主键同样适用 —— 不在数值/值域块内）。
    # 有行数口径时要求 unique≈row；封顶集合视为不足证据。
    if "id" in scores and fp.unique_count is not None and not fp.unique_capped:
        if row_count > 1:
            if fp.unique_count >= row_count:
                _add("id", scores["id"] + 0.15, ReasonCode.ID_UNIQUENESS)
        elif fp.unique_count >= max(fp.null_count + 1, 1) and fp.unique_count > 1:
            _add("id", scores["id"] + 0.15, ReasonCode.ID_UNIQUENESS)

    # name：标签列基数佐证（名称近唯一 + 字符串 dtype —— 名称列每要素一个
    # 标签，类别列低基数；无行数口径时不佐证）。
    if ("name" in scores and is_stringish and fp.unique_count is not None
            and not fp.unique_capped and row_count >= 8
            and fp.unique_count >= 0.9 * row_count):
        _add("name", scores["name"] + 0.08, ReasonCode.NAME_CARDINALITY)

    # dtype 佐证：数值角色 + number dtype。
    for role in list(scores):
        if role in NUMERIC_PROPOSAL_ROLES and is_numeric:
            _add(role, scores[role] + 0.08, ReasonCode.DTYPE_CONSISTENT)

    # time：画像 temporal_hint / 日期样本佐证（hint 无名称证据也立候选）。
    if "time" in scores or fp.temporal_hint:
        if fp.temporal_hint:
            _add("time", max(scores.get("time", 0.0), 0.6) + 0.2,
                 ReasonCode.TIME_PARSED)

    # category：低基数字符串佐证（distinct/样本比值 ≤ 0.5 —— 与
    # semantic_profile._CATEGORY_CARDINALITY_RATIO 同口径）。
    if fp.dtype in ("string", "boolean") and fp.unique_count is not None:
        base_n = max(1, (fp.unique_count or 1))
        if not fp.unique_capped and base_n <= 50:
            _add("category", max(scores.get("category", 0.0), 0.55),
                 ReasonCode.CATEGORY_CARDINALITY)

    # value：数值列泛值兜底（名称未命中任何角色时的诚实弱候选）。
    # 投影量级判定只对坐标角色有意义 —— 计数列 max>180 不是投影坐标。
    if is_numeric and not scores and rng is not None:
        _add("value", 0.52, ReasonCode.NUMERIC_MEASURE)

    # 3) 排序、阈值过滤、竞争澄清。
    ranked: List[Tuple[float, str]] = sorted(
        ((c, r) for r, c in scores.items() if c >= MIN_CANDIDATE_CONFIDENCE),
        key=lambda kv: (-kv[0], kv[1]),
    )
    # 同分带竞争（差 < AMBIGUITY_GAP 且都过阈值）→ 保留候选但标澄清；
    # 置信度并列第一的两个角色不互相拒绝（真实数据常有多角色字段，
    # 如「区县代码」同是 admin_code 与 id 候选 —— 用户裁决）。
    candidates = tuple(
        RoleCandidate(role=r, confidence=c,
                      reasons=tuple(x.value for x in reasons.get(r, [])))
        for c, r in ranked[:_MAX_CANDIDATES]
    )
    needs_clarification = False
    if not candidates:
        # 无候选（含全部被值域反证拒绝）= 必须由用户指派 —— 提案层不许猜。
        needs_clarification = True
    else:
        top = candidates[0].confidence
        if top < AUTO_ADOPT_CONFIDENCE:
            needs_clarification = True
        elif (len(candidates) > 1
              and top - candidates[1].confidence < AMBIGUITY_GAP):
            needs_clarification = True
    return FieldProposal(
        field=fp.name,
        dtype=dtype,
        candidates=candidates,
        rejected=tuple(rejected),
        needs_clarification=needs_clarification,
    )


def name_role_hit(field: str, role: str) -> Optional[ReasonCode]:
    """字段名 → 角色词表命中码（名称身份判定；与置信度无关）。

    供收据层做坐标列**身份**识别：xy 对调场景中名义 lat 列被值域反证
    拒绝后不再出现在候选里，但对调检测仍需要「这列名义上是 lat」的
    名称事实 —— 名称身份与值域证据分立，正是对调检测的输入。
    """
    if role not in PROPOSAL_ROLES:
        return None
    code, _ = _name_hit(_norm(field), role)
    return code


def propose_field_roles(
    fields: Mapping[str, FieldProfile],
    *,
    user_hints: Mapping[str, str] | None = None,
    row_count: int = 0,
) -> List[FieldProposal]:
    """字段画像集 → 提案列表（确定性；≤64 字段，超长诚实截断）。

    ``row_count``：数据集行数口径（唯一性佐证的基准；0 = 无口径，不佐证）。
    """
    hints = dict(user_hints or {})
    out: List[FieldProposal] = []
    for name, fp in list(fields.items())[:_MAX_FIELDS_PROPOSED]:
        if not isinstance(fp, FieldProfile):
            continue
        out.append(_propose_one(fp, user_hints=hints, row_count=row_count))
    return out


def proposals_summary(proposals: Sequence[FieldProposal]) -> Dict[str, Any]:
    """提案 → 有界摘要（工具/LLM 面；绝不携带样本值）。"""
    mapped: Dict[str, str] = {}
    clarify: List[str] = []
    for p in proposals:
        if p.top is not None:
            mapped[p.field] = p.top.role
            if p.needs_clarification:
                clarify.append(p.field)
        else:
            clarify.append(p.field)
    return {
        "mapped": mapped,
        "needs_clarification": sorted(clarify)[:32],
        "field_count": len(proposals),
    }
