"""R2-P1（qc-loop round 4）回归锁：资格门必须读 producer 的真实比率键。

历史缺陷：``EligibilityContext.from_profile`` 只读 camelCase 键
``uniqueRatio``/``missingRatio``，而唯一的生产 profile 产出口
（``dataset_profile._resolver_fields``）发的是 ``type`` + ``null_ratio``
snake_case —— 字段基数/缺失率门在真实 profile 上恒 unknown 放行，
FIELD_MISSING_RATIO_HIGH 等 V4 资格裁决静默失效。
"""
from app.services.gis_harness.recipes import EligibilityContext


def test_producer_snake_case_null_ratio_is_read():
    ctx = EligibilityContext.from_profile({
        "fields": {"students": {"type": "numeric", "null_ratio": 0.9}},
    })
    assert ctx.fields["students"].missing_ratio == 0.9


def test_camel_case_alias_still_supported():
    ctx = EligibilityContext.from_profile({
        "fields": {"students": {"type": "numeric", "uniqueRatio": 0.5}},
    })
    assert ctx.fields["students"].unique_ratio == 0.5


def test_absent_ratio_stays_unknown():
    ctx = EligibilityContext.from_profile({
        "fields": {"students": {"type": "numeric"}},
    })
    assert ctx.fields["students"].missing_ratio is None
    assert ctx.fields["students"].unique_ratio is None
