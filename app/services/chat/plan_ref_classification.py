"""Distinguish plan control refs from reusable session data."""
# 计划自身 ref 的判定（P2-6 / recovery P2）：plan-mode 计划以 ref:plan-* 存，
# canonical 计划经别名 plan-current / plan-id:* 寻址。它们只代表"计划存在"，
# 不代表会话有可复用的数据 —— session_has_refs 必须把它们排除，否则
# "仅存了计划"的会话会被 ref_reuse 分类误判。
_PLAN_REF_PREFIX = "ref:plan-"
_PLAN_ALIAS_PREFIX = "plan-id:"
_PLAN_CURRENT_ALIAS = "plan-current"


def _has_non_plan_refs(refs: dict) -> bool:
    """list_refs() 结果里是否存在非计划数据引用（ref:plan-* / plan 别名除外）。"""
    from app.services.session_plan import is_session_plan_listing
    for rid, alias in (refs or {}).items():
        rid_s = str(rid)
        alias_s = str(alias or "")
        if rid_s.startswith(_PLAN_REF_PREFIX):
            continue
        if alias_s == _PLAN_CURRENT_ALIAS or alias_s.startswith(_PLAN_ALIAS_PREFIX):
            continue
        if is_session_plan_listing(rid_s, alias_s):
            continue
        return True
    return False
