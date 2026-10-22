"""G03 typed fallback 码表交叉引用 parity 测试（WP-C.9 / DECISIONS D3）。

FALLBACK_CODE_XWALK 把回退语义钉到既有权威码；本测试保证每个目标码
在它的权威层词表里真实存在——词表演进时目标码被移除即红，杜绝「映射
指向不存在的码」的静默漂移。
"""
from app.lib.gis.tool_capability_conformance import FALLBACK_CODE_XWALK


def _surface_drop_codes() -> set:
    from app.services.chat.pi_surface_receipt import SURFACE_DROP_CODES

    return set(SURFACE_DROP_CODES)


def _gate_codes() -> set:
    from app.services.gis_harness.hotpath_convergence.dispatch_reason_codes import (
        ERROR_CLASS_BY_CODE,
    )

    return set(ERROR_CLASS_BY_CODE)


def _capability_reason_codes() -> set:
    from app.services.gis_harness.hotpath_convergence.capability_reasons import (
        REASON_CODES,
    )

    return set(REASON_CODES.values())


def test_xwalk_is_frozen_and_unique():
    triples = [(sem, layer, code) for sem, layer, code in FALLBACK_CODE_XWALK]
    assert len(triples) == len(set(triples)), "同一 (语义, 层, 码) 不允许重复登记"
    semantics = {sem for sem, _, _ in FALLBACK_CODE_XWALK}
    # WP-C.9 点名的五个回退语义必须全部有既有码可复用
    assert {"credential_absent", "offline", "unsupported_method",
            "stale_surface", "data_requirement_missing"} <= semantics


def test_every_target_code_exists_in_its_authority():
    drop = _surface_drop_codes()
    gates = _gate_codes()
    reasons = _capability_reason_codes()
    for _sem, layer, code in FALLBACK_CODE_XWALK:
        if layer == "pi_surface_receipt":
            assert code in drop, f"{code} not in SURFACE_DROP_CODES"
        elif layer == "registry_dispatch":
            assert code in gates, f"{code} not in dispatch_reason_codes"
        elif layer == "capability_reasons":
            assert code in reasons, f"{code} not in capability REASON_CODES"
        elif layer == "pi_input_gate":
            from app.services.chat.pi_input_gate import GATE_ERROR_CODE

            assert code == GATE_ERROR_CODE
        elif layer == "pi_bridge_surface_gate":
            # 两个码都是源码字面量（无模块级常量）——与
            # test_pi_h01_reason_code_parity 同款 inspect.getsource 钉法；
            # SURFACE_FINGERPRINT_MISMATCH 的生产者在 pi_surface_receipt
            # 的 verify_callback_fingerprints 返回值。
            import inspect

            from app.services.chat import pi_bridge_surface_gate, pi_surface_receipt

            if code == "SURFACE_FINGERPRINT_MISMATCH":
                src = inspect.getsource(pi_surface_receipt)
            else:
                src = inspect.getsource(pi_bridge_surface_gate)
            assert f'"{code}"' in src, f"{code!r} literal absent from authority source"
        elif layer == "parameter_contracts":
            from app.lib.gis.parameter_contracts import apply_contract

            # 前缀码：apply_contract 的 ValueError 机器可读前缀
            try:
                apply_contract("__nonexistent__", {})
            except ValueError as exc:
                assert "unknown parameter contract" in str(exc)
            else:  # pragma: no cover — 未注册契约必须抛错
                raise AssertionError("apply_contract must reject unknown id")
            assert code == "parameter_contract_violation"
        else:  # pragma: no cover — 词表封闭
            raise AssertionError(f"unknown authority layer {layer!r}")


def test_no_invented_dispatch_codes():
    """映射只复用既有码——registry_dispatch 层的目标码必须全部来自
    dispatch_reason_codes 权威表，不允许本模块私造机器码。"""
    gates = _gate_codes()
    for _sem, layer, code in FALLBACK_CODE_XWALK:
        if layer == "registry_dispatch":
            assert code in gates
