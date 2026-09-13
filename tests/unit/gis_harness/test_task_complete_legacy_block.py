"""R2-P1（qc-loop round 2）回归锁：legacy map_product 块缺 product_verdict 键
时 _task_complete 不得崩溃。

历史缺陷：块上既无 ``task_complete`` 也无 ``product_verdict`` 时，
``verdict`` 为 None，直接 ``.startswith`` 抛 AttributeError；异常沿
derive_runtime_phase → maybe_update_runtime_state → commit_runtime_context
传播且在各生产调用点被吞 —— V7 状态机对每个 legacy 会话静默冻结。
修复与同文件 _verdict_ready 的 ``str(verdict or "")`` 口径一致。
"""

from app.services.gis_harness.runtime_state_machine import _task_complete


def test_missing_product_verdict_key_is_false_not_crash():
    assert _task_complete({"map_product": {"final_map_status": "verified"}}) is False
    assert _task_complete({"map_product": {}}) is False


def test_dict_and_str_verdict_still_recognized():
    assert _task_complete({"map_product": {
        "product_verdict": {"verdict": "READY"},
        "final_map_status": "verified"}}) is True
    assert _task_complete({"map_product": {
        "product_verdict": "READY",
        "final_map_status": "verified"}}) is True
    assert _task_complete({"map_product": {
        "product_verdict": "DEGRADED",
        "final_map_status": "verified"}}) is False
