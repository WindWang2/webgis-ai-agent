"""P2 review-swarm: corrupt session state must fail closed, never be overwritten."""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.schemas.georeferencing_schema import (
    GeorefDomainError,
    GeorefErrorCode,
    GeorefSourceRecord,
)
from app.services.georeferencing.store import (
    georef_session_dir,
    get_plan,
    save_source,
)


def _source(session_id: str) -> GeorefSourceRecord:
    return GeorefSourceRecord(
        source_id="gsrc_corrupt_state",
        session_id=session_id,
        filename="corrupt.png",
        detected_format="PNG",
        width=8,
        height=8,
        bands=3,
        dtype="uint8",
        color_mode="RGB",
        has_alpha=False,
        original_blob_key="k-orig",
        normalized_blob_key="k-norm",
        normalized_path="",
        original_sha256="a" * 64,
        normalized_sha256="b" * 64,
        byte_size=16,
        created_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.parametrize("corrupt_payload", ['{"sources": ', "[]"])
def test_corrupt_state_json_is_not_silently_replaced_with_empty_state(
    tmp_path, monkeypatch, corrupt_payload
):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path), raising=False)
    sid = "sess-p2-corrupt-state"
    state_path = georef_session_dir(sid) / "state.json"
    state_path.write_text(corrupt_payload, encoding="utf-8")

    with pytest.raises(GeorefDomainError) as read_exc:
        get_plan(sid, "gplan_missing")
    assert read_exc.value.code == GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED

    with pytest.raises(GeorefDomainError) as save_exc:
        save_source(_source(sid))
    assert save_exc.value.code == GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED

    # The unreadable file must be left untouched for manual recovery.
    assert state_path.read_text(encoding="utf-8") == corrupt_payload
