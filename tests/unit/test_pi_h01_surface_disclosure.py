"""H01（K6）：turn 面资格逐出披露。

master 现状：``compute_turn_active_tools`` 的 disclosure（含
``qualification_dropped``）在 ``_active_tools_block_for`` 中只消费
``abstained`` 后即丢弃 —— 模型只看到「工具变少」，无法自纠（缺凭证/权限
时不知道为什么、该做什么）。本文件钉住有界披露（先例：abstained 的
``[工具面提示]``）：

- 有资格逐出时 marker 追加 ``[工具面资格]`` 段（tool:code 列表 + 指引）；
- 有界：最多 6 条、单条字段截断、总段长上界；
- 杀开关 ``PI_SURFACE_DISCLOSURE=0`` → 不追加（master 行为）；
- 无逐出 / 弃权路径不受影响。
"""
import pytest

from app.services.chat.pi_turn_context import _active_tools_block_for


def _fake_compute(drops):
    def compute(
        message, *, active_capabilities=None, workflow_stage="",
        disclosure=None, situation=None, session_id="", turn_id="",
    ):
        if disclosure is not None:
            if drops:
                disclosure["qualification_dropped"] = drops
        return ["webgis_execute", "list_available_tools"]
    return compute


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    monkeypatch.delenv("PI_SURFACE_DISCLOSURE", raising=False)


@pytest.mark.asyncio
async def test_qualification_drops_disclosed_in_marker(monkeypatch):
    monkeypatch.setattr(
        "app.services.chat.pi_native_surface.compute_turn_active_tools",
        _fake_compute([
            {"tool": "poi_query", "code": "credential_absent",
             "detail": "credentials absent: stats_api"},
            {"tool": "admin_tool", "code": "permission_not_granted",
             "detail": "permission not granted: admin"},
        ]),
    )
    marker = await _active_tools_block_for("成都小学分布", None, None)
    assert "[工具面资格]" in marker
    assert "poi_query:credential_absent" in marker
    assert "admin_tool:permission_not_granted" in marker
    # 指引存在（模型可自纠：补凭证/权限或走代理通道）
    assert "webgis_execute" in marker


@pytest.mark.asyncio
async def test_disclosure_bounded(monkeypatch):
    many = [
        {"tool": f"tool_{i}", "code": "credential_absent", "detail": "d"}
        for i in range(30)
    ]
    monkeypatch.setattr(
        "app.services.chat.pi_native_surface.compute_turn_active_tools",
        _fake_compute(many),
    )
    marker = await _active_tools_block_for("q", None, None)
    tail = marker.split("[工具面资格]", 1)[1]
    assert "tool_5:" in tail
    assert "tool_6:" not in tail, "最多披露 6 条"
    assert len(tail) <= 900, "披露段有字符上界"


@pytest.mark.asyncio
async def test_disclosure_kill_switch(monkeypatch):
    import os

    os.environ["PI_SURFACE_DISCLOSURE"] = "0"
    try:
        monkeypatch.setattr(
            "app.services.chat.pi_native_surface.compute_turn_active_tools",
            _fake_compute([
                {"tool": "poi_query", "code": "credential_absent", "detail": ""},
            ]),
        )
        marker = await _active_tools_block_for("q", None, None)
        assert "[工具面资格]" not in marker
    finally:
        os.environ.pop("PI_SURFACE_DISCLOSURE", None)


@pytest.mark.asyncio
async def test_no_drops_no_disclosure_section(monkeypatch):
    monkeypatch.setattr(
        "app.services.chat.pi_native_surface.compute_turn_active_tools",
        _fake_compute([]),
    )
    marker = await _active_tools_block_for("q", None, None)
    assert "[工具面资格]" not in marker
    assert marker.startswith("[")  # 仍是有效 marker


@pytest.mark.asyncio
async def test_malformed_drop_entries_do_not_break_marker(monkeypatch):
    monkeypatch.setattr(
        "app.services.chat.pi_native_surface.compute_turn_active_tools",
        _fake_compute([
            {"code": "credential_absent"},             # 无 tool
            {"tool": "t2"},                            # 无 code
            "not-a-dict",                              # 形态错误
            {"tool": "t3", "code": "offline_network_tool", "detail": "x"},
        ]),
    )
    marker = await _active_tools_block_for("q", None, None)
    assert "[工具面资格]" in marker
    assert "t3:offline_network_tool" in marker
