"""Dataset Semantics Role Binding —— 多数据集 → 任务角色的权威绑定（G01/WP-A）。

ADR-0265 留下的最大歧义：多数据 session 的语义供给是「首个命中」
（``primary_session_descriptor`` 取 list_refs 迭代序首个 ok descriptor），
且整套资格链只有一个 resolver_profile/descriptor 服务全部角色 —— 第二个
数据集的事实永远进不了资格裁决。本模块把「哪个数据集扮演哪个角色」升级为
有界、确定性、可审计的绑定投影：

- ``collect_session_evidence``：session refs（≤ ``MAX_ROLE_BINDING_REFS``，
  **按 ref_id 排序** —— 不依赖 dict 迭代序）→ 每 ref 的语义记录
  （descriptor / 指纹 / 读取状态）。纯读，零数据扫描。
- ``bind_session_roles``：角色需求（复用 ``workflow_schema.DATA_ROLES`` 词表，
  duck-typed ``DataRoleRequirement``）+ 会话证据 → 每角色
  ``RoleBinding``，状态词表 ``ROLE_BINDING_STATUSES``：

  - ``bound``：唯一候选胜出（显式绑定或证据唯一）；
  - ``ambiguous``：多个候选证据并列（如两个同名数据集）—— **输出候选
    清单，绝不静默取首个**；
  - ``not_found``：会话内无该角色的证据候选；
  - ``not_authorized``：显式绑定的 ref 不在当前会话作用域（跨会话/跨项目
    引用或已被 LRU 淘汰）—— fail-closed 拒绝解析，绝不跨作用域取数；
  - ``stale``：绑定成立但记录面期望指纹 ≠ 当前语义指纹（复用
    ``evaluate_reuse`` 的 verdict 与 reason codes，不另造对账）；
  - ``partial``：绑定对象存在但角色所需事实不足（如 boundary 角色缺
    几何证据、损坏 descriptor）—— 诚实披露缺口。

绑定层与资格层（``data_qualification`` 五态）显式分层：绑定层回答
「哪个数据集是这个角色」，资格层回答「这个数据集行不行」—— 状态词表
不混用，映射由消费方（W2 接线）显式完成。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

#: 绑定状态词表（稳定契约；与资格五态分层，见模块 docstring）。
ROLE_BINDING_STATUSES = (
    "bound",
    "not_found",
    "ambiguous",
    "not_authorized",
    "stale",
    "partial",
)

logger = logging.getLogger(__name__)

#: ref 语义读取状态（store 读结果 + 会话作用域判定的投影）。
REF_READ_STATUSES = ("ok", "missing", "out_of_scope", "corrupt", "unsupported")

#: 会话证据读取上限（与 context_bridge.MAX_TRACKED_REFS=16 对齐：durable
#: facts 记录面同界，超出部分的绑定诚实 not_found 而非无界扫描）。
MAX_ROLE_BINDING_REFS = 16

#: ambiguous 候选清单上限（披露给 Agent/用户的澄清载荷有界）。
MAX_BINDING_CANDIDATES = 4

# ── 稳定 reason codes（机读；G01 契约面）────────────────────────────────
CODE_BOUND = "ROLE_BINDING_BOUND"
CODE_BOUND_BY_REF = "ROLE_BINDING_BOUND_BY_REF"
CODE_AMBIGUOUS = "ROLE_BINDING_AMBIGUOUS"
CODE_NOT_FOUND = "ROLE_BINDING_NOT_FOUND"
CODE_OUT_OF_SCOPE = "ROLE_BINDING_REF_OUT_OF_SCOPE"
CODE_STALE = "ROLE_BINDING_STALE"
CODE_FACTS_INCOMPLETE = "ROLE_BINDING_FACTS_INCOMPLETE"
CODE_DESCRIPTOR_UNREADABLE = "ROLE_BINDING_DESCRIPTOR_UNREADABLE"
CODE_DESCRIPTOR_MISSING = "ROLE_BINDING_DESCRIPTOR_MISSING"

# （角色专属字段证据词表复用 workflow_schema 公有词表，见
# _match_role_evidence 内的延迟 import —— 避免包初始化环。）


@dataclass
class RefSemanticRecord:
    """单个 session ref 的语义证据（有界、确定性投影）。"""

    ref_id: str = ""
    alias: str = ""
    read_status: str = "missing"      # ⊆ REF_READ_STATUSES
    reason_code: str = ""
    descriptor: Optional[Any] = None  # GISDatasetDescriptor（read_status=ok 时非 None）
    fingerprint: str = ""             # descriptor_fingerprint（≤96）

    @property
    def ok(self) -> bool:
        return self.read_status == "ok" and self.descriptor is not None

    def to_bounded_dict(self) -> Dict[str, Any]:
        """审计视图（不含 descriptor 本体；指纹/别名有界）。"""
        return {
            "ref_id": str(self.ref_id)[:64],
            "alias": str(self.alias)[:128],
            "read_status": self.read_status,
            "reason_code": str(self.reason_code)[:64],
            "descriptor_fingerprint": str(self.fingerprint)[:96],
        }


@dataclass
class RoleCandidate:
    """ambiguous 澄清清单中的候选摘要（≤ MAX_BINDING_CANDIDATES）。"""

    ref_id: str = ""
    alias: str = ""
    fingerprint: str = ""
    score: int = 0
    matched_on: List[str] = field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "ref_id": str(self.ref_id)[:64],
            "alias": str(self.alias)[:128],
            "descriptor_fingerprint": str(self.fingerprint)[:96],
            "score": int(self.score),
            "matched_on": [str(m)[:64] for m in self.matched_on[:8]],
        }


@dataclass
class RoleBinding:
    """单个角色的绑定裁决（确定性纯函数输出）。"""

    role: str = ""
    status: str = "not_found"         # ⊆ ROLE_BINDING_STATUSES
    ref_id: str = ""
    alias: str = ""
    fingerprint: str = ""
    reason_code: str = CODE_NOT_FOUND
    detail: str = ""
    candidates: List[RoleCandidate] = field(default_factory=list)
    matched_on: List[str] = field(default_factory=list)
    expected_fingerprint: str = ""    # 记录面期望（stale 对账输入，可空）

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "role": str(self.role)[:32],
            "status": self.status,
            "ref_id": str(self.ref_id)[:64],
            "alias": str(self.alias)[:128],
            "descriptor_fingerprint": str(self.fingerprint)[:96],
            "expected_descriptor_fingerprint": str(self.expected_fingerprint)[:96],
            "reason_code": str(self.reason_code)[:64],
            "detail": str(self.detail)[:256],
            "candidates": [c.to_bounded_dict() for c in self.candidates[:MAX_BINDING_CANDIDATES]],
            "matched_on": [str(m)[:64] for m in self.matched_on[:8]],
        }


@dataclass
class RoleBindingReport:
    """一次绑定的全量审计报告（含会话证据快照）。"""

    session_id: str = ""
    refs: List[RefSemanticRecord] = field(default_factory=list)
    bindings: List[RoleBinding] = field(default_factory=list)
    truncated_refs: int = 0           # 超出 MAX_ROLE_BINDING_REFS 被排除的 ref 数

    def binding_by_role(self) -> Dict[str, RoleBinding]:
        return {b.role: b for b in self.bindings}

    def ok_descriptors_by_ref(self) -> Dict[str, Any]:
        return {r.ref_id: r.descriptor for r in self.refs if r.ok}

    def to_bounded_dict(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {s: 0 for s in ROLE_BINDING_STATUSES}
        for b in self.bindings:
            counts[b.status] = counts.get(b.status, 0) + 1
        return {
            "session_id": str(self.session_id)[:64],
            "refs": [r.to_bounded_dict() for r in self.refs],
            "bindings": [b.to_bounded_dict() for b in self.bindings],
            "truncated_refs": int(self.truncated_refs),
            "status_counts": counts,
        }


# ── 会话证据收集（纯读；零数据扫描）────────────────────────────────────

async def collect_session_evidence(
    session_id: str,
    *,
    max_refs: int = MAX_ROLE_BINDING_REFS,
) -> List[RefSemanticRecord]:
    """session refs → 逐 ref 语义记录（按 ref_id 排序，确定性）。

    - ref 清单来自 ``session_data_manager.list_refs``（会话作用域内）；
    - 语义记录来自语义 store ``get(session_id, ref)``：ok / missing /
      corrupt / unsupported（store reason codes 直映）；单 ref 读失败
      跳过并留状态（诚实缺席，不中断其它 ref）；
    - 有界：``max_refs``（默认 16，与 durable facts 记录面同界）。
    """
    if not session_id:
        return []
    from app.services.dataset_semantics import get_dataset_semantic_store
    from app.services.session_data import session_data_manager

    try:
        aliases = await session_data_manager.list_refs(session_id)
    except Exception:  # noqa: BLE001 — ref 清单缺席 = 无证据（诚实空）
        # review P2：基础设施故障必须可见 —— 静默降级会让 Agent 把
        # 「存储不可用」误读成「该会话没有数据」。
        logger.warning(
            "[RoleBinding] list_refs unavailable sid=%s（证据面降级为空）",
            session_id, exc_info=True)
        return []
    if not isinstance(aliases, dict):
        return []
    # 确定性：排序后取前 N（list_refs 的 dict 迭代序跨后端不保证稳定）。
    ref_ids = sorted(str(r) for r in aliases.keys())[:max(0, int(max_refs))]

    store = get_dataset_semantic_store()
    records: List[RefSemanticRecord] = []
    for ref in ref_ids:
        rec = RefSemanticRecord(
            ref_id=ref,
            alias=str(aliases.get(ref, "") or "")[:128],
        )
        try:
            stored = await store.get(session_id, ref)
        except Exception:  # noqa: BLE001 — 单 ref 读失败按 corrupt（可见不吞）
            rec.read_status = "corrupt"
            rec.reason_code = "DESCRIPTOR_STORE_READ_FAILED"
            records.append(rec)
            continue
        status = str(getattr(stored, "status", "") or "")
        if getattr(stored, "ok", False) and stored.descriptor is not None:
            rec.read_status = "ok"
            rec.descriptor = stored.descriptor
            rec.fingerprint = str(
                getattr(stored, "fingerprint", "")
                or getattr(stored.descriptor, "descriptor_fingerprint", "")
                or "",
            )[:96]
        elif "unsupported" in (str(getattr(stored, "reason_code", "") or "").lower()):
            rec.read_status = "unsupported"
            rec.reason_code = str(getattr(stored, "reason_code", "") or "DESCRIPTOR_VERSION_UNSUPPORTED")[:64]
        elif status and status != "missing":
            # store 显式失败（corrupt/too_large/…）：保留其 reason code。
            rec.read_status = "corrupt"
            rec.reason_code = str(getattr(stored, "reason_code", "") or "DESCRIPTOR_STORE_CORRUPT")[:64]
        else:
            rec.read_status = "missing"
            rec.reason_code = "DESCRIPTOR_MISSING"
        records.append(rec)
    return records


# ── 证据匹配（确定性评分；词表复用不另造）───────────────────────────────

def _geometry_category(descriptor: Any) -> str:
    """descriptor → 主导几何类别（单一事实源：data_qualification.geometry_category；
    栅格由 kind=raster 直接判定 —— 矢量归约零复制，延迟 import 避免环）。"""
    kind = str(getattr(descriptor, "kind", "") or "")
    if kind == "raster":
        return "raster"
    from app.services.gis_harness.data_qualification import (
        geometry_category,
    )

    return geometry_category(list(getattr(descriptor, "geometry_types", None) or []))


def _field_names(descriptor: Any) -> List[str]:
    return [str(f.name) for f in (getattr(descriptor, "fields", None) or [])]


def _match_role_evidence(
    req: Any,
    rec: RefSemanticRecord,
    *,
    name_hint: str = "",
) -> tuple[int, List[str], List[str]]:
    """角色需求 × ref 语义记录 → (得分, 命中证据, 缺失证据)。

    评分维度（全部确定性布尔证据，无概率）：
    - 显式名称提示命中 alias/dataset_key → +3（用户/计划点名）；
    - accepted_artifact_types 命中 descriptor.artifact_type → +2；
    - geometry_kinds 命中主导几何类别 → +2；
    - 角色专属字段证据（denominator 分母字段 / 时间角色时间字段）→ +2；
    几何需求声明而类别 unknown → 不计分且记入缺失（partial 判据）。
    """
    descriptor = rec.descriptor
    score = 0
    matched: List[str] = []
    missing: List[str] = []

    name_hint = str(name_hint or "").strip().lower()
    if name_hint:
        name_pool = [str(rec.alias or "").lower(),
                     str(getattr(descriptor, "dataset_key", "") or "").lower()]
        if any(name_hint in name for name in name_pool if name):
            score += 3
            matched.append("name_hint")

    artifact_types = [str(a) for a in (getattr(req, "accepted_artifact_types", None) or [])]
    artifact_type = str(getattr(descriptor, "artifact_type", "") or "")
    if artifact_types and artifact_type:
        if artifact_type in artifact_types:
            score += 2
            matched.append("artifact_type")

    geom_kinds = [str(g) for g in (getattr(req, "geometry_kinds", None) or [])]
    geom_cat = _geometry_category(descriptor)
    if geom_kinds:
        if geom_cat != "unknown":
            if geom_cat in geom_kinds:
                score += 2
                matched.append("geometry_kind")
            else:
                score -= 1  # 有证据但不符：负向，不被首选
        else:
            missing.append("geometry_kind")

    role = str(getattr(req, "role", "") or "")
    fields = _field_names(descriptor)
    lowered = [f.lower() for f in fields]
    if role == "denominator":
        from app.services.gis_harness.workflow_schema import DENOMINATOR_FIELD_HINTS

        if any(h in name for name in lowered for h in DENOMINATOR_FIELD_HINTS):
            score += 2
            matched.append("denominator_field")
        elif fields:
            missing.append("denominator_field")
    if role in ("baseline", "target_time", "comparison_time"):
        temporal = getattr(descriptor, "temporal", None)
        has_time = getattr(temporal, "has_time_field", None)
        if has_time is True:
            score += 2
            matched.append("time_field")
        else:
            missing.append("time_field")

    return score, matched, missing


def _ref_in_scope(bound_ref: str, records: Sequence[RefSemanticRecord]) -> Optional[RefSemanticRecord]:
    for r in records:
        if r.ref_id == bound_ref:
            return r
    return None


# ── 绑定主入口（确定性纯函数）───────────────────────────────────────────

def bind_session_roles(
    role_reqs: Sequence[Any],
    ref_records: Sequence[RefSemanticRecord],
    *,
    session_id: str = "",
    bound_refs: Optional[Dict[str, str]] = None,
    expected_fingerprints: Optional[Dict[str, str]] = None,
    name_hints: Optional[Dict[str, str]] = None,
    truncated_refs: int = 0,
) -> RoleBindingReport:
    """角色需求 × 会话语义证据 → 每角色绑定裁决。

    输入优先级（显式 > 证据推导，均确定性）：
    1. ``bound_refs[role]``（planner 真实计划行绑定）—— ref 必须在
       ``ref_records`` 作用域内，否则 ``not_authorized``（跨会话/淘汰
       fail-closed）；绑定后按 ``expected_fingerprints[ref]`` 对账 stale；
    2. 证据匹配 —— 全部 ok 记录按 (得分, ref_id) 排序；最高分并列 ≥2 →
       ``ambiguous``（候选清单 ≤4）；唯一 >0 → bound；
       唯一候选但缺角色所需关键事实 → ``partial``；
    3. 无 ok 记录 → ``not_found``。

    ``expected_fingerprints``：记录面（MapSpec source / durable facts /
    plan 当时引用的指纹），键为 ref_id；绑定命中（显式或证据推导）且
    该 ref 存在记录面锚时做 stale 对账；无锚不虚构期望。
    """
    bound_refs = dict(bound_refs or {})
    expected_fingerprints = dict(expected_fingerprints or {})
    name_hints = dict(name_hints or {})
    # 审计面确定性：refs 快照按 ref_id 排序（与 collect_session_evidence
    # 同序），报告不随调用方输入顺序漂移。
    ordered_records = sorted(ref_records, key=lambda r: str(r.ref_id))
    report = RoleBindingReport(
        session_id=str(session_id)[:64],
        refs=ordered_records,
        truncated_refs=int(truncated_refs),
    )
    ok_records = [r for r in ordered_records if r.ok]

    for req in role_reqs:
        role = str(getattr(req, "role", "") or "")[:32]
        binding = RoleBinding(role=role)

        explicit_ref = str(bound_refs.get(role, "") or "")
        if explicit_ref:
            rec = _ref_in_scope(explicit_ref, ref_records)
            if rec is None:
                binding.status = "not_authorized"
                binding.reason_code = CODE_OUT_OF_SCOPE
                binding.ref_id = explicit_ref[:64]
                binding.expected_fingerprint = str(
                    expected_fingerprints.get(explicit_ref, ""))[:96]
                binding.detail = (
                    "绑定的 ref 不在当前会话作用域（跨会话/跨项目引用或已被"
                    "淘汰）：拒绝跨作用域解析，不读取其语义或数据。"
                )
                report.bindings.append(binding)
                continue
            if not rec.ok:
                # ref 在作用域内但语义不可读（missing/corrupt/unsupported）。
                binding.ref_id = rec.ref_id
                binding.alias = rec.alias
                binding.expected_fingerprint = str(
                    expected_fingerprints.get(rec.ref_id, ""))[:96]
                if rec.read_status == "missing":
                    binding.status = "partial"
                    binding.reason_code = CODE_DESCRIPTOR_MISSING
                    binding.detail = "ref 在会话内但语义 descriptor 缺席（可经既有零扫描补铸路径恢复）。"
                else:
                    binding.status = "partial"
                    binding.reason_code = CODE_DESCRIPTOR_UNREADABLE
                    binding.detail = f"语义 descriptor 不可读（{rec.reason_code}）：按缺口披露，不虚构事实。"
                report.bindings.append(binding)
                continue

            binding.status = "bound"
            binding.ref_id = rec.ref_id
            binding.alias = rec.alias
            binding.fingerprint = rec.fingerprint
            binding.reason_code = CODE_BOUND_BY_REF
            binding.matched_on = ["explicit_binding"]
            expected = str(expected_fingerprints.get(rec.ref_id, "") or "")[:96]
            binding.expected_fingerprint = expected
            if expected:
                verdict = _reuse_verdict(expected, rec)
                if verdict not in ("valid",):
                    binding.status = "stale"
                    binding.reason_code = CODE_STALE
                    binding.detail = (
                        f"记录面指纹与当前语义不符（reuse={verdict}）："
                        "相关结论需按新语义重评，不静默续用。"
                    )
                    report.bindings.append(binding)
                    continue
                binding.detail = "记录面指纹与当前语义一致（freshness 确认）。"
            report.bindings.append(binding)
            continue

        # ── 证据推导路径（无显式绑定）─────────────────────────────────
        scored: List[tuple[int, str, RefSemanticRecord, List[str], List[str]]] = []
        for rec in ok_records:
            score, matched, missing = _match_role_evidence(
                req, rec, name_hint=name_hints.get(role, ""))
            scored.append((score, rec.ref_id, rec, matched, missing))
        if not scored:
            binding.status = "not_found"
            binding.reason_code = CODE_NOT_FOUND
            binding.detail = "会话内无带语义证据的数据集。"
            report.bindings.append(binding)
            continue

        scored.sort(key=lambda t: (-t[0], t[1]))  # (得分降序, ref_id 升序)
        top_score = scored[0][0]
        if top_score <= 0:
            binding.status = "not_found"
            binding.reason_code = CODE_NOT_FOUND
            binding.detail = "无任一数据集具备该角色的正向证据（不猜测绑定）。"
            report.bindings.append(binding)
            continue
        tied = [t for t in scored if t[0] == top_score]
        if len(tied) >= 2:
            binding.status = "ambiguous"
            binding.reason_code = CODE_AMBIGUOUS
            binding.detail = (
                f"{len(tied)} 个数据集证据并列（得分 {top_score}）："
                "需显式澄清，绝不自动取首个。"
            )
            binding.candidates = [
                RoleCandidate(
                    ref_id=t[2].ref_id, alias=t[2].alias,
                    fingerprint=t[2].fingerprint, score=t[0], matched_on=t[3],
                )
                for t in tied[:MAX_BINDING_CANDIDATES]
            ]
            report.bindings.append(binding)
            continue

        score, _, rec, matched, missing = scored[0]
        binding.ref_id = rec.ref_id
        binding.alias = rec.alias
        binding.fingerprint = rec.fingerprint
        binding.matched_on = matched
        # review P2：证据路径绑定成功后，若记录面对该 ref 存在指纹锚，
        # 同样做 stale 对账（不只显式绑定路径）—— 生产入口不传
        # bound_refs 时 stale 判据必须可达。
        expected_ev = str(expected_fingerprints.get(rec.ref_id, "") or "")[:96]
        binding.expected_fingerprint = expected_ev
        if expected_ev:
            verdict = _reuse_verdict(expected_ev, rec)
            if verdict not in ("valid",):
                binding.status = "stale"
                binding.reason_code = CODE_STALE
                binding.detail = (
                    f"记录面指纹与当前语义不符（reuse={verdict}）："
                    "相关结论需按新语义重评，不静默续用。"
                )
                report.bindings.append(binding)
                continue
            binding.matched_on = matched + ["freshness_valid"]
        if missing:
            binding.status = "partial"
            binding.reason_code = CODE_FACTS_INCOMPLETE
            binding.detail = (
                "证据唯一但角色所需关键事实缺席（" + "、".join(missing[:4]) +
                "）：绑定成立，资格裁决按缺口降级。"
            )
        else:
            binding.status = "bound"
            binding.reason_code = CODE_BOUND
            binding.detail = (
                f"唯一证据候选（得分 {score}）"
                + ("，记录面指纹一致。" if expected_ev else "。")
            )
        report.bindings.append(binding)

    return report


def _reuse_verdict(expected_fingerprint: str, rec: RefSemanticRecord) -> str:
    """记录面指纹 × 当前记录 → reuse verdict（复用 reuse.evaluate_reuse）。

    RefSemanticRecord → DescriptorRecord 形状适配（ok 记录语义等价于
    store 的 STATUS_OK 读结果），对账逻辑单一来源。
    """
    from app.services.dataset_semantics.reuse import evaluate_reuse
    from app.services.dataset_semantics.store import DescriptorRecord, STATUS_OK

    try:
        current = DescriptorRecord(
            status=STATUS_OK, descriptor=rec.descriptor,
            fingerprint=rec.fingerprint,
            dataset_key=str(getattr(rec.descriptor, "dataset_key", "") or ""),
        )
        decision = evaluate_reuse(expected_fingerprint, current)
        return str(decision.verdict)
    except Exception:  # noqa: BLE001 — 对账不可执行按 unknown（fail-closed 侧）
        return "unknown"


# ── 资格消费投影（W2 接线面）────────────────────────────────────────────

def role_qualification_evidence(
    report: RoleBindingReport,
) -> Dict[str, Dict[str, Any]]:
    """绑定报告 → 每角色资格参数形状（``{descriptor, expected_descriptor_fingerprint}``）。

    - bound/partial → 供 descriptor（partial 的事实缺口由资格层按
      PROFILE_FACTS_UNAVAILABLE/缺检查诚实降级）；
    - stale → 供 descriptor + expected 指纹（资格层 freshness guard 产出
      ``DESCRIPTOR_STALE_*`` 精确降级）；
    - ambiguous/not_found/not_authorized → 不供（无事实即无资格输入，
      绝不拿别的数据集顶替）。
    """
    from app.services.dataset_semantics.qualification_bridge import descriptor_evidence

    out: Dict[str, Dict[str, Any]] = {}
    for b in report.bindings:
        if b.status not in ("bound", "partial", "stale"):
            continue
        descriptor = report.ok_descriptors_by_ref().get(b.ref_id)
        if descriptor is None:
            continue
        expected = b.expected_fingerprint if b.status == "stale" else ""
        out[b.role] = descriptor_evidence(descriptor, expected)
    return out


def primary_binding(report: RoleBindingReport, *, prefer_roles: Sequence[str] = ("subject", "measure")) -> Optional[RoleBinding]:
    """报告 → 主绑定（subject/measure 优先；否则首个 bound；无则 None）。

    替代「首个命中」的确定性主数据集选择：只在**已成功绑定**的角色里选，
    歧义/缺位不会伪装成主数据集。
    """
    by_role = report.binding_by_role()
    for role in prefer_roles:
        b = by_role.get(role)
        if b is not None and b.status in ("bound", "partial"):
            return b
    for b in report.bindings:
        if b.status in ("bound", "partial"):
            return b
    return None


__all__ = [
    "ROLE_BINDING_STATUSES",
    "REF_READ_STATUSES",
    "MAX_ROLE_BINDING_REFS",
    "MAX_BINDING_CANDIDATES",
    "RefSemanticRecord",
    "RoleCandidate",
    "RoleBinding",
    "RoleBindingReport",
    "collect_session_evidence",
    "bind_session_roles",
    "role_qualification_evidence",
    "primary_binding",
]
