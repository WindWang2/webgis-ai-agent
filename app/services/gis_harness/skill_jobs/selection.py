"""G30 用户技能选择解析 —— ``chat.skill_name`` 的向后兼容语义。

解析规则（closed；同名资产不静默合并、不按加载顺序误跑）：

1. **精确 id**：``SkillLibrary`` 中 id 相等 → resolved(match_type=id)；
2. **精确 name**：GIS 技能 name 相等 → 唯一命中 resolved(match_type=name)，
   多个命中 → ambiguous（列出候选，不猜）；
3. **md 指南**：``app/skills/*.md`` 命中 → resolved(match_type=md_guide)——
   知识资产无执行面：披露「仅方法指导」，绝不映射到同域 GIS 技能；
4. **未命中**：not_found（附近邻建议 = id 前缀/名称包含，仅提示）。

resolved 的 GIS 技能记录 skill_id/version/digest；弃用/pack 非 core 由
policy/prepare 层照常裁决（这里只做身份解析，不做资格）。
"""
from __future__ import annotations

from typing import Any, Dict, List


def resolve_user_skill_selection(skill_name: str) -> Dict[str, Any]:
    """解析用户提供的技能名 → 有界选择记录（不抛异常；不确定即披露）。"""
    name = str(skill_name or "").strip()[:128]
    if not name:
        return {"status": "empty"}

    from app.services.gis_harness.skills.loader import get_skill_library

    lib = get_skill_library()

    # 1) 精确 id
    skill = lib.get(name)
    if skill is not None:
        return {
            "status": "resolved",
            "match_type": "id",
            "skill_id": skill.id,
            "skill_version": skill.version,
            "skill_digest": skill.fingerprint()[:16],
            "requested_name": name,
            "runnable": skill.is_runnable_asset(),
            "deprecated": skill.deprecated,
            "deprecated_by": skill.deprecated_by if skill.deprecated else "",
        }

    # 2) 精确 name（GIS 技能）
    by_name = [s for s in lib.skills if s.name == name]
    if len(by_name) == 1:
        s = by_name[0]
        return {
            "status": "resolved",
            "match_type": "name",
            "skill_id": s.id,
            "skill_version": s.version,
            "skill_digest": s.fingerprint()[:16],
            "requested_name": name,
            "runnable": s.is_runnable_asset(),
            "deprecated": s.deprecated,
            "deprecated_by": s.deprecated_by if s.deprecated else "",
        }
    if len(by_name) > 1:
        return {
            "status": "ambiguous",
            "match_type": "name",
            "requested_name": name,
            "candidates": [
                {"skill_id": s.id, "name": s.name, "version": s.version}
                for s in by_name[:5]
            ],
            "hint": "多个同名技能：请用 skill_id 精确指定",
        }

    # 3) md 指南（另一资产类型：只读知识，无执行面）
    try:
        from app.tools.skills import get_md_skill, list_md_skills

        if get_md_skill(name) is not None:
            return {
                "status": "resolved",
                "match_type": "md_guide",
                "requested_name": name,
                "runnable": False,
                "hint": (
                    "这是方法指南（markdown 知识资产），无执行面；"
                    "GIS 技能作业请从技能目录选择可执行技能"),
            }
        # 大小写/空白不敏感的名称匹配（防御直接复制名称时的误差）
        lower = name.lower()
        for item in list_md_skills():
            if str(item.get("name", "")).strip().lower() == lower:
                return {
                    "status": "resolved",
                    "match_type": "md_guide",
                    "requested_name": name,
                    "runnable": False,
                    "hint": "这是方法指南（markdown 知识资产），无执行面",
                }
    except Exception:  # noqa: BLE001 — 指南面缺席不影响技能解析
        pass

    # 4) 未命中：近邻建议（id 前缀或名称包含；仅提示，不猜）
    suggestions: List[Dict[str, str]] = []
    for s in lib.skills:
        if s.id.startswith(name) or name in s.name or name in s.id:
            suggestions.append({"skill_id": s.id, "name": s.name,
                                "version": s.version})
        if len(suggestions) >= 5:
            break
    return {
        "status": "not_found",
        "requested_name": name,
        "suggestions": suggestions,
        "hint": "未找到该技能；可用 gis_skill_search 检索或查看技能目录",
    }


def selection_notice_text(selection: Dict[str, Any]) -> str:
    """有界披露文案（SSE notice / Pi 提示共用；≤240 字）。"""
    status = str(selection.get("status") or "")
    if status == "resolved":
        if selection.get("match_type") == "md_guide":
            return (
                f"（提示：skill_name={selection.get('requested_name')} 是方法"
                "指南（知识资产，无执行面）；本次按普通对话处理，不注入执行。) ")
        dep = ""
        if selection.get("deprecated"):
            dep = f"；该版本已弃用（替代：{selection.get('deprecated_by') or '未声明'}）"
        run = "可执行" if selection.get("runnable") else "仅方法指导"
        return (
            f"（已选择技能 {selection.get('skill_id')} "
            f"v{selection.get('skill_version')}（{run}{dep}）；"
            "规划将尊重该选择；执行可经 gis_skill_job_prepare → "
            "gis_skill_job_start。）"
        )
    if status == "ambiguous":
        cands = ", ".join(
            c.get("skill_id", "") for c in (selection.get("candidates") or [])[:3])
        return (
            f"（skill_name={selection.get('requested_name')} 匹配多个同名技能："
            f"{cands}；请用 skill_id 精确指定，本次按普通对话处理。）")
    if status == "not_found":
        sug = ", ".join(
            c.get("skill_id", "") for c in (selection.get("suggestions") or [])[:3])
        tail = f"相近技能：{sug}。" if sug else ""
        return (
            f"（未找到技能 skill_name={selection.get('requested_name')}。{tail}"
            "可用 gis_skill_search 检索；本次按普通对话处理。）")
    return ""
