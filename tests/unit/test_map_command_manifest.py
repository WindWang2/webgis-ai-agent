"""PR-A：地图命令清单契约（app/contracts/map_commands.py）。

三条不变量：
1. 生成物 ``frontend/lib/map-commands/manifest.generated.ts`` 与清单 byte 级一致；
2. ``app/`` 下每个 ``command`` 字面量发射都在清单里（#535 幽灵命令不变量的
   后端侧镜像 —— 前端 catalogue-contract.test.ts 同口径扫描）；
3. dispatcher 只转发清单内命令：清单外的 ``command`` / ``commands[]`` 项在
   铸 action_id 与 SSE 投影之前被剔除。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.contracts import map_commands
from app.contracts.map_commands import MAP_COMMANDS, is_map_command
from app.services.tool_dispatch_service import ToolDispatchService

def _svc() -> ToolDispatchService:
    # 只用到无状态的命令门禁/铸造方法 —— 不构造完整依赖。
    return ToolDispatchService.__new__(ToolDispatchService)


APP_ROOT = Path(__file__).resolve().parents[2] / "app"

# 与 frontend/lib/map-commands/catalogue-contract.test.ts 的 COMMAND_EMISSION_RE 同口径。
_EMISSION_RE = re.compile(
    r"""(?<![A-Za-z0-9_])["']?command["']?\s*\]?\s*[=:]\s*["']([A-Za-z][A-Za-z0-9_]{0,48})["']"""
)


def test_generated_manifest_has_no_drift():
    verdict = map_commands.check_drift()
    assert not verdict["drifted"], (
        f"manifest.generated.ts 漂移（{verdict['reason']}）—— "
        "请运行 python -m app.contracts.map_commands"
    )


def test_manifest_is_lowercase_sorted_unique():
    assert list(MAP_COMMANDS) == sorted(set(MAP_COMMANDS))
    assert all(n == n.lower() for n in MAP_COMMANDS)


def test_every_backend_command_literal_is_in_manifest():
    emissions: dict[str, list[str]] = {}
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for match in _EMISSION_RE.finditer(path.read_text(encoding="utf-8")):
            emissions.setdefault(match.group(1).lower(), []).append(str(path))
    assert emissions, "扫描应命中已知发射站点"
    ghosts = {k: v[:2] for k, v in emissions.items() if not is_map_command(k)}
    assert ghosts == {}, f"幽灵命令（清单未登记）: {ghosts}"


def test_analysis_workflow_tools_self_identify_with_tool_key():
    src = (APP_ROOT / "tools" / "analysis_workflow_tools.py").read_text(encoding="utf-8")
    assert '"command"' not in src
    assert '"tool": tool_name' in src


def test_is_map_command_case_insensitive():
    assert is_map_command("fly_to")
    assert is_map_command("FLY_TO")
    assert not is_map_command("webgis_requirement_compile")
    assert not is_map_command(None)
    assert not is_map_command(42)


def test_dispatcher_drops_unregistered_single_command():
    result = {"success": True, "command": "webgis_requirement_compile", "x": 1}
    minted = _svc()._mint_map_action_ids(result)
    assert minted == []
    assert "command" not in result
    assert "action_id" not in result
    assert result["x"] == 1


def test_dispatcher_keeps_registered_single_command():
    result = {"command": "fly_to", "params": {"center": [0, 0]}}
    minted = _svc()._mint_map_action_ids(result)
    assert len(minted) == 1 and minted[0]["command"] == "fly_to"
    assert result["command"] == "fly_to" and result["action_id"] == minted[0]["action_id"]


def test_dispatcher_filters_unregistered_batch_items():
    result = {"commands": [
        {"command": "export_map", "params": {}},
        {"command": "not_a_command", "params": {}},
        {"type": "zoom_to_bbox", "params": {}},
        "garbage",
    ]}
    minted = _svc()._mint_map_action_ids(result)
    assert [m["command"] for m in minted] == ["export_map", "zoom_to_bbox"]
    assert [c.get("command") or c.get("type") for c in result["commands"]] == [
        "export_map", "zoom_to_bbox",
    ]
