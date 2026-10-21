"""Explorer — orchestrator 所有权校验（P4 补强 E3 相邻面：#526 owner 边界）。"""
from __future__ import annotations

import pytest

from app.services.explorer import orchestrator as OC


@pytest.fixture()
def _clean_registry(monkeypatch):
    # 进程内注册表隔离：清空 LRU 并屏蔽 durable 落盘（Redis 缺席降级）。
    monkeypatch.setattr(OC, "_chain_runs", OC.OrderedDict(), raising=False)
    monkeypatch.setattr(OC, "persist_chain_run_sync",
                        lambda *a, **k: None, raising=False)
    yield


def test_register_and_collect_roundtrip(_clean_registry) -> None:
    OC.register_chain_run("chain-1", ["a", "b", "c"], owner="u-1")
    run = OC.get_chain_run("chain-1")
    assert run is not None
    assert list(run.stage_ids) == ["a", "b", "c"]
    assert run.owner == "u-1"


def test_unknown_chain_run_is_none(_clean_registry) -> None:
    assert OC.get_chain_run("nope") is None


def test_chain_run_verification_owner_match(_clean_registry) -> None:
    OC.register_chain_run("chain-2", ["x"], owner="u-2")
    run = OC.get_chain_run("chain-2")
    assert run.owner == "u-2"
    assert run.owner != "u-3"
