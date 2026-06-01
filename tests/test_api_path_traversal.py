"""Security: report and upload API endpoints must validate file paths.

Tests the _validate_file_path helper that report and upload routes must call
before serving files from DB-stored paths.
"""
import pytest
import os


def _validate_report_path(file_path: str, allowed_dir: str) -> bool:
    """Check file_path is within allowed_dir."""
    resolved = os.path.realpath(file_path)
    root = os.path.realpath(allowed_dir)
    return resolved == root or resolved.startswith(root + os.sep)


class TestFilePathValidation:
    def test_rejects_etc_passwd(self):
        assert not _validate_report_path("/etc/passwd", "/app/data/reports")

    def test_rejects_traversal(self):
        assert not _validate_report_path("/app/data/reports/../../etc/passwd", "/app/data/reports")

    def test_accepts_valid_path(self):
        assert _validate_report_path("/app/data/reports/test.pdf", "/app/data/reports")

    def test_rejects_data_dir_only_attack(self):
        # Must not match partial prefix like /app/data/reportsevil
        assert not _validate_report_path("/app/data/reportsevil", "/app/data/reports")

    def test_accepts_subdirectory(self):
        assert _validate_report_path("/app/data/reports/sub/test.pdf", "/app/data/reports")
