"""H11: WebSocket heartbeat interval must be cleared on unmount."""
import ast


def _read_hook_source() -> str:
    with open("frontend/lib/hooks/use-websocket.ts") as f:
        return f.read()


def _find_cleanup_block(source: str) -> str:
    """Extract the useEffect cleanup return function body."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, str):
            pass
    # AST can't parse TSX directly; use text search.
    return source


class TestWebSocketHeartbeatCleanup:
    def test_unmount_clears_heartbeat_interval(self):
        """Unmount cleanup must clear the ping interval, not just close the socket.

        Bug: cleanup sets onclose=null before calling close(), which prevents
        the onclose handler from running clearInterval on the heartbeat.
        Fix: cleanup must explicitly clearInterval from the socket before closing.
        """
        source = _read_hook_source()

        # The cleanup function must contain clearInterval for the ping interval
        # Pattern: either clearInterval((socketRef.current as any)._pingInterval)
        # or similar explicit cleanup of the ping timer before nullifying onclose.
        assert "clearInterval" in source, (
            "use-websocket.ts must use clearInterval for heartbeat cleanup"
        )

        # Find the cleanup block
        cleanup_start = source.find("return () => {")
        assert cleanup_start != -1, "No cleanup function found"

        cleanup_end = source.find("};", cleanup_start + 1)
        # Find the second }; to get full cleanup
        cleanup_end = source.find("};", cleanup_end + 1)

        cleanup_block = source[cleanup_start:cleanup_end + 2]

        # The cleanup must call clearInterval on the ping interval
        assert "_pingInterval" in cleanup_block, (
            "Cleanup block does not clear _pingInterval — heartbeat timer leaks on unmount. "
            "Fix: add clearInterval((socketRef.current as any)?._pingInterval) before onclose=null."
        )
