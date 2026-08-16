"""redact_paths regression tests (Windows drive paths).

The old ``_WIN_DRIVE_PATH_RE`` required two consecutive backslashes, so real
Windows paths (single backslash) were never redacted. Both single and doubled
backslash forms must now match; Unix paths stay covered and non-paths stay
untouched.
"""
from app.utils.security import redact_paths


def test_single_backslash_windows_path_is_redacted():
    assert redact_paths(r"Error at C:\Users\k\app.py line 1") == \
        "Error at <path> line 1"


def test_doubled_backslash_windows_path_is_redacted():
    # Escaped/doubled form (e.g. inside repr or JSON strings).
    assert redact_paths("Error at C:\\\\Users\\\\k\\\\app.py line 1") == \
        "Error at <path> line 1"


def test_unix_absolute_path_is_redacted():
    assert redact_paths("failed to open /data/exports/x.geojson") == \
        "failed to open <path>"


def test_unix_relative_path_is_preserved():
    assert redact_paths("read ./data/x.geojson") == "read ./data/x.geojson"


def test_url_is_preserved():
    assert redact_paths("fetched https://example.com/a/b") == \
        "fetched https://example.com/a/b"


def test_plain_text_untouched():
    assert redact_paths("no paths here, just A1:B2 cells") == \
        "no paths here, just A1:B2 cells"


def test_empty_input():
    assert redact_paths("") == ""
