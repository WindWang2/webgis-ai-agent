"""Celery Worker 启动模块""""
from app.services.celery_config import celery_app
if __name__ == "__main__":
    celery_app.start()