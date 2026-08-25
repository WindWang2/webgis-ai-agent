"""E-3（#894）：实现已搬移到 app/lib/artifacts.py（lib 叶子层需要这些原语，
原位置造成 lib→services 反向依赖）。此处 re-export 保持兼容。"""
from app.lib.artifacts import *  # noqa: F401,F403
