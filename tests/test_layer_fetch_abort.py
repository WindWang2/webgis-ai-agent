"""H14: Layer data fetches must be aborted on session switch."""
import re


def _read_source() -> str:
    with open("frontend/lib/hooks/use-sse-stream.ts") as f:
        return f.read()


class TestLayerFetchAbort:
    def test_geojson_fetch_uses_abort_signal(self):
        """The geojson_ref fetch must pass an AbortSignal so it can be cancelled."""
        source = _read_source()

        # The geojson_ref fetch now goes through apiFetch (transport helper) with
        # an encodeURIComponent'd ref (H14 + Data Plane). Find the block by the
        # data endpoint path and verify an AbortSignal is passed to it.
        fetch_match = re.search(r"api/v1/layers/data/\$\{encodeURIComponent\(fetchRef\)\}", source)
        assert fetch_match, "Could not find geojson_ref fetch call (apiFetch encodeURIComponent path)"

        # Get surrounding context (300 chars after the fetch URL)
        context_start = fetch_match.start()
        context = source[context_start:context_start + 300]

        assert "signal" in context, (
            "geojson_ref fetch does not pass an AbortSignal. "
            "Add an AbortController ref that resets on session change and pass its signal to fetch()."
        )

    def test_has_abort_controller_ref(self):
        """Hook must maintain an AbortController ref for in-flight layer fetches."""
        source = _read_source()
        assert "AbortController" in source, (
            "use-sse-stream.ts does not use AbortController for layer data fetches. "
            "Add an abortControllerRef that is reset on session change."
        )
