"""#703 批次三机械清扫测试：H-4 raster_id 白名单、admin 探测去重。

wall-clock 去计时化/挂 marker 的五处为测试重构本身，由既有套件回归覆盖，
不单独立测。
"""
import pytest
from unittest.mock import patch

from app.services.local_first import _split_place_and_keyword
from app.services.raster_store import resolve_png_path, save_png


# ── H-4: resolve_png_path 字符集白名单 ───────────────────────────────────

@pytest.fixture
def session_dir(tmp_path):
    d = tmp_path / ".webgis-agent" / "sess-h4"
    d.mkdir(parents=True)
    return d


def test_resolve_accepts_legitimate_id_roundtrip(session_dir):
    """save_png 产出的合法 id（含下划线/连字符）必须能解析回去。"""
    ref = save_png(session_dir, "abc_X-9", b"\x89PNG\r\n\x1a\n")
    path = resolve_png_path(session_dir, ref)
    assert path is not None and path.exists()


@pytest.mark.parametrize("bad_ref", [
    "ref:raster/../../etc/passwd",
    "ref:raster/..%2F..%2Fetc",          # 分隔符变体虽非 ../，含 % 同样不进白名单
    "ref:raster/a/b",
    "ref:raster/a\\b",
    "ref:raster/",
    "ref:raster/空格 id",
])
def test_resolve_rejects_non_whitelisted_ids(session_dir, bad_ref):
    """路径遍历/分隔符/空字符集一律 None（fail-silent 与 cache-miss 语义一致）。"""
    assert resolve_png_path(session_dir, bad_ref) is None


def test_resolve_rejects_missing_but_valid_id(session_dir):
    """合法字符集但文件不存在 → None（原语义保持）。"""
    assert resolve_png_path(session_dir, "ref:raster/no_such-id_1") is None


# ── admin 探测去重 ────────────────────────────────────────────────────────

def test_split_place_dedupes_admin_probe_per_candidate():
    """每候选至多两次调用（裸名 + 加「市」）；旧写法判定+赋值重复调用至多三次。"""
    calls = []

    def fake_admin(name):
        calls.append(name)
        # 第三个探测（「成都市」）命中——走「裸名未中、加市命中」分支
        return [103.0, 30.0, 104.0, 31.0] if name == "成都市" else None

    with patch("app.services.local_first.admin_bbox_wgs84", fake_admin):
        place, rest = _split_place_and_keyword("成都大学分布")

    assert place == "成都市"
    assert rest == "大学分布"
    # 「成都」（n=2 是最后一轮）之前的候选各至多两次：大学(2) + 成都大学(3)…
    # 关键不变量：同一字符串不被连续探测两次
    assert len(calls) == len(set(calls)), f"存在重复探测: {calls}"


def test_split_place_bare_name_hit_stops_immediately():
    """无后缀地名走探测环：裸名命中只调一次，且不再尝试后续候选。"""
    calls = []

    def fake_admin(name):
        calls.append(name)
        return [116.0, 39.0, 117.0, 40.0] if name == "五道口" else None

    with patch("app.services.local_first.admin_bbox_wgs84", fake_admin):
        place, rest = _split_place_and_keyword("五道口咖啡")

    assert place == "五道口"
    assert rest == "咖啡"
    assert calls.count("五道口") == 1
