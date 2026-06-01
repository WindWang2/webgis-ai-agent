"""Security: skill code AST validator must block all known bypass patterns."""
import pytest
from app.tools.skills import _validate_skill_code


class TestSkillCodeValidation:
    """Ensure _validate_skill_code blocks dangerous patterns."""

    # --- Must block ---

    def test_blocks_os_import(self):
        assert _validate_skill_code("import os")

    def test_blocks_subprocess_import(self):
        assert _validate_skill_code("import subprocess")

    def test_blocks_eval(self):
        assert _validate_skill_code("eval('1')")

    def test_blocks_exec(self):
        assert _validate_skill_code("exec('1')")

    def test_blocks_importlib(self):
        assert _validate_skill_code("import importlib"), "importlib allows arbitrary module loading"

    def test_blocks_builtins_module(self):
        assert _validate_skill_code("import builtins"), "builtins gives access to exec/eval/open"

    def test_blocks_sys_module(self):
        assert _validate_skill_code("import sys"), "sys.modules allows importing anything"

    def test_blocks_types_module(self):
        assert _validate_skill_code("import types"), "types can construct code objects"

    def test_blocks_io_module(self):
        assert _validate_skill_code("import io"), "io can read/write files"

    def test_blocks_code_module(self):
        assert _validate_skill_code("import code"), "code module provides interactive console"

    def test_blocks_shutil(self):
        assert _validate_skill_code("import shutil")

    def test_blocks_pathlib(self):
        assert _validate_skill_code("import pathlib")

    def test_blocks_open_builtin(self):
        assert _validate_skill_code("open('/etc/passwd')")

    def test_blocks_getattr(self):
        assert _validate_skill_code("getattr(obj, 'system')")

    def test_blocks_breakpoint(self):
        assert _validate_skill_code("breakpoint()")

    def test_blocks_compile(self):
        assert _validate_skill_code("compile('1','','exec')")

    def test_blocks_globals(self):
        assert _validate_skill_code("globals()")

    def test_blocks_locals(self):
        assert _validate_skill_code("locals()")

    def test_blocks__import__(self):
        assert _validate_skill_code("__import__('os')")

    def test_blocks_vfs_attribute(self):
        assert _validate_skill_code("obj.system('id')")

    def test_blocks_popen(self):
        assert _validate_skill_code("os.popen('id')")

    # --- Must allow ---

    def test_allows_math(self):
        assert not _validate_skill_code("import math\nmath.sqrt(4)")

    def test_allows_json(self):
        assert not _validate_skill_code("import json\njson.loads('{}')")

    def test_allows_geopandas(self):
        assert not _validate_skill_code("import geopandas as gpd")

    def test_allows_numpy(self):
        assert not _validate_skill_code("import numpy as np")

    def test_allows_shapely(self):
        assert not _validate_skill_code("from shapely.geometry import Point")

    def test_allows_safe_function_def(self):
        assert not _validate_skill_code("def hello():\n    return 'world'")

    def test_allows_register_skills(self):
        assert not _validate_skill_code(
            "def register_skills(registry):\n    pass"
        )
