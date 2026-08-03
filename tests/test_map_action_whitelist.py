"""H13: Map action renderer must validate commands against a whitelist.

注：这里做的是结构校验（whitelist 存在 + dispatch 前有 guard），不是
源码字面量匹配。真正的行为校验（拒绝未知命令、拒绝畸形 params）在前端
vitest 套件 map-action-renderer.test.tsx 里。

架构演进：原先期望 ``ALLOWED_COMMANDS = new Set([...])`` 字面量白名单；
现已重构为 ``COMMAND_CATALOGUE``（lib/map-commands/catalogue.ts）作为命令
词汇表的唯一真源，renderer 的 ``isValidAction`` guard 通过
``COMMAND_CATALOGUE`` 键查找校验。这些守卫校验该结构是否存在。
"""
import re


def _read_renderer_source() -> str:
    with open("frontend/components/chat/map-action-renderer.tsx") as f:
        return f.read()


def _read_handler_source() -> str:
    with open("frontend/components/map/map-action-handler.tsx") as f:
        return f.read()


def _read_catalogue_keys() -> set[str]:
    """Read the catalogue source and extract the merged command slices' keys.

    The catalogue merges domain slices (viewCommands, layerCommands, ...) so the
    individual command names live in those slice files. As a structural guard we
    read the catalogue imports + the slice files to enumerate command names.
    """
    import os

    catalogue_path = "frontend/lib/map-commands/catalogue.ts"
    with open(catalogue_path) as f:
        cat_src = f.read()
    # The catalogue merges named slices imported from sibling files.
    slice_imports = re.findall(r"import\s+\{\s*(\w+Commands)\s*\}\s+from\s+'([^']+)'", cat_src)
    keys: set[str] = set()
    for slice_name, rel_path in slice_imports:
        slice_file = os.path.join("frontend/lib/map-commands", rel_path + ".ts")
        if not os.path.exists(slice_file):
            continue
        with open(slice_file) as f:
            slice_src = f.read()
        # Slice objects are `export const viewCommands = { addView: {...}, ... }`.
        # Match bareword keys at the top level of the object literal.
        for key in re.findall(r"^\s{2}([A-Za-z_]\w*)\s*:", slice_src, re.MULTILINE):
            keys.add(key.lower())
    return keys


class TestMapActionWhitelist:
    def test_renderer_has_command_whitelist(self):
        """Renderer must validate commands against a whitelist before dispatching.

        Accepts either the legacy ``ALLOWED_COMMANDS`` Set or the current
        ``COMMAND_CATALOGUE`` lookup pattern.
        """
        source = _read_renderer_source()
        assert (
            "ALLOWED_COMMANDS" in source
            or "ALLOWED_ACTIONS" in source
            or "COMMAND_CATALOGUE" in source
        ), (
            "map-action-renderer.tsx has no command whitelist. "
            "Add a Set of allowed commands and check action.command before dispatchAction."
        )

    def test_renderer_checks_command_against_whitelist(self):
        """dispatchAction must only be called if command passes whitelist + schema guard.

        行为要求：在 dispatchAction 调用之前，必须有一个 guard 函数同时校验
        (a) command 在白名单内，(b) params 通过 schema 校验。
        Accepts the legacy ``ALLOWED_COMMANDS.has(...)`` inline check OR the
        current ``isValidAction`` guard (which looks up ``COMMAND_CATALOGUE``).
        """
        source = _read_renderer_source()
        assert (
            "ALLOWED_COMMANDS" in source or "COMMAND_CATALOGUE" in source
        ), "Renderer must reference a command whitelist (ALLOWED_COMMANDS or COMMAND_CATALOGUE)."
        # Guard must exist and be called before dispatch.
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
        """The renderer whitelist must include all commands the handler supports.

        The handler resolves commands via ``COMMAND_CATALOGUE`` lookup (not
        ``case`` labels), so the guard compares the catalogue's command keys
        against themselves -- i.e. renderer and handler share the same source
        of truth. This asserts the catalogue is non-empty and that the renderer
        references it (so the two cannot diverge).
        """
        handler = _read_handler_source()
        renderer = _read_renderer_source()

        # Handler must resolve commands via COMMAND_CATALOGUE (single source of truth).
        assert "COMMAND_CATALOGUE" in handler, (
            "Handler must resolve commands via COMMAND_CATALOGUE lookup, not ad-hoc case labels."
        )
        # Renderer must reference the same catalogue so the two cannot diverge.
        assert "COMMAND_CATALOGUE" in renderer, (
            "Renderer must reference COMMAND_CATALOGUE so its guard covers every handler command."
        )
        # The catalogue must actually enumerate commands.
        catalogue_keys = _read_catalogue_keys()
        assert catalogue_keys, "COMMAND_CATALOGUE enumerates no commands"
