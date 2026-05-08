"""Tests for geocode enhancement in explorer task chain"""
import pytest
from unittest.mock import patch, AsyncMock

from app.tasks.explorer.task_chain import explorer_geocode_task


def test_geocode_task_calls_batch_geocode_cn():
    """Verify task calls batch_geocode_cn, maps results back, returns correct success_rate"""
    prev_result = {
        "task_id": "test_task",
        "parsed_results": [
            {
                "ref_id": "ref_1",
                "row_count": 2,
                "mapping": {"address": "addr"},
            }
        ],
    }

    rows = [
        {"name": "A", "addr": "北京"},
        {"name": "B", "addr": "上海"},
    ]

    with patch("app.tasks.explorer.task_chain._load_ref") as mock_load, \
         patch("app.tasks.explorer.task_chain._store_ref") as mock_store, \
         patch("app.tools.chinese_maps.batch_geocode_cn", new_callable=AsyncMock) as mock_batch:

        mock_load.return_value = {
            "rows": rows,
            "mapping": {"address": "addr"},
        }
        mock_store.return_value = "geocoded_ref_123"

        mock_batch.return_value = {
            "total": 2,
            "success_count": 2,
            "error_count": 0,
            "results": [
                {"index": 0, "status": "ok", "address": "北京", "results": [{"location": [116.4, 39.9]}]},
                {"index": 1, "status": "ok", "address": "上海", "results": [{"location": [121.5, 31.2]}]},
            ],
            "errors": [],
            "provider": "amap",
        }

        result = explorer_geocode_task.run(prev_result)

        assert result["task_id"] == "test_task"
        assert result["geocoded_ref_id"] == "geocoded_ref_123"
        assert result["total_rows"] == 2
        assert result["success_rate"] == 1.0

        mock_batch.assert_called_once()
        call_kwargs = mock_batch.call_args.kwargs
        assert call_kwargs.get("provider") == "amap"

        stored_data = mock_store.call_args[0][0]
        assert stored_data["summary"]["total"] == 2
        assert stored_data["summary"]["success"] == 2
        assert stored_data["summary"]["failed"] == 0
        assert stored_data["summary"]["success_rate"] == 1.0
        assert stored_data["summary"]["multi_provider"] is False

        for row in stored_data["rows"]:
            assert row["_geocode_status"] == "ok"
            assert row["_geocode_provider"] == "amap"
            assert row["_geocode_error"] is None

        assert stored_data["rows"][0]["_lat"] == 39.9
        assert stored_data["rows"][0]["_lon"] == 116.4
        assert stored_data["rows"][1]["_lat"] == 31.2
        assert stored_data["rows"][1]["_lon"] == 121.5


def test_geocode_task_multi_provider_fallback():
    """Mock first call with 50% failure, second call retries, verifies 75% final success rate"""
    prev_result = {
        "task_id": "test_task",
        "parsed_results": [
            {
                "ref_id": "ref_1",
                "row_count": 4,
                "mapping": {"address": "addr"},
            }
        ],
    }

    rows = [
        {"name": "A", "addr": "北京"},
        {"name": "B", "addr": "上海"},
        {"name": "C", "addr": "广州"},
        {"name": "D", "addr": "深圳"},
    ]

    async def mock_batch(addresses, provider="amap", max_concurrency=3):
        if provider == "amap":
            return {
                "total": len(addresses),
                "success_count": 2,
                "error_count": 2,
                "results": [
                    {"index": 0, "status": "ok", "address": addresses[0], "results": [{"location": [116.4, 39.9]}]},
                    {"index": 1, "status": "ok", "address": addresses[1], "results": [{"location": [121.5, 31.2]}]},
                ],
                "errors": [
                    {"index": 2, "status": "error", "address": addresses[2], "error": "not found"},
                    {"index": 3, "status": "error", "address": addresses[3], "error": "not found"},
                ],
                "provider": "amap",
            }
        elif provider == "baidu":
            return {
                "total": len(addresses),
                "success_count": 1,
                "error_count": 1,
                "results": [
                    {"index": 0, "status": "ok", "address": addresses[0], "results": [{"location": [113.3, 23.1]}]},
                ],
                "errors": [
                    {"index": 1, "status": "error", "address": addresses[1], "error": "not found"},
                ],
                "provider": "baidu",
            }
        else:
            return {
                "total": len(addresses),
                "success_count": 0,
                "error_count": len(addresses),
                "results": [],
                "errors": [{"index": i, "status": "error", "address": a, "error": "failed"} for i, a in enumerate(addresses)],
                "provider": provider,
            }

    with patch("app.tasks.explorer.task_chain._load_ref") as mock_load, \
         patch("app.tasks.explorer.task_chain._store_ref") as mock_store, \
         patch("app.tools.chinese_maps.batch_geocode_cn", side_effect=mock_batch):

        mock_load.return_value = {
            "rows": rows,
            "mapping": {"address": "addr"},
        }
        mock_store.return_value = "geocoded_ref_456"

        result = explorer_geocode_task.run(prev_result)

        assert result["task_id"] == "test_task"
        assert result["total_rows"] == 4
        assert result["success_rate"] == 0.75

        stored_data = mock_store.call_args[0][0]
        assert stored_data["summary"]["success"] == 3
        assert stored_data["summary"]["failed"] == 1
        assert stored_data["summary"]["multi_provider"] is True

        # Verify which rows succeeded/failed
        statuses = [r["_geocode_status"] for r in stored_data["rows"]]
        assert statuses.count("ok") == 3
        assert statuses.count("failed") == 1


def test_geocode_task_empty_data():
    """Empty parsed_results returns success_rate 0.0"""
    prev_result = {
        "task_id": "test_task",
        "parsed_results": [],
    }

    with patch("app.tasks.explorer.task_chain._load_ref") as mock_load, \
         patch("app.tasks.explorer.task_chain._store_ref") as mock_store:

        result = explorer_geocode_task.run(prev_result)

        assert result["task_id"] == "test_task"
        assert result["geocoded_ref_id"] is None
        assert result["success_rate"] == 0.0
        mock_load.assert_not_called()
        mock_store.assert_not_called()


def test_geocode_task_predefined_coordinates():
    """Rows with existing lat/lon should be marked predefined and skipped from geocoding"""
    prev_result = {
        "task_id": "test_task",
        "parsed_results": [
            {
                "ref_id": "ref_1",
                "row_count": 3,
                "mapping": {"address": "addr", "lat": "latitude", "lon": "longitude"},
            }
        ],
    }

    rows = [
        {"name": "A", "addr": "北京", "latitude": 39.9, "longitude": 116.4},
        {"name": "B", "addr": "上海", "latitude": None, "longitude": None},
        {"name": "C", "addr": "广州"},
    ]

    with patch("app.tasks.explorer.task_chain._load_ref") as mock_load, \
         patch("app.tasks.explorer.task_chain._store_ref") as mock_store, \
         patch("app.tools.chinese_maps.batch_geocode_cn", new_callable=AsyncMock) as mock_batch:

        mock_load.return_value = {
            "rows": rows,
            "mapping": {"address": "addr", "lat": "latitude", "lon": "longitude"},
        }
        mock_store.return_value = "geocoded_ref_789"

        mock_batch.return_value = {
            "total": 2,
            "success_count": 2,
            "error_count": 0,
            "results": [
                {"index": 0, "status": "ok", "address": "上海", "results": [{"location": [121.5, 31.2]}]},
                {"index": 1, "status": "ok", "address": "广州", "results": [{"location": [113.3, 23.1]}]},
            ],
            "errors": [],
            "provider": "amap",
        }

        result = explorer_geocode_task.run(prev_result)

        assert result["task_id"] == "test_task"
        assert result["total_rows"] == 3
        assert result["success_rate"] == 1.0

        stored_data = mock_store.call_args[0][0]
        assert stored_data["summary"]["total"] == 3
        assert stored_data["summary"]["success"] == 2
        assert stored_data["summary"]["failed"] == 0
        assert stored_data["summary"]["predefined"] == 1

        # Verify row statuses
        row_a = stored_data["rows"][0]
        assert row_a["_geocode_status"] == "predefined"
        assert row_a["_lat"] == 39.9
        assert row_a["_lon"] == 116.4
        assert row_a["_geocode_provider"] is None

        row_b = stored_data["rows"][1]
        assert row_b["_geocode_status"] == "ok"
        assert row_b["_lat"] == 31.2
        assert row_b["_lon"] == 121.5
        assert row_b["_geocode_provider"] == "amap"

        row_c = stored_data["rows"][2]
        assert row_c["_geocode_status"] == "ok"
        assert row_c["_lat"] == 23.1
        assert row_c["_lon"] == 113.3
        assert row_c["_geocode_provider"] == "amap"

        # batch_geocode_cn should only be called once with 2 addresses (skips predefined)
        mock_batch.assert_called_once()
        call_args = mock_batch.call_args[0]
        assert len(call_args[0]) == 2
        assert call_args[0][0] == "上海"
        assert call_args[0][1] == "广州"


def test_geocode_task_all_providers_failed():
    """When all providers return errors, all rows should be marked failed with all_providers_failed"""
    prev_result = {
        "task_id": "test_task",
        "parsed_results": [
            {
                "ref_id": "ref_1",
                "row_count": 2,
                "mapping": {"address": "addr"},
            }
        ],
    }

    rows = [
        {"name": "A", "addr": "北京"},
        {"name": "B", "addr": "上海"},
    ]

    async def mock_batch(addresses, provider="amap", max_concurrency=3):
        return {
            "total": len(addresses),
            "success_count": 0,
            "error_count": len(addresses),
            "results": [],
            "errors": [
                {"index": i, "status": "error", "address": a, "error": "service unavailable"}
                for i, a in enumerate(addresses)
            ],
            "provider": provider,
        }

    mock_batch_obj = AsyncMock(side_effect=mock_batch)

    with patch("app.tasks.explorer.task_chain._load_ref") as mock_load, \
         patch("app.tasks.explorer.task_chain._store_ref") as mock_store, \
         patch("app.tools.chinese_maps.batch_geocode_cn", mock_batch_obj):

        mock_load.return_value = {
            "rows": rows,
            "mapping": {"address": "addr"},
        }
        mock_store.return_value = "geocoded_ref_abc"

        result = explorer_geocode_task.run(prev_result)

        assert result["task_id"] == "test_task"
        assert result["total_rows"] == 2
        assert result["success_rate"] == 0.0

        stored_data = mock_store.call_args[0][0]
        assert stored_data["summary"]["total"] == 2
        assert stored_data["summary"]["success"] == 0
        assert stored_data["summary"]["failed"] == 2
        assert stored_data["summary"]["predefined"] == 0

        for row in stored_data["rows"]:
            assert row["_geocode_status"] == "failed"
            assert row["_lat"] is None
            assert row["_lon"] is None
            assert row["_geocode_error"] == "all_providers_failed"

        # Should have tried all 3 providers for each batch
        # Since both rows are in one batch, 3 provider calls total
        assert mock_batch_obj.call_count == 3
