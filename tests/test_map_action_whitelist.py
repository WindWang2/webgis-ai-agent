"""H13: Map action renderer must validate commands against a whitelist."""
import re


def _read_renderer_source() -> str:
    with open("frontend/components/chat/map-action-renderer.tsx") as f:
        return f.read()


def _read_handler_source() -> str:
    with open("frontend/components/map/map-action-handler.tsx") as f:
        return f.read()


class TestMapActionWhitelist:
    def test_renderer_has_command_whitelist(self):
        """Renderer must validate commands against a whitelist before dispatching."""
        source = _read_renderer_source()
        assert "ALLOWED_COMMANDS" in source or "ALLOWED_ACTIONS" in source, (
            "map-action-renderer.tsx has no command whitelist. "
            "Add a Set of allowed commands and check action.command before dispatchAction."
        )

    def test_renderer_checks_command_against_whitelist(self):
        """dispatchAction must only be called if command is in whitelist."""
        source = _read_renderer_source()
        # There should be a guard like: ALLOWED_COMMANDS.has(action.command)
        assert "ALLOWED_COMMANDS" in source and "has(action" in source, (
            "Renderer must check action.command against ALLOWED_COMMANDS.has() before dispatch. "
            "Currently dispatches any JSON with a truthy 'command' field."
        )

    def test_whitelist_covers_handler_commands(self):
        """The renderer whitelist must include all commands the handler supports."""
        handler = _read_handler_source()
        renderer = _read_renderer_source()

        # Extract all case labels from handler
        cases = re.findall(r"case\s+'([^']+)'", handler)
        handler_commands = set(c.lower() for c in cases)

        # Extract ALLOWED_COMMANDS from renderer
        allowed_match = re.search(
            r"ALLOWED_COMMANDS\s*=\s*new\s+Set\(\[([^\]]*)\]", renderer
        )
        assert allowed_match, "Cannot find ALLOWED_COMMANDS Set in renderer"

        allowed = set(
            s.strip().strip("'\"").lower()
            for s in allowed_match.group(1).split(",")
            if s.strip()
        )

        missing = handler_commands - allowed
        assert not missing, (
            f"Handler commands not in renderer whitelist: {missing}"
        )
