"""TaskTracker 单元测试"""
import pytest
import time
from datetime import datetime, timezone


class TestTaskTracker:
    """TaskTracker 核心方法测试"""

    def test_create_task(self):
        """测试创建任务"""
        from app.services.task_tracker import TaskTracker

        tracker = TaskTracker()
        task = tracker.create("session-1", "成都市大学分布热力图")

        assert task is not None
        assert task.session_id == "session-1"
        assert task.original_request == "成都市大学分布热力图"
        assert task.status.value == "running"
        assert len(task.steps) == 0
        assert task.id.startswith("task-")

    def test_start_step(self):
        """测试启动步骤"""
        from app.services.task_tracker import TaskTracker

        tracker = TaskTracker()
        task = tracker.create("session-1", "测试请求")

        step = tracker.start_step(task.id, "query_osm_poi", {"city": "成都", "type": "university"})

        assert step is not None
        assert step.id == "step-1"
        assert step.tool == "query_osm_poi"
        assert step.params == {"city": "成都", "type": "university"}
        assert step.status.value == "running"

    def test_complete_step(self):
        """测试完成步骤"""
        from app.services.task_tracker import TaskTracker, StepStatus

        tracker = TaskTracker()
        task = tracker.create("session-1", "测试请求")
        step = tracker.start_step(task.id, "query_osm_poi", {})

        result = {"type": "FeatureCollection", "features": []}
        tracker.complete_step(task.id, step.id, result)

        assert task.steps[0].status == StepStatus.completed
        assert task.steps[0].result == result
        assert task.steps[0].finished_at is not None

    def test_fail_step(self):
        """测试步骤失败"""
        from app.services.task_tracker import TaskTracker, StepStatus

        tracker = TaskTracker()
        task = tracker.create("session-1", "测试请求")
        step = tracker.start_step(task.id, "query_osm_poi", {})

        error_msg = "Network timeout"
        tracker.fail_step(task.id, step.id, error_msg)

        assert task.steps[0].status == StepStatus.failed
        assert task.steps[0].error == error_msg
        assert task.steps[0].finished_at is not None

    def test_complete_task(self):
        """测试完成任务"""
        from app.services.task_tracker import TaskTracker, TaskStatus

        tracker = TaskTracker()
        task = tracker.create("session-1", "测试请求")

        tracker.complete_task(task.id)

        assert task.status == TaskStatus.completed
        assert task.finished_at is not None

    def test_fail_task(self):
        """测试任务失败"""
        from app.services.task_tracker import TaskTracker, TaskStatus

        tracker = TaskTracker()
        task = tracker.create("session-1", "测试请求")

        error_msg = "Max rounds reached"
        tracker.fail_task(task.id, error_msg)

        assert task.status == TaskStatus.failed
        assert task.finished_at is not None

    def test_cancel_task(self):
        """测试取消任务"""
        from app.services.task_tracker import TaskTracker, TaskStatus

        tracker = TaskTracker()
        task = tracker.create("session-1", "测试请求")

        result = tracker.cancel(task.id)

        assert result is True
        assert task.status == TaskStatus.cancelled

    def test_cancel_nonexistent_task(self):
        """测试取消不存在的任务"""
        from app.services.task_tracker import TaskTracker

        tracker = TaskTracker()

        result = tracker.cancel("task-nonexistent")

        assert result is False

    def test_get_task(self):
        """测试获取任务"""
        from app.services.task_tracker import TaskTracker

        tracker = TaskTracker()
        task = tracker.create("session-1", "测试请求")

        retrieved = tracker.get(task.id)

        assert retrieved is not None
        assert retrieved.id == task.id

    def test_get_nonexistent_task(self):
        """测试获取不存在的任务"""
        from app.services.task_tracker import TaskTracker

        tracker = TaskTracker()

        result = tracker.get("task-nonexistent")

        assert result is None

    def test_list_by_session(self):
        """测试按会话列出任务"""
        from app.services.task_tracker import TaskTracker

        tracker = TaskTracker()

        task1 = tracker.create("session-1", "请求1")
        task2 = tracker.create("session-1", "请求2")
        task3 = tracker.create("session-2", "请求3")

        tasks = tracker.list_by_session("session-1")

        assert len(tasks) == 2
        assert task1 in tasks
        assert task2 in tasks

    def test_multiple_steps_auto_increment(self):
        """测试多步骤自动编号"""
        from app.services.task_tracker import TaskTracker

        tracker = TaskTracker()
        task = tracker.create("session-1", "测试请求")

        step1 = tracker.start_step(task.id, "tool1", {})
        step2 = tracker.start_step(task.id, "tool2", {})
        step3 = tracker.start_step(task.id, "tool3", {})

        assert step1.id == "step-1"
        assert step2.id == "step-2"
        assert step3.id == "step-3"

    def test_is_cancelled(self):
        """测试取消状态检查"""
        from app.services.task_tracker import TaskTracker

        tracker = TaskTracker()
        task = tracker.create("session-1", "测试请求")

        assert tracker.is_cancelled(task.id) is False

        tracker.cancel(task.id)

        assert tracker.is_cancelled(task.id) is True


class TestTaskModels:
    """Task 模型测试"""

    def test_step_status_enum(self):
        """测试 StepStatus 枚举"""
        from app.services.task_tracker import StepStatus

        assert StepStatus.running.value == "running"
        assert StepStatus.completed.value == "completed"
        assert StepStatus.failed.value == "failed"

    def test_task_status_enum(self):
        """测试 TaskStatus 枚举"""
        from app.services.task_tracker import TaskStatus

        assert TaskStatus.running.value == "running"
        assert TaskStatus.completed.value == "completed"
        assert TaskStatus.failed.value == "failed"
        assert TaskStatus.cancelled.value == "cancelled"


class TestDetectGeoJSON:
    """detect_geojson 函数测试"""

    def test_detect_geojson_feature_collection(self):
        """测试检测直接 FeatureCollection"""
        from app.services.task_tracker import detect_geojson

        result = {"type": "FeatureCollection", "features": []}
        assert detect_geojson(result) is True

    def test_detect_geojson_nested(self):
        """测试检测嵌套在 data 字段中"""
        from app.services.task_tracker import detect_geojson

        result = {"data": {"type": "FeatureCollection", "features": [{"type": "Feature"}]}}
        assert detect_geojson(result) is True

    def test_detect_geojson_no_match(self):
        """测试无 GeoJSON 时返回 False"""
        from app.services.task_tracker import detect_geojson

        result = {"type": "Feature", "geometry": {}}
        assert detect_geojson(result) is False

    def test_detect_geojson_non_dict(self):
        """测试非 dict 输入返回 False"""
        from app.services.task_tracker import detect_geojson

        # list 输入
        assert detect_geojson([{"type": "FeatureCollection"}]) is False
        # string 输入
        assert detect_geojson("FeatureCollection") is False
        # None 输入
        assert detect_geojson(None) is False