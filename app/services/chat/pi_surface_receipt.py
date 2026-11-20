"""Turn-scoped surface qualification + SurfaceReceipt（Goal 04, ADR-0224）。

master 现状：per-turn 工具面选择（``compute_turn_active_tools``）只消费检索
分数 + tier/可见性 + 字节预算，**从不消费 RuntimeSituation** —— 凭证缺席、
offline、durable worker 不可用等事实要到 dispatch 期 capability bind 才裁决，
模型会先看到「可调」再吃 INELIGIBLE。本模块补 turn 侧资格投影：

- 输入：V3 选择器产物（candidates）+ RuntimeSituation 白名单投影（复用
  ``to_qualification_dict()``，不建第二消费契约）+ PiToolSpecV1（安全/资源
  先验）；
- 输出：qualified surface + **SurfaceReceipt**（candidates/selected/dropped/
  reason codes/bytes/fingerprint 的确定性记录，per (session_id, turn_id)
  存储，供回调 fingerprint echo 校验与披露面消费）。

断言纪律（与 runtime_situation 一致）：无来源的面 = unknown = **不排除**；
只有阳性缺席事实（credentials_present[id] == False、offline is True、
durable_worker is False）才排除。dispatch 期闸（capability bind / tier /
input gate）仍是唯一执行权威 —— 本层是投影，不是闸的替代。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from app.services.chat.pi_tool_spec import (
    PiToolSpecV1,
    surface_fingerprint,
)

logger = logging.getLogger(__name__)

#: 资格策略版本（receipt 披露面；规则变更时 +1 并同步 ADR-0224）。
SURFACE_QUALIFICATION_POLICY = "pi_surface_qualification.v1"

#: receipt 存储上限（与 bridge 的 dispatch cache 同量级；turn 结束清理 + 容量逐出）。
_RECEIPT_STORE_MAX = 128

#: bare-name 面成员硬校验（ADR-0288 起默认 ON）：native bare-name 回调必须
#: 落在 turn 的 selected ∪ discovered 集内，否则 typed 拒绝（proxy 通道不受限）。
#: 发现（tool_search）有合法 grant 路径后，旧行为的误拒场景（setActiveTools
#: 退化全超集激活）不再成立 —— 注册超集内的合法发现调用会被 grant 接住。
#: kill-switch：显式 0/false/off/no 回到 G12 前的放行行为。
PI_SURFACE_SCOPE_ENFORCE_ENV = "PI_SURFACE_SCOPE_ENFORCE"

#: 封闭 drop reason codes（receipt / 披露面消费；新增需同步 ADR + 测试）。
DROP_TIER3 = "tier3_excluded"                    # tier / 生效安全层 >= 3
DROP_DESTRUCTIVE = "side_effect_destructive"     # DESTRUCTIVE 副作用类（低 tier 同样逐出）
DROP_CREDENTIAL_ABSENT = "credential_absent"     # 所需凭证已证实缺席
DROP_PERMISSION_NOT_GRANTED = "permission_not_granted"  # 所需权限已证实未授予（H01；与 canonical reason code 同名）
DROP_OFFLINE_NETWORK = "offline_network_tool"    # offline 已证实且工具需要网络
DROP_NO_DURABLE_WORKER = "durable_worker_unavailable"  # 执行策略需 durable worker 且已证实缺席
DROP_SPEC_OVER_BUDGET = "spec_over_budget"       # per-tool schema 预算（compile 期）
DROP_BYTE_BUDGET = "byte_budget"                 # surface 总预算（既有 D2 语义）

#: 全量 drop codes 封闭集合快照（H01：一致性守卫与跨层对照表消费）。
SURFACE_DROP_CODES = frozenset({
    DROP_TIER3,
    DROP_DESTRUCTIVE,
    DROP_CREDENTIAL_ABSENT,
    DROP_PERMISSION_NOT_GRANTED,
    DROP_OFFLINE_NETWORK,
    DROP_NO_DURABLE_WORKER,
    DROP_SPEC_OVER_BUDGET,
    DROP_BYTE_BUDGET,
})


def surface_scope_enforce_enabled() -> bool:
    """PI_SURFACE_SCOPE_ENFORCE（ADR-0288 起默认 ON；0/false/off/no 回退）。"""
    import os

    raw = (os.getenv(PI_SURFACE_SCOPE_ENFORCE_ENV) or "").strip().lower()
    return raw not in ("0", "false", "off", "no")


@dataclass(frozen=True)
class SurfaceDrop:
    """单个候选被逐出 native 激活面的原因（封闭 codes）。"""
    tool: str
    code: str
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "code": self.code, "detail": self.detail[:96]}


@dataclass(frozen=True)
class SurfaceReceipt:
    """一个 turn 的工具面裁决记录（deterministic、可验证）。

    - ``selected`` 是 deterministic ordering（native 前门冻结序 + 动态候选
      按选择器序）；``dropped`` 按 (tool) 排序稳定；
    - ``surface_fingerprint`` 绑定 selected 全部 spec 指纹 + registry
      generation —— 回调 echo 与之对照即可判 stale（跨 turn / registry 漂移）；
    - ``tool_fingerprints`` 只含 selected（dormant 裸调无指纹可验，跳过）。
    """

    session_id: str
    turn_id: str
    candidates: Tuple[str, ...] = ()
    selected: Tuple[str, ...] = ()
    dropped: Tuple[SurfaceDrop, ...] = ()
    surface_fingerprint: str = ""
    registry_generation: str = ""
    schema_bytes: int = 0
    byte_budget: int = 0
    tool_fingerprints: Dict[str, str] = field(default_factory=dict)
    requires_confirmation_tools: Tuple[str, ...] = ()
    #: V6 弃权（ADR-0119 D2）：本轮未注入动态面（native 前门 only）。
    abstained: bool = False
    policy_version: str = SURFACE_QUALIFICATION_POLICY

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "candidates": list(self.candidates),
            "selected": list(self.selected),
            "dropped": [d.to_dict() for d in self.dropped],
            "surface_fingerprint": self.surface_fingerprint,
            "registry_generation": self.registry_generation,
            "schema_bytes": int(self.schema_bytes),
            "byte_budget": int(self.byte_budget),
            "tool_fingerprints": dict(self.tool_fingerprints),
            "requires_confirmation_tools": list(self.requires_confirmation_tools),
            "abstained": bool(self.abstained),
            "policy_version": self.policy_version,
        }


# ---------------------------------------------------------------------------
# Qualification（纯函数，可离线测试）
# ---------------------------------------------------------------------------

def qualify_surface(
    specs: Sequence[PiToolSpecV1],
    *,
    situation: Optional[Mapping[str, Any]] = None,
) -> Tuple[Tuple[str, ...], Tuple[SurfaceDrop, ...]]:
    """按 spec 安全/资源先验 + situation 阳性缺席事实过滤 candidates。

    输入 ``specs`` 必须是候选序（deterministic ordering 由调用方保证）；
    输出 (selected_names, drops)。native 前门（调用方负责恒含）不在此特判
    —— 前门同样要过安全面（tier-3/destructive 的前门本就不存在）。
    """
    situation = situation or {}
    credentials: Mapping[str, bool] = situation.get("credentials_present") or {}
    runtime_availability: Mapping[str, bool] = situation.get("runtime_availability") or {}
    offline = situation.get("offline")
    selected: list[str] = []
    drops: list[SurfaceDrop] = []
    for spec in specs:
        if spec.tier >= 3 or spec.effective_security_tier >= 3:
            drops.append(SurfaceDrop(
                spec.name, DROP_TIER3,
                f"tier={spec.tier}/sec={spec.effective_security_tier}",
            ))
            continue
        if spec.side_effect == "destructive":
            drops.append(SurfaceDrop(
                spec.name, DROP_DESTRUCTIVE,
                "native surface requires confirmation semantics; proxy path unchanged",
            ))
            continue
        missing = [
            cred for cred in spec.requires_credentials
            if credentials.get(cred) is False
        ]
        if missing:
            drops.append(SurfaceDrop(
                spec.name, DROP_CREDENTIAL_ABSENT,
                f"credentials absent: {', '.join(sorted(missing)[:4])}",
            ))
            continue
        # H01（K5）：权限先验披露 —— granted_permissions 键在场（供给成功）
        # 且未含所需权限 → 阳性缺席，逐出；键缺席（未供给）或截断 →
        # unknown 不排除（ADR-0224 裁决 3 纪律；执行权威仍在 registry 闸）。
        required_perm = getattr(spec, "required_permission", None)
        if required_perm:
            granted_perms = situation.get("granted_permissions")
            truncated = situation.get("granted_permissions_truncated") is True
            if (
                isinstance(granted_perms, list)
                and not truncated
                and required_perm not in granted_perms
            ):
                drops.append(SurfaceDrop(
                    spec.name, DROP_PERMISSION_NOT_GRANTED,
                    f"permission not granted: {required_perm[:64]}",
                ))
                continue
        if offline is True and spec.network is True:
            drops.append(SurfaceDrop(spec.name, DROP_OFFLINE_NETWORK, "offline=true"))
            continue
        if (
            spec.execution_policy in ("durable", "background", "celery")
            and runtime_availability.get("durable_worker") is False
        ):
            drops.append(SurfaceDrop(
                spec.name, DROP_NO_DURABLE_WORKER,
                f"execution_policy={spec.execution_policy}",
            ))
            continue
        selected.append(spec.name)
    return tuple(selected), tuple(drops)


# ---------------------------------------------------------------------------
# Receipt store（bounded；turn 生命周期绑定）
# ---------------------------------------------------------------------------

class _SurfaceReceiptStore:
    """(session_id, turn_id) → SurfaceReceipt 的进程级有界存储。

    - 一个 turn 内 receipt **不可变**（重复 record 同 key = no-op 并告警：
      registry generation 变化只影响下一 turn）；
    - turn 结束（bridge cleanup）/ session 清理时 evict；
    - 容量逐出按插入序（oldest first），与 bridge dispatch cache 同纪律。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._receipts: Dict[Tuple[str, str], SurfaceReceipt] = {}
        self._evicted: Dict[Tuple[str, str], None] = {}

    def record(self, receipt: SurfaceReceipt) -> SurfaceReceipt:
        """登记 receipt；返回**实际生效**的 receipt（同 turn 重投影 → 存量）。

        调用方（disclosure/链发射）必须消费返回值而非自铸对象 —— 否则披露
        面会出现无 receipt 支撑的指纹（review P2：re-projection 分叉）。
        """
        if not receipt.session_id or not receipt.turn_id:
            return receipt
        with self._lock:
            key = (receipt.session_id, receipt.turn_id)
            existing = self._receipts.get(key)
            if existing is not None:
                if existing.surface_fingerprint != receipt.surface_fingerprint:
                    logger.warning(
                        "[PiSurfaceReceipt] re-projection for turn %s changed fingerprint "
                        "(%s -> %s); keeping the turn-start receipt (surface immutable "
                        "within a turn; registry changes apply next turn)",
                        receipt.turn_id, existing.surface_fingerprint,
                        receipt.surface_fingerprint,
                    )
                return existing
            self._receipts[key] = receipt
            self._evicted.pop(key, None)
            while len(self._receipts) > _RECEIPT_STORE_MAX:
                oldest = next(iter(self._receipts))
                self._receipts.pop(oldest, None)
                self._evicted[oldest] = None
                while len(self._evicted) > _RECEIPT_STORE_MAX * 4:
                    self._evicted.pop(next(iter(self._evicted)), None)
            return receipt

    def was_evicted(self, session_id: str, turn_id: str) -> bool:
        with self._lock:
            return (session_id, turn_id) in self._evicted

    def lookup(
        self, session_id: str, turn_id: str
    ) -> Optional[SurfaceReceipt]:
        with self._lock:
            return self._receipts.get((session_id, turn_id))

    def evict_turn(self, session_id: str, turn_id: str) -> None:
        with self._lock:
            self._receipts.pop((session_id, turn_id), None)

    def evict_session(self, session_id: str) -> None:
        with self._lock:
            for key in [k for k in self._receipts if k[0] == session_id]:
                self._receipts.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._receipts.clear()
            self._evicted.clear()


surface_receipt_store = _SurfaceReceiptStore()


# ---------------------------------------------------------------------------
# Discovery grants（ADR-0288 / W05）：turn 内 additive 的发现记录。
#
# 语义：receipt 的 ``selected`` 是 turn 起始声明面，turn 内不可变；上游
# tool_search 在 turn 中途把注册超集内的 deferred 工具并入声明面（C12 merge
# 语义）。``_discovered_store`` 记录这些**已验证的**中途发现（tool → 指纹），
# 供 scope 校验放行与披露面观测。
#
# 授权纪律（红线）：发现从不扩大执行权限 —— grant 只对「spawn 注册超集内
# （model_visible ∧ tier<3 ∧ 非 DESTRUCTIVE）+ 指纹 echo 校验通过」的调用
# 铸造（``pi_bridge_surface_gate`` 调用 ``record_discovered_tool``），权限
# 等价于 webgis_execute 代理通道（同一注册超集 + 同一 dispatch 闸序）。
# 绝不是 LLM 自授权：铸造点在宿主 gate，且仅接受注册面成员。
# ---------------------------------------------------------------------------

#: 每 turn 发现 grant 上限（发现风暴有界；超出 = scope violation 事实拒绝）。
_DISCOVERY_GRANT_MAX = 16


class _DiscoveredStore:
    """(session_id, turn_id) → {tool: fingerprint} 的进程级有界存储。

    与 receipt store 同生命周期（turn 终态 evict_session / 容量逐出）；
    同 key 重复 grant 幂等（首次指纹为准 —— 与 receipt 不可变纪律一致）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._grants: Dict[Tuple[str, str], Dict[str, str]] = {}

    def record(
        self, session_id: str, turn_id: str, tool: str, fingerprint: str = ""
    ) -> bool:
        """登记发现 grant。返回 False = 超过每 turn 上限（拒绝）。"""
        with self._lock:
            key = (session_id, turn_id)
            grants = self._grants.setdefault(key, {})
            if tool in grants:
                return True  # 幂等：首次指纹为准
            if len(grants) >= _DISCOVERY_GRANT_MAX:
                return False
            grants[tool] = str(fingerprint or "")
            while len(self._grants) > _RECEIPT_STORE_MAX:
                oldest = next(iter(self._grants))
                self._grants.pop(oldest, None)
            return True

    def contains(self, session_id: str, turn_id: str, tool: str) -> bool:
        with self._lock:
            return tool in self._grants.get((session_id, turn_id), {})

    def fingerprint_of(self, session_id: str, turn_id: str, tool: str) -> Optional[str]:
        """grant 时记录的指纹（echo 缺席的 grant 记空串 = 无可对照值）。"""
        with self._lock:
            return self._grants.get((session_id, turn_id), {}).get(tool)

    def tools(self, session_id: str, turn_id: str) -> Tuple[str, ...]:
        with self._lock:
            return tuple(self._grants.get((session_id, turn_id), {}))

    def evict_session(self, session_id: str) -> None:
        with self._lock:
            for key in [k for k in self._grants if k[0] == session_id]:
                self._grants.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._grants.clear()


discovered_store = _DiscoveredStore()


def record_discovered_tool(
    session_id: str, turn_id: str, tool: str, fingerprint: str = ""
) -> bool:
    """铸造一个 turn 内的发现 grant（幂等、有界；见 _DiscoveredStore 纪律）。"""
    return discovered_store.record(session_id, turn_id, tool, fingerprint)


def discovered_tools(session_id: str, turn_id: str) -> Tuple[str, ...]:
    """该 turn 已 grant 的发现工具（披露面 / 诊断面只读）。"""
    return discovered_store.tools(session_id, turn_id)


def record_turn_surface(
    specs: Sequence[PiToolSpecV1],
    *,
    session_id: str,
    turn_id: str,
    candidates: Sequence[str],
    selected: Sequence[str],
    drops: Sequence[SurfaceDrop],
    byte_budget: int = 0,
    abstained: bool = False,
) -> SurfaceReceipt:
    """铸造并登记一个 turn 的 SurfaceReceipt（幂等：同 turn 首次为准）。"""
    generation = specs[0].registry_generation if specs else ""
    receipt = SurfaceReceipt(
        session_id=session_id,
        turn_id=turn_id,
        candidates=tuple(candidates),
        selected=tuple(selected),
        dropped=tuple(sorted(drops, key=lambda d: (d.tool, d.code))),
        surface_fingerprint=surface_fingerprint(
            specs, registry_generation=generation,
        ),
        registry_generation=generation,
        schema_bytes=sum(s.schema_bytes for s in specs),
        byte_budget=int(byte_budget or 0),
        tool_fingerprints={s.name: s.fingerprint() for s in specs},
        requires_confirmation_tools=tuple(
            s.name for s in specs if s.requires_confirmation
        ),
        abstained=bool(abstained),
    )
    # 返回实际生效的 receipt（同 turn 重投影 → 存量原 receipt）。
    return surface_receipt_store.record(receipt)


# ---------------------------------------------------------------------------
# 回调校验（route 消费；echo 缺席 = master 行为）
# ---------------------------------------------------------------------------

def verify_callback_fingerprints(
    *,
    session_id: str,
    turn_id: str,
    resolved_tool: str,
    tool_fingerprint: str = "",
) -> Optional[str]:
    """对照 turn receipt 校验回调 echo；返回错误码或 None（通过）。

    - receipt 缺席（动态面关/弃成/前 master turn）→ 一律放行（兼容）；
    - ``toolFingerprint`` echo：resolved 工具在 receipt.selected 内且指纹
      不符 → ``SURFACE_FINGERPRINT_MISMATCH``（registry 代内漂移 / 跨 turn
      重放）；不在 selected 内（dormant 裸调）时对照 **discovery grant**
      记录的指纹（ADR-0288：grant 后的后续调用也要对拍 —— 伪造/过期 echo
      不得因已 grant 而绕过）；grant 未记录指纹（echo 缺席的 grant）或无
      echo → 跳过。
    - 刻意**不设**面级（surface）echo：turn 的 surface 指纹是 receipt
      私有值，调用方（扩展）无从合法获知 —— dump 超集指纹与 turn 选中面
      指纹构造性不同，接入只会产生永假阳性（review P2-2）。
    """
    receipt = surface_receipt_store.lookup(session_id, turn_id)
    if receipt is None:
        if surface_receipt_store.was_evicted(session_id, turn_id):
            return "SURFACE_RECEIPT_EVICTED"
        return None
    if tool_fingerprint:
        expected = receipt.tool_fingerprints.get(resolved_tool)
        if expected is None:
            expected = discovered_store.fingerprint_of(session_id, turn_id, resolved_tool) or None
        if expected is not None and expected != tool_fingerprint:
            return "SURFACE_FINGERPRINT_MISMATCH"
    return None


def enforce_callback_scope(
    *,
    session_id: str,
    turn_id: str,
    resolved_tool: str,
) -> Optional[str]:
    """bare-name 面成员校验（PI_SURFACE_SCOPE_ENFORCE，ADR-0288 起默认 ON）。

    返回 ``SURFACE_SCOPE_VIOLATION`` 表示 resolved_tool 不在本 turn 的
    ``selected ∪ discovered`` 集内（native 前门 + selected 动态面 + 本 turn
    已 grant 的发现）；proxy 通道（webgis_execute unwrap）不经此函数。
    receipt 缺席 → 放行。发现 grant 的铸造在 ``pi_bridge_surface_gate``
    （注册超集 + 指纹校验通过才 grant）—— 本函数只消费 grant 集合。
    """
    if not surface_scope_enforce_enabled():
        return None
    receipt = surface_receipt_store.lookup(session_id, turn_id)
    if receipt is None:
        if surface_receipt_store.was_evicted(session_id, turn_id):
            return "SURFACE_RECEIPT_EVICTED"
        return None
    if resolved_tool in receipt.selected:
        return None
    if discovered_store.contains(session_id, turn_id, resolved_tool):
        return None
    return "SURFACE_SCOPE_VIOLATION"
