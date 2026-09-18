"""#1342（audit ISSUE-055）：变差函数拟合静默路径必须留痕。

修复前主拟合/多起点失败是 ``except Exception: pass`` —— 生产故障无痕化。
修复后每处静默兜底至少有一条 ``logger.debug``。
"""
import logging

import numpy as np

from app.lib.geo_analysis import kriging


def test_primary_fit_failure_is_logged(caplog):
    """NaN gamma 让 curve_fit 立即失败 → 必须有 debug 记录，不允许静默。"""
    lags = np.array([1.0, 2.0, 3.0, 4.0])
    gamma = np.array([np.nan, np.nan, np.nan, np.nan])
    weights = np.ones(4)
    with caplog.at_level(logging.DEBUG, logger="app.lib.geo_analysis.kriging"):
        kriging._fit_model("spherical", lags, gamma, weights,
                           var_values=1.0, span=10.0)
    assert any(
        "primary fit failed" in record.message
        for record in caplog.records
    ), [r.message for r in caplog.records]


def test_multi_start_failure_is_logged(caplog, monkeypatch):
    """curve_fit 整体不可用 → 每个备选起点失败也要留痕（落到 grid 兜底前）。"""
    lags = np.array([1.0, 2.0, 3.0, 4.0])
    gamma = np.array([0.1, 0.4, 0.7, 0.9])
    weights = np.ones(4)

    import scipy.optimize

    def boom(*args, **kwargs):
        raise RuntimeError("curve_fit unavailable")

    monkeypatch.setattr(scipy.optimize, "curve_fit", boom)
    with caplog.at_level(logging.DEBUG, logger="app.lib.geo_analysis.kriging"):
        kriging._fit_model("spherical", lags, gamma, weights,
                           var_values=1.0, span=10.0)
    messages = [r.message for r in caplog.records]
    assert any("primary fit failed" in m for m in messages), messages
    assert any("alt-start fit failed" in m for m in messages), messages
