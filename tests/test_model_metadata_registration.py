"""防回归（评审 [High]）：模型模块注册完整性。

migrations/env.py 与 app/models/__init__.py 的 import 清单曾漏掉新增模型模块
（harness_journal / gis_context / ads_fabric …）—— Alembic autogenerate 对
已迁移库会把缺注册的表生成 drop_table（数据丢失）。这里用静态 AST 对比钉死
契约：app/models 下每个声明了表的模块都必须被两处 import 覆盖，且其全部
表名真实出现在 Base.metadata。
"""
import ast
import importlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = REPO_ROOT / "app" / "models"


def _imported_model_modules(source: str) -> set[str]:
    """收集 ``import app.models.<mod>`` / ``from app.models.<mod> import ...``。"""
    mods: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if len(parts) == 3 and parts[:2] == ["app", "models"]:
                    mods.add(parts[2])
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) == 3 and parts[:2] == ["app", "models"]:
                mods.add(parts[2])
    return mods


def _table_modules() -> dict[str, set[str]]:
    """app/models/*.py 中声明了 ``__tablename__`` 的模块 → 表名集合。

    纯静态（AST）判定：pydantic_models / api_response 这类无表模块自然出局。
    """
    tables: dict[str, set[str]] = {}
    for path in sorted(MODELS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        names: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(t, ast.Name) and t.id == "__tablename__"
                for t in node.targets
            ):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                names.add(node.value.value)
        if names:
            tables[path.stem] = names
    return tables


def test_alembic_env_imports_every_table_module():
    """env.py 的 import 集合必须覆盖每个表模块 —— 漏一个，下一次
    ``alembic revision --autogenerate`` 就会对已迁移库生成 drop_table。"""
    env_mods = _imported_model_modules(
        (REPO_ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    )
    missing = set(_table_modules()) - env_mods
    assert not missing, (
        f"migrations/env.py 缺少模型模块 import: {sorted(missing)} —— "
        "autogenerate 会把这些表当作 metadata 不存在并生成 drop_table（数据丢失）"
    )


def test_models_package_imports_every_table_module():
    """app.models 包必须同样 import 每个表模块：``import app.models`` 即完整
    注册 Base.metadata（与 env.py 的 autogenerate 视角一致）。"""
    init_mods = _imported_model_modules(
        (MODELS_DIR / "__init__.py").read_text(encoding="utf-8")
    )
    missing = set(_table_modules()) - init_mods
    assert not missing, (
        f"app/models/__init__.py 缺少模型模块 import: {sorted(missing)} —— "
        "import app.models 后这些表不会注册进 Base.metadata"
    )


def test_every_table_module_registers_its_tables():
    """逐个 importlib 加载表模块后，全部声明的表名必须出现在 Base.metadata。"""
    from app.core.database import Base

    declared: set[str] = set()
    modules = _table_modules()
    for mod in sorted(modules):
        importlib.import_module(f"app.models.{mod}")
        declared |= modules[mod]
    missing = declared - set(Base.metadata.tables)
    assert not missing, (
        f"声明了 __tablename__ 但未注册进 Base.metadata: {sorted(missing)}"
    )
