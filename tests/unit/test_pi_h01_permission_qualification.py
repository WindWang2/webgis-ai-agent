"""H01（K5）：required_permission 的 turn 面先验披露。

master 现状：``PiToolSpecV1`` 不投影 ``required_permission``，turn 资格层
（qualify_surface）不查权限 —— 模型可见「可调」但 dispatch 恒
PERMISSION_DENIED。本文件钉住加性修复：

- spec 投影 descriptor.required_permission（条件序列化：未声明不出键，
  既有工具指纹零漂移）；
- qualify_surface 消费 situation 的 ``granted_permissions``：阳性缺席
  （供给成功且未授予）才逐出（DROP_PERMISSION_NOT_GRANTED）；
- 供给失败 / 截断 → unknown → 不排除（ADR-0224 裁决 3 纪律）；
- RuntimeSituation.to_qualification_dict 加性携带 granted_permissions
  （空列表 = 供给成功零授予，与键缺席 = 未供给可区分）。
"""
import pytest

from app.services.chat.pi_surface_receipt import (
    DROP_PERMISSION_NOT_GRANTED,
    qualify_surface,
)
from app.services.chat.pi_tool_spec import compile_tool_spec
from app.services.gis_harness.hotpath_convergence.runtime_situation import (
    RuntimeSituation,
)
from app.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def abi_on(monkeypatch):
    monkeypatch.delenv("PI_TOOL_ABI", raising=False)


def _registry_with(permission: str | None) -> ToolRegistry:
    registry = ToolRegistry()
    kwargs = {"required_permission": permission} if permission else {}
    registry.register(
        "h01_perm_tool",
        "permission-gated tool (H01)",
        lambda **kw: {"success": True},
        parameters={"type": "object", "properties": {}},
        **kwargs,
    )
    return registry


def _spec(registry: ToolRegistry):
    spec = compile_tool_spec(registry, "h01_perm_tool")
    assert spec is not None
    return spec


# ── spec 投影 ────────────────────────────────────────────────────────


def test_spec_projects_required_permission():
    spec = _spec(_registry_with("admin"))
    assert spec.required_permission == "admin"
    dumped = spec.to_dump_dict()
    assert dumped["required_permission"] == "admin"


def test_spec_without_permission_omits_key_fingerprint_stable():
    """未声明权限的工具：不出键 —— 既有 300+ 工具指纹零漂移。"""
    spec = _spec(_registry_with(None))
    assert spec.required_permission is None
    assert "required_permission" not in spec.to_dict()
    assert "required_permission" not in spec.to_dump_dict()


def test_permission_participates_in_fingerprint():
    with_perm = _spec(_registry_with("admin")).fingerprint()
    without_perm = _spec(_registry_with(None)).fingerprint()
    assert with_perm != without_perm


# ── qualify_surface 权限裁决 ─────────────────────────────────────────


def _sit(**kw):
    base = {
        "credentials_present": {},
        "granted_permissions": [],
    }
    base.update(kw)
    return base


def test_qualify_surface_drops_ungranted_permission():
    spec = _spec(_registry_with("admin"))
    selected, drops = qualify_surface([spec], situation=_sit())
    assert selected == ()
    assert drops and drops[0].code == DROP_PERMISSION_NOT_GRANTED
    assert "admin" in drops[0].detail


def test_qualify_surface_keeps_tool_when_permission_granted():
    spec = _spec(_registry_with("admin"))
    selected, drops = qualify_surface(
        [spec], situation=_sit(granted_permissions=["admin"])
    )
    assert selected == ("h01_perm_tool",)
    assert drops == ()


def test_qualify_surface_unknown_supply_keeps_tool():
    """situation 未供给权限面（供给失败/旧调用方）→ unknown 不排除。"""
    spec = _spec(_registry_with("admin"))
    selected, drops = qualify_surface(
        [spec], situation={"credentials_present": {}}
    )
    assert selected == ("h01_perm_tool",)
    assert drops == ()


def test_qualify_surface_truncated_supply_keeps_tool():
    """授予集被截断（>上限）→ 缺席不可判 → 不排除（诚实 unknown）。"""
    spec = _spec(_registry_with("admin"))
    selected, drops = qualify_surface(
        [spec],
        situation=_sit(
            granted_permissions=["other"],
            granted_permissions_truncated=True,
        ),
    )
    assert selected == ("h01_perm_tool",)
    assert drops == ()


def test_qualify_surface_no_permission_requirement_unaffected():
    spec = _spec(_registry_with(None))
    selected, drops = qualify_surface([spec], situation=_sit())
    assert selected == ("h01_perm_tool",)
    assert drops == ()


# ── RuntimeSituation 投影 ────────────────────────────────────────────


def test_to_qualification_dict_carries_granted_permissions():
    situation = RuntimeSituation(
        session_id="s1",
        credentials_present={"perm:admin": True, "cred:x": True},
    )
    qual = situation.to_qualification_dict()
    assert qual["granted_permissions"] == ["admin"]


def test_to_qualification_dict_empty_grants_are_explicit():
    """零授予：granted_permissions=[] 在场（区别于未供给的键缺席）。"""
    situation = RuntimeSituation(
        session_id="s1",
        credentials_present={"cred:x": True},
    )
    qual = situation.to_qualification_dict()
    assert qual["granted_permissions"] == []


def test_to_qualification_dict_truncation_flag():
    situation = RuntimeSituation(
        session_id="s1",
        credentials_present={"perm:a": True},
        truncations={"granted_permissions": 9},
    )
    qual = situation.to_qualification_dict()
    assert qual.get("granted_permissions_truncated") is True
