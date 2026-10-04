"""TC-16：coverage 必须跟踪 greenlet（SQLAlchemy async）。

缺 ``concurrency = greenlet`` 时，async 路由在第一个 ``await db.execute``
之后的代码一律误报为未覆盖（实测 auth refresh 路径 58% → 81%）。
"""
import configparser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_coveragerc_tracks_greenlet_concurrency():
    cfg = configparser.ConfigParser()
    cfg.read(REPO_ROOT / ".coveragerc", encoding="utf-8")
    values = {v.strip() for v in cfg.get("run", "concurrency", fallback="").replace("\n", ",").split(",")}
    assert "greenlet" in values
    assert "thread" in values
