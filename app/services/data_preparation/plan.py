"""准备计划 —— closed-union 步集、前置校验、幂等键与持久化（M4；契约 §4）。

设计纪律：
- **closed union**：步类型是 Literal 判别式（``type``），未知类型
  fail-closed 拒收 —— 与 descriptor ``from_dict`` 版本拒收同纪律；
- **前置校验**：步不是自由组合 —— AxisSwapStep 必须有收据里的
  AXIS_ORDER_SUSPECT 事实背书（防「顺手一换」）；ReprojectStep 必须有
  已声明 CRS（crs_source=assumed 的数据重投影 = 把猜测变事实，禁止）；
- **幂等键**：plan_hash = sha256(input_fingerprint + canonical steps) ——
  同输入同计划重复执行返回既有 receipt（executor 侧查询）；
- **持久化**：会话目录 JSONL 有界 ring（与 provenance 同纪律：per-writer
  mkstemp + os.replace 原子替换；坏行跳过；超限丢最旧）；
- **状态机**：proposed → previewed → executed → (verified | rolled_back)；
  discarded 仅允许自 proposed/previewed（executed 计划的清理走 rolled_back）。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, field_validator

from app.services.data_preparation.vocabulary import (
    is_proposal_role,
)

#: 计划状态机（closed 词表）。
PLAN_STATES = ("proposed", "previewed", "executed", "verified",
               "rolled_back", "discarded")

#: 有界上限。
MAX_PLAN_STEPS = 16
_MAX_PLAN_RECORDS = 64
MAX_FILTER_DROP_FRACTION = 0.5
DEFAULT_MAX_DROP_FRACTION = 0.05
MAX_KEY_FIELDS = 4

#: FilterRowsStep 允许的条件码（closed；与 quality 码对齐）。
FILTER_CONDITIONS = ("impossible_coordinates", "null_geometry")

_PLANS_FILE = "plans.jsonl"


# ── 步模型（closed union）──────────────────────────────────────────────────

class PlanStep(BaseModel):
    """步基类（判别式 type）。"""

    type: str

    def canonical(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True, by_alias=True)


class FieldMappingStep(PlanStep):
    """角色→字段映射（元数据步：不改数据，确认语义写入 receipt 与 descriptor）。"""

    type: Literal["field_mapping"] = "field_mapping"
    role: str
    field: str
    source: Literal["proposal", "user"] = "proposal"

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if not is_proposal_role(v):
            raise ValueError(f"unregistered proposal role: {v!r}")
        return v


class AxisSwapStep(PlanStep):
    """经纬度对调修正（数据步：几何坐标对换 + 两列属性值互换）。"""

    type: Literal["axis_swap"] = "axis_swap"
    lon_field: str
    lat_field: str


class FilterRowsStep(PlanStep):
    """按 typed 条件剔除行（数据步；受 max_drop_frac 硬闸保护）。"""

    type: Literal["filter_rows"] = "filter_rows"
    condition_code: str
    max_drop_frac: float = DEFAULT_MAX_DROP_FRACTION

    @field_validator("condition_code")
    @classmethod
    def _cond(cls, v: str) -> str:
        if v not in FILTER_CONDITIONS:
            raise ValueError(f"unregistered filter condition: {v!r}")
        return v

    @field_validator("max_drop_frac")
    @classmethod
    def _frac(cls, v: float) -> float:
        v = float(v)
        if not (0.0 < v <= MAX_FILTER_DROP_FRACTION):
            raise ValueError(f"max_drop_frac out of (0, {MAX_FILTER_DROP_FRACTION}]")
        return v


class DedupStep(PlanStep):
    """按键列去重（数据步；稳定保首）。"""

    type: Literal["dedup"] = "dedup"
    key_fields: Tuple[str, ...] = ()

    @field_validator("key_fields")
    @classmethod
    def _keys(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        vals = tuple(str(k)[:128] for k in v if str(k))[:MAX_KEY_FIELDS]
        if not vals:
            raise ValueError("dedup requires at least one key field")
        return vals


class RenameFieldStep(PlanStep):
    """字段重命名（数据步；属性键替换）。"""

    type: Literal["rename_field"] = "rename_field"
    from_field: str
    to_field: str


class ReprojectStep(PlanStep):
    """重投影（数据步；委托 data_quality.autofix 的 reproject 实现 ——
    pyproj 缺席时诚实降级 attach-CRS，不在本域重复造坐标变换）。"""

    type: Literal["reproject"] = "reproject"
    target_crs: str


PreparationStep = Union[
    FieldMappingStep, AxisSwapStep, FilterRowsStep,
    DedupStep, RenameFieldStep, ReprojectStep,
]

_STEP_MODELS = {
    "field_mapping": FieldMappingStep,
    "axis_swap": AxisSwapStep,
    "filter_rows": FilterRowsStep,
    "dedup": DedupStep,
    "rename_field": RenameFieldStep,
    "reproject": ReprojectStep,
}

#: 数据变异步（决定是否铸造新 ref；field_mapping 是纯元数据步）。
DATA_MUTATING_TYPES = frozenset({"axis_swap", "filter_rows", "dedup",
                                 "rename_field", "reproject"})


def parse_step(data: Any) -> PlanStep:
    """dict → 步（closed union fail-closed：未知 type 拒收）。"""
    if not isinstance(data, dict):
        raise ValueError("step must be a dict")
    stype = str(data.get("type") or "")
    model = _STEP_MODELS.get(stype)
    if model is None:
        raise ValueError(f"unregistered step type: {stype!r}")
    return model.model_validate(data)


class PlanValidationError(ValueError):
    """计划前置校验失败（typed；携带稳定 reason 码）。"""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)
        self.reason_code = reason_code
        self.detail = str(detail)[:200]


# ── 计划模型 ────────────────────────────────────────────────────────────────

def compute_plan_hash(input_fingerprint: str, steps: Sequence[PlanStep]) -> str:
    """(输入指纹, canonical steps) → sha256 前 16 位（幂等键）。"""
    blob = json.dumps(
        {"input_fingerprint": str(input_fingerprint or ""),
         "steps": [s.canonical() for s in steps]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


class PreparationPlan(BaseModel):
    """一次数据准备的待执行配置（可预览、可撤销、幂等）。"""

    plan_id: str
    session_id: str = ""
    owner: str = ""
    input_ref: str = ""
    input_fingerprint: str = ""     # dsd-v1:…（输入 descriptor 指纹）
    steps: Tuple[PlanStep, ...] = ()
    plan_hash: str = ""
    state: str = "proposed"
    created_at: float = 0.0

    @field_validator("state")
    @classmethod
    def _state(cls, v: str) -> str:
        if v not in PLAN_STATES:
            raise ValueError(f"unregistered plan state: {v!r}")
        return v

    @field_validator("steps", mode="before")
    @classmethod
    def _steps(cls, v: Any) -> Tuple[PlanStep, ...]:
        if isinstance(v, tuple) and all(isinstance(s, PlanStep) for s in v):
            return v[:MAX_PLAN_STEPS]
        steps = tuple(parse_step(s) for s in (v or []))
        if len(steps) > MAX_PLAN_STEPS:
            raise ValueError(f"plan exceeds {MAX_PLAN_STEPS} steps")
        return steps

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id[:64],
            "session_id": self.session_id[:128],
            "owner": self.owner[:128],
            "input_ref": self.input_ref[:200],
            "input_fingerprint": self.input_fingerprint[:96],
            "steps": [s.canonical() for s in self.steps],
            "plan_hash": self.plan_hash[:32],
            "state": self.state,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["PreparationPlan"]:
        if not isinstance(data, dict):
            return None
        try:
            return cls.model_validate({
                "plan_id": data.get("plan_id"),
                "session_id": data.get("session_id"),
                "owner": data.get("owner"),
                "input_ref": data.get("input_ref"),
                "input_fingerprint": data.get("input_fingerprint"),
                "steps": data.get("steps") or [],
                "plan_hash": data.get("plan_hash"),
                "state": data.get("state") or "proposed",
                "created_at": float(data.get("created_at") or 0.0),
            })
        except Exception:  # noqa: BLE001 — 坏记录跳过（append-only 容忍）
            return None

    @property
    def mutates_data(self) -> bool:
        return any(s.type in DATA_MUTATING_TYPES for s in self.steps)


def build_plan(
    *,
    session_id: str,
    owner: str,
    input_ref: str,
    input_fingerprint: str,
    steps: Sequence[PlanStep | Dict[str, Any]],
    axis_suspect_evidence: Optional[Dict[str, Any]] = None,
    crs_source: str = "",
    available_fields: Optional[Sequence[str]] = None,
) -> PreparationPlan:
    """构造 + 前置校验（不落盘；持久化由 save_plan 显式完成）。

    ``axis_suspect_evidence``：收据 AXIS_ORDER_SUSPECT finding 的 evidence
    （含 lon_field/lat_field）—— AxisSwapStep 的唯一合法来源。
    ``crs_source``：declared/source/assumed/rfc7946_default —— reproject 闸。
    ``available_fields``：输入数据字段全集 —— 步引用字段存在性校验。
    """
    import time
    import uuid

    parsed: List[PlanStep] = []
    for s in steps:
        parsed.append(s if isinstance(s, PlanStep) else parse_step(s))
    if not parsed:
        raise PlanValidationError("EMPTY_PLAN", "计划至少包含一个步")
    if len(parsed) > MAX_PLAN_STEPS:
        raise PlanValidationError("TOO_MANY_STEPS", f">{MAX_PLAN_STEPS}")

    fields = set(available_fields) if available_fields is not None else None
    suspect_fields: Dict[str, str] = {}
    ev = axis_suspect_evidence or {}
    if isinstance(ev, dict):
        if ev.get("lon_field"):
            suspect_fields["lon"] = str(ev["lon_field"])
        if ev.get("lat_field"):
            suspect_fields["lat"] = str(ev["lat_field"])

    for step in parsed:
        if isinstance(step, FieldMappingStep):
            if fields is not None and step.field not in fields:
                raise PlanValidationError("UNKNOWN_FIELD", step.field)
        elif isinstance(step, AxisSwapStep):
            if not suspect_fields:
                raise PlanValidationError(
                    "SWAP_WITHOUT_EVIDENCE",
                    "AxisSwapStep 需要 inspect 收据的 axis_order_suspect 事实背书")
            if (suspect_fields.get("lon") != step.lon_field
                    or suspect_fields.get("lat") != step.lat_field):
                raise PlanValidationError(
                    "SWAP_FIELDS_MISMATCH",
                    f"收据嫌疑列 {suspect_fields} 与步字段不一致")
        elif isinstance(step, FilterRowsStep):
            pass  # 条件码在模型层 closed
        elif isinstance(step, DedupStep):
            if fields is not None and any(k not in fields for k in step.key_fields):
                raise PlanValidationError("UNKNOWN_FIELD", ",".join(step.key_fields))
        elif isinstance(step, RenameFieldStep):
            if fields is not None and step.from_field not in fields:
                raise PlanValidationError("UNKNOWN_FIELD", step.from_field)
            if fields is not None and step.to_field in fields:
                raise PlanValidationError("FIELD_ALREADY_EXISTS", step.to_field)
        elif isinstance(step, ReprojectStep):
            if not crs_source or crs_source == "assumed":
                raise PlanValidationError(
                    "REPROJECT_ON_ASSUMED_CRS",
                    "crs_source=assumed 的数据不得重投影 —— 先声明真实 CRS")

    plan = PreparationPlan(
        plan_id=f"prep-{uuid.uuid4().hex[:16]}",
        session_id=str(session_id or "")[:128],
        owner=str(owner or "")[:128],
        input_ref=str(input_ref or "")[:200],
        input_fingerprint=str(input_fingerprint or "")[:96],
        steps=tuple(parsed),
        state="proposed",
        created_at=time.time(),
    )
    plan.plan_hash = compute_plan_hash(plan.input_fingerprint, plan.steps)
    return plan


# ── 持久化（JSONL ring；与 provenance 同纪律）──────────────────────────────

def _plans_base() -> Path:
    from app.services.mapspec.store import BASE_STORAGE_DIR

    return BASE_STORAGE_DIR.resolve()


def _plans_path(session_id: str) -> Path:
    root = _plans_base()
    session_dir = (root / str(session_id)).resolve()
    if session_dir.parent != root:
        raise ValueError("invalid session id for preparation plan storage")
    return session_dir / "data_preparation" / _PLANS_FILE


def _load_sync(path: Path) -> List[PreparationPlan]:
    if not path.exists():
        return []
    out: List[PreparationPlan] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                plan = PreparationPlan.from_dict(json.loads(line))
                if plan is not None:
                    out.append(plan)
    except Exception:  # noqa: BLE001 — 读失败按空（诚实缺席）
        return []
    return out[-_MAX_PLAN_RECORDS:]


def _append_sync(path: Path, plan: PreparationPlan) -> int:
    records = _load_sync(path)
    # upsert：同 plan_id 覆盖旧记录（状态迁移），否则追加。
    records = [r for r in records if r.plan_id != plan.plan_id]
    records.append(plan)
    records = records[-_MAX_PLAN_RECORDS:]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r.to_bounded_dict(), ensure_ascii=False,
                                   sort_keys=True, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return len(records)


async def save_plan(plan: PreparationPlan) -> bool:
    """计划落盘（状态迁移持久化；失败 False 诚实披露）。"""
    import asyncio

    try:
        await asyncio.to_thread(_append_sync, _plans_path(plan.session_id), plan)
        return True
    except Exception:  # noqa: BLE001 — 持久化失败不炸调用方
        return False


async def get_plan(session_id: str, plan_id: str) -> Optional[PreparationPlan]:
    import asyncio

    try:
        records = await asyncio.to_thread(_load_sync, _plans_path(session_id))
    except Exception:  # noqa: BLE001
        return None
    for r in records:
        if r.plan_id == plan_id:
            return r
    return None


async def find_executed_by_hash(session_id: str, plan_hash: str) -> Optional[PreparationPlan]:
    import asyncio

    try:
        records = await asyncio.to_thread(_load_sync, _plans_path(session_id))
    except Exception:  # noqa: BLE001
        return None
    for r in reversed(records):
        if r.plan_hash == plan_hash and r.state in ("executed", "verified"):
            return r
    return None


async def list_plans(session_id: str) -> List[PreparationPlan]:
    import asyncio

    try:
        return await asyncio.to_thread(_load_sync, _plans_path(session_id))
    except Exception:  # noqa: BLE001
        return []


async def discard_plan(session_id: str, plan_id: str) -> Optional[PreparationPlan]:
    """未执行计划作废（executed/verified/rolled_back 不可作废）。"""
    plan = await get_plan(session_id, plan_id)
    if plan is None:
        return None
    if plan.state in ("executed", "verified", "rolled_back"):
        raise PlanValidationError("DISCARD_INVALID_STATE", plan.state)
    plan.state = "discarded"
    await save_plan(plan)
    return plan
