"""Deep-review carto-platform CP-11 / CP-17：monitoring_report。"""

from __future__ import annotations

import sys
import types


def test_cp11_write_pdf_runs_under_weasyprint_mutex(monkeypatch, tmp_path):
    from app.services.publication_export import _WEASYPRINT_LOCK
    from app.tools import monitoring_report as mr

    seen: dict[str, bool] = {}

    class _HTML:
        def __init__(self, string, url_fetcher=None):
            pass

        def write_pdf(self, path):
            # 锁被持有时非阻塞抢锁必然失败
            got = _WEASYPRINT_LOCK.acquire(blocking=False)
            if got:
                _WEASYPRINT_LOCK.release()
            seen["locked"] = not got

    monkeypatch.setitem(sys.modules, "weasyprint", types.SimpleNamespace(HTML=_HTML))
    mr._html_to_pdf("<p>x</p>", str(tmp_path / "x.pdf"))
    assert seen == {"locked": True}


def test_cp17_every_template_key_maps_to_existing_template():
    from pathlib import Path

    from app.tools import monitoring_report as mr

    tdir = Path(mr.__file__).resolve().parent.parent / "services" / "templates"
    for key in ("natural_resources", "vegetation", "water", "fire", "unknown"):
        assert (tdir / mr._get_template_name(key)).is_file(), key
