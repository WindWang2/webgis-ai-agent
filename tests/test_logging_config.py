"""日志配置模块测试"""
import logging
import sys
from pathlib import Path

import pytest

from app.core.logging_config import (
    get_logger,
    setup_logging_from_env,
    LOG_DIR,
    _get_shared_file_handler,
)


class TestGetLogger:
    def test_returns_logger_with_handlers(self):
        logger = get_logger("test_logger_1", level="DEBUG")
        assert isinstance(logger, logging.Logger)
        assert len(logger.handlers) >= 2  # console + file

    def test_does_not_duplicate_handlers(self):
        logger = get_logger("test_logger_2", level="INFO")
        first_count = len(logger.handlers)
        logger2 = get_logger("test_logger_2", level="INFO")
        assert len(logger2.handlers) == first_count

    def test_level_set_correctly(self):
        logger = get_logger("test_logger_3", level="ERROR")
        assert logger.level == logging.ERROR

    def test_file_handler_exists(self):
        logger = get_logger("test_logger_4", level="INFO")
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert len(file_handlers) >= 1


class TestSharedFileHandler:
    def test_single_handler_instance(self):
        h1 = _get_shared_file_handler()
        h2 = _get_shared_file_handler()
        assert h1 is h2

    def test_handler_points_to_app_log(self):
        h = _get_shared_file_handler()
        assert "app.log" in h.baseFilename


class TestSetupLoggingFromEnv:
    def test_returns_dict_with_env_info(self):
        result = setup_logging_from_env()
        assert "env" in result
        assert "level" in result
        assert "debug_mode" in result

    def test_debug_mode_false_in_production(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        result = setup_logging_from_env()
        assert result["debug_mode"] is False

    def test_debug_mode_true_in_development(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        result = setup_logging_from_env()
        assert result["debug_mode"] is True
