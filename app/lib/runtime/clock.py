"""统一 aware-UTC 时钟（H04 turn journal 的时序基座，issue #1551 口径收敛起点）。

仓库现状三种时序形态并存（recon 实测）：
- envelope/kernel：epoch float（``harness_kernel.runtime._now``）；
- DB 行：naive-UTC datetime（``datetime.now(timezone.utc).replace(tzinfo=None)``，
  workflow store / spatial ledger 的既定边界约定）;
- provenance/envelope JSON：aware ISO 字符串。

本模块是**新代码**的唯一时钟入口：进程内一律 aware（``utc_now`` /
``epoch_to_utc``），DB 边界显式降为 naive-UTC（``to_db_utc``），读取边界把
历史 naive 数据按 UTC 兼容抬回 aware（``from_db_utc``）——naive 即 UTC 是
全库既有写入口径（没有任何本地时区写入点），该假设安全且只读。

规则：新模块禁止裸 ``datetime.utcnow()`` / 无参 ``datetime.now()``；
``tests/unit/turn_journal/test_clock_discipline.py`` 对本方向模块做门禁。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union

__all__ = [
    "utc_now",
    "epoch_to_utc",
    "to_db_utc",
    "from_db_utc",
    "iso_utc",
]


def utc_now() -> datetime:
    """当前时刻（aware UTC）。进程内新代码唯一"现在"入口。"""
    return datetime.now(timezone.utc)


def epoch_to_utc(epoch_s: float) -> datetime:
    """epoch 秒（envelope ``_now()`` 形态）→ aware UTC。"""
    return datetime.fromtimestamp(float(epoch_s), tz=timezone.utc)


def to_db_utc(value: datetime) -> datetime:
    """写入 DB 前的边界转换：aware → naive-UTC（既有列口径）。

    naive 输入原样返回（视为已是 UTC——与全库既有写入行为一致）。
    """
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def from_db_utc(value: Optional[Union[datetime, str]]) -> Optional[datetime]:
    """读取边界：DB/JSON 时间 → aware UTC。

    naive 值按 UTC 抬升（兼容历史 naive 行）；aware 值归一化为 UTC 时区；
    字符串走 ISO 解析（无 offset 的旧串按 UTC）。非法输入返回 None 而非
    抛出——诊断/查询面不允许因脏数据翻车。
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        value = parsed
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_utc(value: Optional[Union[datetime, str]]) -> Optional[str]:
    """aware UTC ISO 字符串（API/诊断输出的唯一时间形态）；非法输入 None。"""
    aware = from_db_utc(value)
    return aware.isoformat() if aware is not None else None
