"""
Issue Tracking 数据库模块
T1: IssueTracking SQLite 持久化层
支持 Issue 状态跟踪、超时检测、提醒计数等功能
"""
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class IssueStatus(str, Enum):
    """Issue 状态枚举"""
    NEW = "new"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REOPENED = "reopened"


class IssueTrackerDB:
    """
    Issue Tracking SQLite 数据库操作类
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        初始化数据库连接

        Args:
            db_path: 数据库文件路径，默认 ./data/issue_tracking.db
        """
        if db_path:
            self.db_path = db_path
        else:
            # 默认放在项目数据目录
            data_dir = Path("./data")
            data_dir.mkdir(exist_ok=True)
            self.db_path = str(data_dir / "issue_tracking.db")

        self._ensure_table()

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self):
        """确保表存在"""
        sql = """
        CREATE TABLE IF NOT EXISTS issue_tracking (
            issue_number INTEGER PRIMARY KEY,
            status TEXT DEFAULT 'new',
            created_at TIMESTAMP,
            started_at TIMESTAMP,
            resolved_at TIMESTAMP,
            closed_at TIMESTAMP,
            reminder_count INTEGER DEFAULT 0,
            last_reminder_at TIMESTAMP,
            assignee TEXT,
            category TEXT,
            priority TEXT
        )
        """
        with self._get_connection() as conn:
            conn.execute(sql)
            conn.commit()
        logger.debug(f"数据库表已就绪: {self.db_path}")

    def save(
        self,
        issue_number: int,
        status: IssueStatus = IssueStatus.NEW,
        created_at: Optional[datetime] = None,
        started_at: Optional[datetime] = None,
        resolved_at: Optional[datetime] = None,
        closed_at: Optional[datetime] = None,
        reminder_count: int = 0,
        last_reminder_at: Optional[datetime] = None,
        assignee: Optional[str] = None,
        category: Optional[str] = None,
        priority: Optional[str] = None,
    ):
        """
        保存或更新 Issue Tracking 记录

        Args:
            issue_number: Issue 编号
            status: 状态
            created_at: 创建时间
            started_at: 开始处理时间
            resolved_at: 解决时间
            closed_at: 关闭时间
            reminder_count: 已提醒次数
            last_reminder_at: 最后提醒时间
            assignee: 受让人
            category: 分类
            priority: 优先级
        """
        sql = """
        INSERT OR REPLACE INTO issue_tracking (
            issue_number, status, created_at, started_at,
            resolved_at, closed_at, reminder_count, last_reminder_at,
            assignee, category, priority
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            issue_number,
            status.value if isinstance(status, IssueStatus) else status,
            created_at.isoformat() if created_at else None,
            started_at.isoformat() if started_at else None,
            resolved_at.isoformat() if resolved_at else None,
            closed_at.isoformat() if closed_at else None,
            reminder_count,
            last_reminder_at.isoformat() if last_reminder_at else None,
            assignee,
            category,
            priority,
        )

        with self._get_connection() as conn:
            conn.execute(sql, params)
            conn.commit()

        logger.debug(f"Issue #{issue_number} tracking 已保存")

    def get_by_number(self, issue_number: int) -> Optional[dict]:
        """
        根据 issue_number 查询记录

        Args:
            issue_number: Issue 编号

        Returns:
            记录字典，无则返回 None
        """
        sql = "SELECT * FROM issue_tracking WHERE issue_number = ?"
        with self._get_connection() as conn:
            cursor = conn.execute(sql, (issue_number,))
            row = cursor.fetchone()

        if row:
            return dict(row)
        return None

    def get_all_open(self) -> list[dict]:
        """
        获取所有 OPEN 状态的 Issue（NEW + IN_PROGRESS）

        Returns:
            Issue 记录列表
        """
        sql = """
        SELECT * FROM issue_tracking
        WHERE status IN ('new', 'in_progress')
        ORDER BY created_at ASC
        """
        with self._get_connection() as conn:
            cursor = conn.execute(sql)
            rows = cursor.fetchall()

        return [dict(r) for r in rows]

    def get_all_closed(self) -> list[dict]:
        """获取所有 CLOSED/RESOLVED 状态的 Issue"""
        sql = """
        SELECT * FROM issue_tracking
        WHERE status IN ('closed', 'resolved')
        ORDER BY closed_at DESC
        """
        with self._get_connection() as conn:
            cursor = conn.execute(sql)
            rows = cursor.fetchall()

        return [dict(r) for r in rows]

    def find_timeout_issues(
        self,
        timeout_hours: int = 72,
        max_reminders: int = 0,
        statuses: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        查找超时未处理的 Issue

        Args:
            timeout_hours: 超时小时数
            max_reminders: 最大提醒次数过滤
            statuses: 要检查的状态列表，默认 ['new','in_progress']

        Returns:
            超时 Issue 列表
        """
        if statuses is None:
            statuses = ["new", "in_progress"]

        import time
        now_timestamp = datetime.now()
        threshold_seconds = timeout_hours * 3600
        now_str = now_timestamp.isoformat()

        # 这里简化处理，直接从数据库查询符合条件的
        sql = f"""
        SELECT * FROM issue_tracking
        WHERE status IN ({','.join(['?' for _ in statuses])})
        AND reminder_count <= ?
        AND created_at IS NOT NULL
        """
        with self._get_connection() as conn:
            cursor = conn.execute(sql, [*statuses, max_reminders])
            rows = cursor.fetchall()

        results = []
        for row in rows:
            record = dict(row)
            created_str = record.get("created_at")
            if created_str:
                created_dt = datetime.fromisoformat(created_str)
                age_seconds = (now_timestamp - created_dt).total_seconds()
                if age_seconds > threshold_seconds:
                    record["age_hours"] = age_seconds / 3600
                    results.append(record)

        return results

    def increment_reminder(
        self,
        issue_number: int,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """
        增加 Issue 提醒次数

        Args:
            issue_number: Issue 编号
            timestamp: 提醒时间默认 NOW

        Returns:
            是否成功
        """
        if timestamp is None:
            timestamp = datetime.now()

        sql = """
        UPDATE issue_tracking
        SET reminder_count = reminder_count + 1,
            last_reminder_at = ?
        WHERE issue_number = ?
        """

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    sql,
                    (timestamp.isoformat(), issue_number,)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"更新提醒计数失败: {e}")
            return False

    def update_status(
        self,
        issue_number: int,
        new_status: IssueStatus,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """
        更新 Issue 状态，并记录时间戳

        Args:
            issue_number: Issue 编号
            new_status: 新状态
            timestamp: 时间戳，默认 NOW

        Returns:
            是否成功
        """
        if timestamp is None:
            timestamp = datetime.now()

        # 根据状态设置对应的时间戳字段
        ts_field_map = {
            IssueStatus.IN_PROGRESS: "started_at",
            IssueStatus.RESOLVED: "resolved_at",
            IssueStatus.CLOSED: "closed_at",
        }

        ts_field = ts_field_map.get(new_status)

        if ts_field:
            sql = f"""
            UPDATE issue_tracking
            SET status = ?, {ts_field} = ?
            WHERE issue_number = ?
            """
            params = (new_status.value, timestamp.isoformat(), issue_number)
        else:
            sql = "UPDATE issue_tracking SET status = ? WHERE issue_number = ?"
            params = (new_status.value, issue_number)

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(sql, params)
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception(f"更新 Issue #{issue_number} 状态失败: {e}")
            return False

    def delete(self, issue_number: int) -> bool:
        """删除 Issue Tracking 记录"""
        sql = "DELETE FROM issue_tracking WHERE issue_number = ?"
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(sql, (issue_number,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除 Issue #{issue_number} 失败: {e}")
            return False

    def count_by_status(self) -> dict[str, int]:
        """按状态统计 Issue 数量"""
        sql = "SELECT status, COUNT(*) as cnt FROM issue_tracking GROUP BY status"
        with self._get_connection() as conn:
            cursor = conn.execute(sql)
            rows = cursor.fetchall()

        result = {}
        for row in rows:
            result[row["status"]] = row["cnt"]
        return result


# 全局实例（延迟初始化）
_tracker_db: Optional[IssueTrackerDB] = None


def get_tracker(db_path: Optional[str] = None) -> IssueTrackerDB:
    """获取全局 Issue Tracker 实例"""
    global _tracker_db
    if _tracker_db is None:
        _tracker_db = IssueTrackerDB(db_path)
    return _tracker_db


__all__ = [
    "IssueStatus",
    "IssueTrackerDB",
    "get_tracker",
]