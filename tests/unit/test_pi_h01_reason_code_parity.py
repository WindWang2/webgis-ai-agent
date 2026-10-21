"""H01（K8）：跨层机器码单一事实源一致性守卫。

钉住 ``dispatch_reason_codes`` 的常量与各生产者字面量逐位一致、
对照表覆盖完整 —— 任何一侧词表漂移在此暴露，而不是在生产遥测里
无法归因。
"""
from app.services.chat.pi_input_gate import GATE_ERROR_CODE
from app.services.chat.pi_result_abi import OPERATION_CANCELLED as ABI_CANCELLED
from app.services.chat.pi_surface_receipt import SURFACE_DROP_CODES
from app.services.gis_harness.hotpath_convergence.capability_bind import (
    CAPABILITY_INELIGIBLE_CODE,
    CAPABILITY_PROVIDER_UNAVAILABLE_CODE,
)
from app.services.gis_harness.hotpath_convergence.capability_reasons import (
    REASON_CODES,
)
from app.services.gis_harness.hotpath_convergence.dispatch_reason_codes import (
    ERROR_CLASSES,
    ERROR_CLASS_BY_CODE,
    TURN_DROP_PARITY,
    CAPABILITY_INELIGIBLE,
    CREDENTIALS_REQUIRED,
    OPERATION_CANCELLED,
    PERMISSION_DENIED,
    PROVIDER_UNAVAILABLE,
    SCHEMA_VALIDATION_REJECTED,
    TIER3_CONFIRMATION_REQUIRED,
    UNKNOWN_TOOL,
)


def test_constants_match_producer_literals():
    """常量必须与生产者字面量逐位一致（防两套词表再分叉）。"""
    assert CAPABILITY_INELIGIBLE == CAPABILITY_INELIGIBLE_CODE
    assert PROVIDER_UNAVAILABLE == CAPABILITY_PROVIDER_UNAVAILABLE_CODE
    assert SCHEMA_VALIDATION_REJECTED == GATE_ERROR_CODE
    assert OPERATION_CANCELLED == ABI_CANCELLED
    # registry 侧码（app/tools/registry.py std_error_response 字面量）
    import inspect

    from app.tools import registry as registry_mod

    src = inspect.getsource(registry_mod)
    for literal in (
        f'code="{UNKNOWN_TOOL}"',
        f'code="{TIER3_CONFIRMATION_REQUIRED}"',
        f'code="{CREDENTIALS_REQUIRED}"',
        f'code="{PERMISSION_DENIED}"',
    ):
        assert literal in src, f"registry 字面量漂移: {literal}"


def test_error_class_values_are_closed_vocabulary():
    for cls in ERROR_CLASS_BY_CODE.values():
        assert cls in ERROR_CLASSES


def test_turn_drop_parity_keys_cover_actual_drop_codes():
    """对照表键必须 ⊆ turn 面真实 drop codes；反向：除预算类（compile 期
    工具预算，非资格事实）外，每个事实类 drop code 必须登记对照 —— 新
    drop code 未登记即在此暴露（词表漂移守卫的双向纪律）。"""
    from app.services.chat.pi_surface_receipt import (
        DROP_BYTE_BUDGET,
        DROP_SPEC_OVER_BUDGET,
    )

    assert set(TURN_DROP_PARITY) <= set(SURFACE_DROP_CODES), (
        "TURN_DROP_PARITY 含未在 pi_surface_receipt 登记的 drop code"
    )
    fact_based = set(SURFACE_DROP_CODES) - {
        DROP_SPEC_OVER_BUDGET, DROP_BYTE_BUDGET}
    unregistered = fact_based - set(TURN_DROP_PARITY)
    assert not unregistered, (
        f"事实类 drop code 未登记跨层对照: {sorted(unregistered)}"
    )


def test_turn_drop_parity_values_are_canonical_reason_codes():
    for canonical in TURN_DROP_PARITY.values():
        assert canonical in set(REASON_CODES.values()), (
            f"对照值 {canonical} 不在 capability_reasons canonical 词表"
        )


def test_permission_drop_code_registered():
    """WP3 将新增 permission_not_granted drop code —— 对照表已预留登记。"""
    assert "permission_not_granted" in TURN_DROP_PARITY
