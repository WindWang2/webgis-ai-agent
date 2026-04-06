# ============================================================
# WebGIS AI Agent 应用日志配置（Python）
# ============================================================

import sys
import logging
import logging.handlers
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 创建日志目录
LOG_DIR = Path("./logs")
LOG_DIR.mkdir(exist_ok=True)

# 定义日志格式
LOG_FORMATTER = logging.Formatter(
    fmt="%(asctime)s [%(levelname)-8s] %(name)-20s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

CONSOLE_FORMATTER = logging.Formatter(
    fmt="\033[36m%(asctime)s\033[0m [\033[1;%(levelname)sm%(levelname)s\033[0m] \033[33m%(name)-20s\033[0m: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def get_logger(name: str, level: str = "INFO"):
    """
    创建标准化的日志记录器
    
    Args:
        name: 日志记录器名称，通常使用 __name__
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    
    Returns:
        配置好的 Logger 实例
    """
    logger = logging.getLogger(name)
    
    # 避免重复添加 handler
    if logger.handlers:
        return logger
    
    # 将字符串级别转换为 logging 常量
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    
    # 控制台 Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(CONSOLE_FORMATTER)
    console_handler.setLevel(numeric_level)
    logger.addHandler(console_handler)
    
    # 文件 Handler - 按日期轮转
    # 生产环境使用 Rollover 每天午夜轮转，保留 14 天
    file_handler = RotatingFileHandler(
        filename=str(LOG_DIR / f"{name.replace('.', '_')}.log"),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=14,
        encoding="utf-8"
    )
    file_handler.setFormatter(LOG_FORMATTER)
    file_handler.setLevel(numeric_level)
    logger.addHandler(file_handler)
    
    # 结构化日志增强（如需要在结构化环境使用）
    # 未来可以在这里添加 JSON formatter 给 ELK/Loki
    # from logging_formatters import JSONFormatter
    # json_handler = RotatingFileHandler(...)
    # json_handler.setFormatter(JSONFormatter())
    # logger.addHandler(json_handler)
    
    return logger


# === 标准化的顶层日志器 ===

# 主应用日志
app_logger = get_logger("app", "INFO")

# 数据库日志（降低到 WARNING 减少噪音）
db_logger = get_logger("app.db", "WARNING")

# API 日志
api_logger = get_logger("app.api", "INFO")

# 任务队列日志
task_logger = get_logger("app.tasks", "INFO")

# Celery 日志
celery_logger = get_logger("celery", "INFO")


# === 快速配置接口 ===
def setup_logging_from_env():
    """
    根据环境变量配置日志级别
    
    可用环境变量:
    - LOG_LEVEL: DEBUG, INFO, WARNING, ERROR
    - ENABLE_FILE_LOGGING: true/false (默认为是)
    - MAX_LOG_SIZE_MB: 单个日志文件最大 MB 数
    - RETAIN_DAYS: 保留天数
    """
    import os
    
    env = os.environ.get("ENV", "development").lower()
    log_level = os.environ.get("LOG_LEVEL", "INFO" if env == "production" else "DEBUG")
    
    # 配置根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # 返回配置
    return {
        "env": env,
        "level": log_level,
        "debug_mode": env != "production"
    }