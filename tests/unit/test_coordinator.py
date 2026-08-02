import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.mapspec.coordinator import compile_via_cli, validate


@pytest.fixture
def tmp_out_dir(tmp_path):
    return tmp_path / "out"


@pytest.mark.asyncio
async def test_compile_via_cli_success(tmp_out_dir):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"stdout", b"stderr")

    with patch("app.services.mapspec.coordinator.asyncio.create_subprocess_exec", return_value=mock_proc):
        # Create dummy report and style files
        tmp_out_dir.mkdir(parents=True, exist_ok=True)
        (tmp_out_dir / "compile-report.json").write_text(json.dumps({"success": True, "stats": {}}))
        (tmp_out_dir / "style.json").write_text(json.dumps({"version": 8}))

        res = await compile_via_cli(Path("dummy.json"), tmp_out_dir)

        assert res["success"] is True
        assert res["report"]["success"] is True
        assert res["style"] == {"version": 8}


@pytest.mark.asyncio
async def test_compile_via_cli_timeout(tmp_out_dir):
    mock_proc = AsyncMock()
    mock_proc.kill = MagicMock()  # kill() is synchronous
    # Setting up the process to simulate a timeout during communicate
    with patch("app.services.mapspec.coordinator.asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("app.services.mapspec.coordinator.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            res = await compile_via_cli(Path("dummy.json"), tmp_out_dir)

            assert mock_proc.kill.called
            assert mock_proc.wait.called
            assert res["success"] is False
            assert res["report"]["errors"][0]["code"] == "CLI_UNAVAILABLE"


@pytest.mark.asyncio
async def test_compile_via_cli_not_found(tmp_out_dir):
    with patch("app.services.mapspec.coordinator.asyncio.create_subprocess_exec", side_effect=FileNotFoundError("Mocked FileNotFoundError")):
        res = await compile_via_cli(Path("dummy.json"), tmp_out_dir)

        assert res["success"] is False
        assert res["report"]["errors"][0]["code"] == "CLI_UNAVAILABLE"


@pytest.mark.asyncio
async def test_compile_via_cli_no_report_file(tmp_out_dir):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"mock stdout output", b"")

    with patch("app.services.mapspec.coordinator.asyncio.create_subprocess_exec", return_value=mock_proc):
        # Ensure no report file exists
        res = await compile_via_cli(Path("dummy.json"), tmp_out_dir)

        assert res["success"] is True
        assert res["report"]["success"] is True
        assert res["report"]["errors"][0]["code"] == "CLI_ERROR"
        assert res["report"]["errors"][0]["message"] == "mock stdout output"


def test_validate_valid():
    mapspec = {
        "sources": {"source1": {}},
        "layers": [{"id": "layer1", "source": "source1"}],
    }
    res = validate(mapspec)
    assert res["success"] is True
    assert len(res["errors"]) == 0


def test_validate_missing_sources():
    mapspec = {"layers": []}
    res = validate(mapspec)
    assert res["success"] is False
    assert any(e["code"] == "MISSING_SOURCES" for e in res["errors"])


def test_validate_invalid_source_ref():
    mapspec = {
        "sources": {"source1": {}},
        "layers": [{"id": "layer1", "source": "source2"}],
    }
    res = validate(mapspec)
    assert res["success"] is False
    assert any(e["code"] == "INVALID_SOURCE_REF" for e in res["errors"])


def test_validate_invalid_stops_count():
    mapspec = {
        "sources": {"source1": {}},
        "layers": [{
            "id": "layer1",
            "source": "source1",
            "paint": {
                "line-width": {
                    "method": "interpolate",
                    "stops": [[0, 1]]
                }
            }
        }],
    }
    res = validate(mapspec)
    assert res["success"] is False
    assert any(e["code"] == "INVALID_STOPS_COUNT" for e in res["errors"])


def test_validate_non_increasing_stops():
    mapspec = {
        "sources": {"source1": {}},
        "layers": [{
            "id": "layer1",
            "source": "source1",
            "paint": {
                "line-width": {
                    "method": "interpolate",
                    "stops": [[10, 1], [5, 2]]
                }
            }
        }],
    }
    res = validate(mapspec)
    assert res["success"] is False
    assert any(e["code"] == "NON_INCREASING_STOPS" for e in res["errors"])
