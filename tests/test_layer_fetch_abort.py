"""H14: Layer data fetches must be aborted on session switch."""
import re


def _read_source() -> str:
    with open("frontend/lib/hooks/use-sse-stream.ts") as f:
        return f.read()


class TestLayerFetchAbort:
    def test_geojson_fetch_uses_abort_signal(self):
        """The geojson_ref fetch must pass an AbortSignal so it can be cancelled."""
        source = _read_source()

        # Find the geojson_ref fetch block
        fetch_match = re.search(r"fetch\(`\$\{API_BASE\}/api/v1/layers/data/\$\{fetchRef\}", source)
        assert fetch_match, "Could not find geojson_ref fetch call"

        # Get surrounding context (200 chars after the fetch URL)
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
