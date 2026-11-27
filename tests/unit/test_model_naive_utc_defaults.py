"""回归：朴素 DateTime 列的默认值必须写入 naive UTC。

P3-app_models-00：``cartography_quality`` 返回 naive UTC，而另外 7 个模型
模块返回 tz-aware UTC，却同样写入无 ``timezone=True`` 的 DateTime 列。
PostgreSQL 会话时区非 UTC 时，aware 值按会话时区强转而 naive 值不转，
同一时刻会落成两种墙钟值（TTL / 跨模块比较漂移）。本测试锁定 8 个模块
的统一口径：naive UTC。
"""
from datetime import datetime, timezone

import pytest

from app.models import (
    cartographic_symbol_asset,
    cartography_quality,
    composition_template_asset,
    coordinate_operations,
    data_fabric,
    data_join_authoring,
    data_lifecycle,
    data_quality,
)

#: 8 个模块：cartography_quality 是既有正确先例，其余 7 处为本次修复面。
_NAIVE_UTC_MODULES = (
    cartography_quality,
    cartographic_symbol_asset,
    composition_template_asset,
    coordinate_operations,
    data_join_authoring,
    data_lifecycle,
    data_quality,
)

#: data_fabric 用内联 lambda 作为列默认（无模块级 _utcnow）。
_DATA_FABRIC_DEFAULT_COLUMNS = (
    ("DataSource", "created_at"),
    ("DataSource", "updated_at"),
    ("DataFabricDataset", "created_at"),
    ("DataFabricDataset", "updated_at"),
    ("DataMaterializationRecord", "materialized_at"),
    ("DatasetStatisticsRecord", "collected_at"),
    ("SourceFactsRecordModel", "collected_at"),
    ("FederatedFeedbackModel", "created_at"),
)


@pytest.mark.parametrize("module", _NAIVE_UTC_MODULES, ids=lambda m: m.__name__)
def test_model_utcnow_default_is_naive_utc(module):
    value = module._utcnow()
    assert value.tzinfo is None, f"{module.__name__}._utcnow() 必须返回 naive UTC"
    drift = abs(
        (datetime.now(timezone.utc).replace(tzinfo=None) - value).total_seconds()
    )
    assert drift < 5, f"{module.__name__}._utcnow() 的墙钟不是 UTC（偏差 {drift:.0f}s）"


@pytest.mark.parametrize(
    "model_name,column_name",
    _DATA_FABRIC_DEFAULT_COLUMNS,
    ids=[f"{m}.{c}" for m, c in _DATA_FABRIC_DEFAULT_COLUMNS],
)
def test_data_fabric_datetime_defaults_are_naive_utc(model_name, column_name):
    column = getattr(data_fabric, model_name).__table__.c[column_name]
    # SQLAlchemy 把零参可调用包装成 (ctx) 签名的 ColumnDefault。
    value = column.default.arg(None)
    assert value.tzinfo is None, (
        f"data_fabric.{model_name}.{column_name} 的默认值必须写入 naive UTC"
    )
