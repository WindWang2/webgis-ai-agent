"""G30 参数预设服务 —— 可复用配置引用（不是第二方法市场）。

预设内容纪律：

- 只存**配置引用**：技能 id+版本+digest、参数值、数据角色绑定模板
  （角色 → 上次 ref（仅提示）+ 事实摘要）；**绝不**复制原始私有数据、
  凭据、一次性下载地址；
- 重用 = 调用方显式提供新 bindings（refs 重新授权）→ 重新 prepare →
  重新资格/版本校验；预设不携带任何执行授权；
- 可见性随 org/project 归属：列表按请求者 org 过滤，复制预设不复制
  他人私有数据（模板里只有 ref 提示，无数据内容）；
- 修改预设 = 新的 updated_at（配置修订）；历史运行记录在 job 行内仍能
  解释当时的原值（params 快照在 run_record）。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PresetPayload(BaseModel):
    name: str = Field(..., max_length=120)
    description: str = Field("", max_length=400)
    skill_id: str = Field(..., max_length=80)
    skill_version: str = Field("", max_length=16)
    params: Dict[str, Any] = Field(default_factory=dict)
    bindings_template: Dict[str, Any] = Field(default_factory=dict)


def _row_to_view(row: Any) -> Dict[str, Any]:
    params = row.params if isinstance(row.params, dict) else {}
    # 参数值有界投影（预设是配置，值本身有界；防御性截断长字符串）
    bounded_params = {
        k: (v if isinstance(v, (int, float, bool)) or v is None
            else str(v)[:64])
        for k, v in list(params.items())[:16]
    }
    return {
        "id": row.id,
        "name": row.name[:120],
        "description": (row.description or "")[:400],
        "skill_id": row.skill_id,
        "skill_version": row.skill_version,
        "skill_digest": (row.skill_digest or "")[:16],
        "params": bounded_params,
        "bindings_template": row.bindings_template if isinstance(row.bindings_template, dict) else {},
        "project_id": row.project_id or "",
        "creator_id": row.creator_id or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def save_preset(
    payload: PresetPayload,
    *,
    org_id: Optional[int],
    creator_id: str,
    project_id: str = "",
    session_id: str = "",
    preset_id: Optional[str] = None,
) -> Dict[str, Any]:
    """保存/更新预设（内容含技能资产身份快照）。"""
    from app.core.database import SessionLocal
    from app.models.db_model import SkillJobPreset
    from app.services.gis_harness.skills.loader import get_skill_library

    import json as _json

    _payload_bytes = len(_json.dumps(
        {"p": payload.params, "b": payload.bindings_template},
        ensure_ascii=False, default=str))
    if _payload_bytes > 16 * 1024:
        raise ValueError("预设载荷超限（≤16KB）：预设是配置引用，不承载大对象")

    lib = get_skill_library()
    skill = lib.get(payload.skill_id)
    if skill is None:
        raise ValueError(f"skill {payload.skill_id!r} not found")
    version = payload.skill_version or skill.version
    if version != skill.version:
        raise ValueError(
            f"skill {payload.skill_id} 当前版本 {skill.version}（预设请求 {version}）")

    with SessionLocal() as db:
        if preset_id:
            row = db.get(SkillJobPreset, preset_id)
            # review P1-1：更新路径与 get/delete 同规则校验 org（跨 org 一律
            # not found，不泄露存在性）。
            if row is None or row.org_id != org_id:
                raise ValueError("preset not found")
            row.name = payload.name
            row.description = payload.description
            row.skill_id = payload.skill_id
            row.skill_version = version
            row.skill_digest = skill.fingerprint()
            row.params = dict(payload.params)
            row.bindings_template = dict(payload.bindings_template)
        else:
            row = SkillJobPreset(
                id=str(uuid.uuid4()),
                org_id=org_id,
                creator_id=creator_id or None,
                project_id=project_id or "",
                session_id=session_id or "",
                name=payload.name,
                description=payload.description,
                skill_id=payload.skill_id,
                skill_version=version,
                skill_digest=skill.fingerprint(),
                params=dict(payload.params),
                bindings_template=dict(payload.bindings_template),
            )
            db.add(row)
        db.commit()
        db.refresh(row)
        return _row_to_view(row)


def list_presets(
    *,
    org_id: Optional[int],
    project_id: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """按 org（+可选 project）列出可见预设（有界）。"""
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.db_model import SkillJobPreset

    stmt = select(SkillJobPreset).where(SkillJobPreset.org_id == org_id)
    if project_id:
        stmt = stmt.where(SkillJobPreset.project_id == project_id)
    stmt = stmt.order_by(SkillJobPreset.updated_at.desc()).limit(min(max(limit, 1), 100))
    with SessionLocal() as db:
        rows = db.execute(stmt).scalars().all()
        return [_row_to_view(r) for r in rows]


def get_preset_raw(preset_id: str, *, org_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """原始（未截断）预设数据 —— 仅供重用/prepare 路径（review P1-4：
    _row_to_view 的有界投影截断长参数，不能作为重用真相）。"""
    from app.core.database import SessionLocal
    from app.models.db_model import SkillJobPreset

    with SessionLocal() as db:
        row = db.get(SkillJobPreset, preset_id)
        if row is None or row.org_id != org_id:
            return None
        return {
            "params": dict(row.params) if isinstance(row.params, dict) else {},
            "bindings_template": (
                dict(row.bindings_template)
                if isinstance(row.bindings_template, dict) else {}),
        }


def get_preset(preset_id: str, *, org_id: Optional[int]) -> Optional[Dict[str, Any]]:
    from app.core.database import SessionLocal
    from app.models.db_model import SkillJobPreset

    with SessionLocal() as db:
        row = db.get(SkillJobPreset, preset_id)
        if row is None or row.org_id != org_id:
            return None
        return _row_to_view(row)


def delete_preset(preset_id: str, *, org_id: Optional[int]) -> bool:
    from app.core.database import SessionLocal
    from app.models.db_model import SkillJobPreset

    with SessionLocal() as db:
        row = db.get(SkillJobPreset, preset_id)
        if row is None or row.org_id != org_id:
            return False
        db.delete(row)
        db.commit()
        return True
