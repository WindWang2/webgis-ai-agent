"""Security: skill code AST validator must block all known bypass patterns."""
import re
from pathlib import Path

from app.tools.skills import _validate_skill_code


def _extract_python_blocks(text: str) -> list[str]:
    return re.findall(r"```python\s*\n(.*?)```", text, re.DOTALL)


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

    # --- Issue #399: module-alias escapes (P3 defense-in-depth) ---

    def test_blocks_platform_import(self):
        assert _validate_skill_code("import platform"), "platform.os exposes full os module"

    def test_blocks_posixsubprocess_import(self):
        assert _validate_skill_code("import _posixsubprocess"), "fork_exec executes processes"

    def test_blocks_posix_import(self):
        assert _validate_skill_code("import posix"), "posix is the raw module os wraps"

    def test_blocks_nt_import(self):
        assert _validate_skill_code("import nt"), "nt is the Windows raw os module"

    def test_blocks_runpy_import(self):
        assert _validate_skill_code("import runpy"), "runpy.run_path executes arbitrary files"

    def test_blocks_zipimport_import(self):
        assert _validate_skill_code("import zipimport"), "zipimport executes modules from zips"

    def test_blocks_pty_import(self):
        assert _validate_skill_code("import pty"), "pty.fork spawns child processes"

    def test_blocks_platform_alias_import(self):
        # Aliased import still names the blocked module.
        assert _validate_skill_code("import platform as p")

    def test_blocks_platform_os_read_vector(self):
        # Verified issue #399 escape: file read through platform.os.
        code = (
            'import platform\n'
            'fd = platform.os.open("/app/.env", 0)\n'
            'print(platform.os.read(fd, 4096))\n'
        )
        errors = _validate_skill_code(code)
        assert errors, "platform.os.* chain must be rejected"

    def test_blocks_platform_os_system_vector(self):
        code = "import platform\nplatform.os.system('id')\n"
        errors = _validate_skill_code(code)
        assert errors, "platform.os.system chain must be rejected"

    def test_blocks_posixsubprocess_fork_exec_vector(self):
        # Verified issue #399 escape: in-process process execution.
        code = (
            "import _posixsubprocess\n"
            "_posixsubprocess.fork_exec([b'/bin/sh'], [], -1, -1, -1, -1, "
            "0, None, None, 0, True, True)\n"
        )
        errors = _validate_skill_code(code)
        assert errors, "fork_exec chain must be rejected"

    def test_blocks_deep_attribute_chain_segment(self):
        # Chain-level check catches a dangerous segment far from the call.
        code = "import numpy as np\nnp.ctypeslib.ctypes.CDLL(None).system('id')\n"
        errors = _validate_skill_code(code)
        assert errors, "deep ctypes/system chain segment must be rejected"

    def test_blocks_open_attribute_chain(self):
        # codecs.open / tokenize.open are file-read escapes without the
        # open() builtin; attribute `open` is blocked aggressively.
        assert _validate_skill_code("import codecs\ncodecs.open('/app/.env')\n")

    def test_blocks_importlib_import_module(self):
        assert _validate_skill_code("import importlib\nimportlib.import_module('os')\n")

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

    def test_allows_scientific_attribute_chains(self):
        # "io"/"types" are legitimate scientific submodules (scipy.io,
        # pandas.api.types) and must not trip the chain check.
        assert not _validate_skill_code("import scipy.io\nscipy.io.loadmat('a.mat')")
        assert not _validate_skill_code(
            "import pandas as pd\npd.api.types.is_numeric_dtype(1)"
        )
        assert not _validate_skill_code(
            "import geopandas as gpd\ngpd.read_file('data.geojson').plot()"
        )
        assert not _validate_skill_code(
            "import numpy as np\nnp.gradient(np.array([1, 2]))"
        )

    def test_allows_realistic_geo_analysis_skill(self):
        # A representative legitimate skill modeled on the app/skills
        # workflows (terrain_analysis / buffer_analysis). Guards against
        # over-blocking real usage.
        code = '''\
import numpy as np
import geopandas as gpd
from shapely.geometry import Point

def compute_slope(dem):
    dx, dy = np.gradient(dem)
    return np.arctan(np.sqrt(dx ** 2 + dy ** 2)) * (180 / np.pi)

def register_skills(registry):
    def terrain_analysis(region, dem_data=None):
        if dem_data is None:
            return {"status": "no dem"}
        slope = compute_slope(dem_data)
        high = int(np.where(slope > 25)[0].size)
        return {"region": region, "high_risk_count": high}

    def buffer_analysis(center, radius_km):
        pt = Point(center["lon"], center["lat"])
        gdf = gpd.GeoDataFrame({"geometry": [pt.buffer(radius_km / 111.0)]})
        return gdf.to_json()

    registry.register(name="terrain_analysis", description="坡度分析",
                      func=terrain_analysis, tier=2, domains=["terrain"],
                      param_descriptions={})
    registry.register(name="buffer_analysis", description="缓冲区分析",
                      func=buffer_analysis, tier=2, domains=["analysis"],
                      param_descriptions={})
'''
        assert not _validate_skill_code(code)


class TestExistingSkillsRegression:
    """Issue #399: deny-list hardening must not reject existing skills
    (防自我破坏). Every skill currently shipped in app/skills/ must still
    validate with zero errors."""

    def test_all_shipped_skills_still_pass(self):
        skills_dir = Path(__file__).resolve().parents[1] / "app" / "skills"
        assert skills_dir.is_dir(), f"missing skills dir: {skills_dir}"
        # Shipped skills are .md workflow files; if they ever carry python
        # blocks (or grow .py files), each block must still pass validation.
        # test_allows_realistic_geo_analysis_skill guards the representative
        # legitimate-skill shape against over-blocking.
        for path in sorted(skills_dir.iterdir()):
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".md":
                for i, block in enumerate(_extract_python_blocks(text)):
                    errors = _validate_skill_code(block)
                    assert errors == [], (
                        f"{path.name} python block {i} rejected: {errors}"
                    )
            elif path.suffix == ".py":
                errors = _validate_skill_code(text)
                assert errors == [], f"{path.name} rejected: {errors}"


class TestIssue1113P3SkillSandbox:
    """#1113 P3-2: alias bypass PoCs + expanded dunder string deny-list."""

    def test_blocks_any_dunder_string_literal(self):
        assert _validate_skill_code('x = "__globals__"')
        assert _validate_skill_code('x = "__class__"')
        assert _validate_skill_code('x = "__mro__"')

    def test_blocks_eval_alias_poc_at_runtime_builtins(self, tmp_path):
        """AST may miss `e = eval; e(...)`; restricted builtins must NameError."""
        import importlib.util
        from app.tools.skills import _restricted_skill_builtins

        code = "e = eval\ne(\"1+1\")\n"
        path = tmp_path / "evil.py"
        path.write_text(code)
        spec = importlib.util.spec_from_file_location("evil_skill", path)
        module = importlib.util.module_from_spec(spec)
        module.__dict__["__builtins__"] = _restricted_skill_builtins()
        try:
            spec.loader.exec_module(module)
            raised = None
        except Exception as exc:  # noqa: BLE001
            raised = exc
        assert raised is not None, "eval alias PoC must fail under restricted builtins"
        assert isinstance(raised, NameError) or "eval" in str(raised).lower()

    def test_blocks_getattr_globals_poc_string(self):
        code = (
            'g = getattr\n'
            'glb = g(obj, "__globals__")\n'
        )
        errors = _validate_skill_code(code)
        assert errors, "getattr + __globals__ string PoC must be rejected"
