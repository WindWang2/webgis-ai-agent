"""日志配置模块测试"""
import logging


from app.core.logging_config import (
    get_logger,
    setup_logging_from_env,
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

    def test_later_logger_lowers_shared_handler_level(self, monkeypatch, tmp_path):
        """共享 handler 须接受最宽松的调用方级别（WARNING 后建 INFO 仍入库）。"""
        import app.core.logging_config as lc

        monkeypatch.setattr(lc, "LOG_DIR", tmp_path)
        monkeypatch.setattr(lc, "_shared_file_handler", None)
        monkeypatch.setattr(lc, "_file_handler_disabled", False)

        handler = lc._get_shared_file_handler(logging.WARNING)
        assert handler is not None
        try:
            assert handler.level == logging.WARNING
            assert lc._get_shared_file_handler(logging.INFO) is handler
            assert handler.level == logging.INFO
            # 更严格的调用方不得反过来抬高共享 handler 的级别
            assert lc._get_shared_file_handler(logging.ERROR) is handler
            assert handler.level == logging.INFO
        finally:
            handler.close()


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
