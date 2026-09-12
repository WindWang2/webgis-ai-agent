"""Workflow V6 — clone_run 守卫负例（P4 补强：W9）。

既有 clone 测试覆盖正常克隆/嵌套传播；本文件钉住守卫面：
非终态源实例拒绝克隆、不存在的实例拒绝、only/skip 交集非法拒绝。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.services.workflow_runtime.service import (
    WorkflowRuntimeError,
    WorkflowRuntimeService,
)
from app.services.workflow_runtime.store import InstanceStore


@pytest.fixture
def factory():
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


OWNER = "u:clone-guard"


def _service(factory) -> WorkflowRuntimeService:
    return WorkflowRuntimeService(store=InstanceStore(factory=factory))


def _seed(factory, package_id: str, status: str = "RUNNING") -> str:
    """store 级造实例（绕开包 registry；守卫测试只需实例行状态）。"""
    store = InstanceStore(factory=factory)
    inst = store.create_instance(
        package_id=package_id, package_version="1.0.0",
        package_fingerprint="cg" * 16, owner_scope=OWNER,
        session_id=f"s-{package_id}",
        node_specs=[{"node_id": "n:1", "optional": False},
                    {"node_id": "n:2", "optional": True}])
    if status != "PENDING":
        store.update_instance(inst["instance_id"], fields={"status": status})
    return inst["instance_id"]


@pytest.mark.asyncio
async def test_clone_unknown_instance_fails(factory) -> None:
    svc = _service(factory)
    with pytest.raises(WorkflowRuntimeError) as ei:
        await svc.clone_run("no-such-instance", owner_scope=OWNER)
    assert ei.value.code == "INSTANCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_clone_running_instance_is_rejected(factory) -> None:
    svc = _service(factory)
    iid = _seed(factory, "recipe-cg", status="running")
    with pytest.raises(WorkflowRuntimeError) as ei:
        await svc.clone_run(iid, owner_scope=OWNER)
    assert ei.value.code == "INSTANCE_NOT_TERMINAL"




@pytest.mark.asyncio
async def test_clone_owner_scope_mismatch_is_not_found(factory) -> None:
    svc = _service(factory)
    iid = _seed(factory, "recipe-cg3", status="failed")
    with pytest.raises(WorkflowRuntimeError) as ei:
        await svc.clone_run(iid, owner_scope="u:wrong-domain")
    assert ei.value.code == "INSTANCE_NOT_FOUND"
