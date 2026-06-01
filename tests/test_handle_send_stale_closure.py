"""H12: handleSend must use a ref to avoid stale isLoading closure."""


def _read_source() -> str:
    with open("frontend/lib/hooks/use-sse-stream.ts") as f:
        return f.read()


class TestHandleSendStaleClosure:
    def test_uses_isLoading_ref_not_closure(self):
        """handleSend must check isLoadingRef.current, not the stale isLoading closure var."""
        source = _read_source()

        # Find the handleSend guard line
        assert "isLoadingRef.current" in source, (
            "handleSend guard must use isLoadingRef.current (live ref) "
            "instead of isLoading (stale closure variable). "
            "This prevents dropped messages during render-cycle gaps."
        )

    def test_isLoading_ref_is_kept_in_sync(self):
        """The ref must be synchronized with isLoading on every render."""
        source = _read_source()
        assert "isLoadingRef.current = isLoading" in source, (
            "isLoadingRef must be assigned isLoading on every render to stay in sync."
        )
