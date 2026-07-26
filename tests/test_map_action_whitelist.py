"""H13: Map action renderer must validate commands against a whitelist.

注：这里做的是结构校验（whitelist 存在 + dispatch 前有 guard），不是
源码字面量匹配。真正的行为校验（拒绝未知命令、拒绝畸形 params）在前端
vitest 套件 map-action-renderer.test.tsx 里。之前用 `has(action` 字面量
匹配会因变量重命名（action → a）误判 —— 已改为匹配 ALLOWED_COMMANDS
被实际调用的结构。
"""
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
        """dispatchAction must only be called if command passes whitelist + schema guard.

        行为要求：在 dispatchAction 调用之前，必须有一个 guard 函数同时校验
        (a) command 在 ALLOWED_COMMANDS 内，(b) params 通过 schema 校验。
        之前断言字面量 `has(action` 会在变量重命名时误判 —— 现在断言
        ALLOWED_COMMANDS 被引用 + 存在 guard 函数（isValidAction）。
        """
        source = _read_renderer_source()
        assert "ALLOWED_COMMANDS" in source, (
            "Renderer must define ALLOWED_COMMANDS whitelist."
        )
        # Guard must exist and be called before dispatch.
        # Accepts either inline `ALLOWED_COMMANDS.has(...)` or a named guard
        # function (current impl uses isValidAction which internally checks both).
        has_inline_check = bool(re.search(r"ALLOWED_COMMANDS\.has\(", source))
        has_guard_fn = bool(
            re.search(r"function\s+isValidAction\s*\(", source)
        ) and "dispatchAction" in source
        assert has_inline_check or has_guard_fn, (
            "Renderer must guard dispatchAction with a whitelist check "
            "(either inline ALLOWED_COMMANDS.has() or a validation function "
            "like isValidAction). Currently dispatches any JSON with a "
            "truthy 'command' field."
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
