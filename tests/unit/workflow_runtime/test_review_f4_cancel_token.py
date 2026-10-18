"""Review F4: Driver._make_cancel_token imported a nonexistent module and was
always None, so geocompute worker threads were never cooperatively cancelled."""
from app.lib.cancellation import CancellationToken
from app.services.workflow_runtime.driver import Driver


def test_make_cancel_token_returns_real_token():
    drv = Driver.__new__(Driver)
    tok = drv._make_cancel_token()
    assert isinstance(tok, CancellationToken)
    # Executor contract: .cancelled / .reason observed between steps.
    assert tok.cancelled is False
    tok.cancel("deadline")
    assert tok.cancelled is True and tok.reason == "deadline"
