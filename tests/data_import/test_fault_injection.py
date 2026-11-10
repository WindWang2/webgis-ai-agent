"""G17 故障注入反验（任务书 §实现细节）：证明测试覆盖真实产品接线。

三条注入（全部在 fixture 内完成，不动生产代码路径以外的状态）：
1. 移除 layer 参数（container_ref 置空）→ 多层 GPKG 成员必须失败，而不是
   静默回落 GDAL 默认第一层。
2. preview 与 commit 间篡改解释（模拟外发陈旧/不一致 hash）→ 计划一致性
   防线拒绝。
3. 伪造未登记 output_ref → 幂等复用校验失败 → 重新真实提交（不信任假 ref）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_geojson_bytes, make_three_layer_gpkg, point_feature  # noqa: E402

from app.core.config import settings
from app.services.data_import import staging
from app.services.data_import.service import DataImportService
from app.services.session_data import session_data_manager

pytestmark = pytest.mark.heavy

SESSION = "sess-g17-fault-0001"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    return DataImportService()


async def test_fault_no_layer_param_fails_not_first_layer(env, tmp_path):
    """注入：container_ref 置空（等价于丢失 layer 参数）→ 必须显式失败。"""
    gpkg = tmp_path / "l.gpkg"
    make_three_layer_gpkg(gpkg)
    state = await env.create_batch(SESSION, "u", [("l.gpkg", gpkg.read_bytes())])
    line_member = next(m for m in state.members if m.display_name == "line_layer")
    # 故障注入：抹掉可复现定位
    line_member.container_ref = None
    staging.save_state(state)
    await env.save_plan(state.batch_id, SESSION, {line_member.member_key: {}},
                        selected=[line_member.member_key])
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][line_member.member_key]
    # 防线：CONTAINER_REF_MISSING / 读取失败 —— 绝不静默读第一层
    assert r["status"] == "failed"
    assert r["error_code"] in ("CONTAINER_REF_MISSING", "VECTOR_READ_FAILED")
    # 且没有产出任何 ref（第一层 pt_layer 未被误导入）
    assert not r.get("output_ref")


async def test_fault_diverted_mapping_between_preview_and_commit(env, tmp_path):
    """注入：preview 后直接篡改状态里的解释（不经 save_plan 版本递增）→
    计划一致性防线以当前事实源 hash 为准；陈旧 hash 提交被拒。"""
    geo = make_geojson_bytes([point_feature({"n": 1}, 116.3, 39.8)])
    state = await env.create_batch(SESSION, "u", [("p.geojson", geo)])
    key = state.members[0].member_key
    await env.save_plan(state.batch_id, SESSION, {key: {}}, selected=[key])
    await env.preview_members(state.batch_id, SESSION, [key], row_limit=10)

    # 篡改：直接改写状态解释（模拟绕过版本链的写入）
    tampered = staging.load_state(state.batch_id)
    tampered.interpretations[key] = {"kind": "vector", "keep_fields": ["absent_field"]}
    staging.save_state(tampered)

    # 篡改不经过 save_plan（版本号不变），版本闸看不到 —— 但 hash 推导防线
    # 是权威：提交按（源 sha, 成员, 当前解释）重算并比对，状态损坏被拒。
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][key]
    assert r["status"] == "failed" and r["error_code"] == "PLAN_HASH_MISMATCH"
    assert out["status"] == "failed"

    # 正确路径：save_plan 重新校验字段（absent_field 不存在 → COLUMN_NOT_FOUND）
    with pytest.raises(Exception) as ei2:
        await env.save_plan(state.batch_id, SESSION,
                            {key: {"keep_fields": ["absent_field"]}})
    assert ei2.value.code == "COLUMN_NOT_FOUND"


async def test_fault_forged_ref_not_reused(env, tmp_path):
    """注入：伪造未登记 output_ref 到已完成收据 → 幂等复用校验失败，
    重新真实提交（假 ref 不得显示为就绪）。"""
    geo = make_geojson_bytes([point_feature({"n": 7}, 116.31, 39.81)])
    state = await env.create_batch(SESSION, "u", [("f.geojson", geo)])
    key = state.members[0].member_key
    await env.save_plan(state.batch_id, SESSION, {key: {}}, selected=[key])
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    real_ref = out["receipts"][key]["output_ref"]

    # 伪造：改写收据指向一个不存在的 ref
    tampered = staging.load_state(state.batch_id)
    forged = "ref:upload-forged000000000"
    tampered.receipts[key].output_ref = forged
    staging.save_state(tampered)

    out2 = await env.start_commit(state.batch_id, SESSION, background=False)
    r2 = out2["receipts"][key]
    # 假 ref 不可读 → 幂等校验失败 → 重新提交得到真实 ref
    assert r2["status"] == "completed"
    assert r2["output_ref"] != forged
    assert (await session_data_manager.get(SESSION, r2["output_ref"])) is not None
