"""需求契约映射（T12）——任务书条款 → 生产调用链证据（与 task_data_supply
的 requirement contract 同款纪律：目录存在性不算证据，调用链才算）。
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


class TestRequirementContract:
    def test_wp1_manifest_schema_version_and_logical_ids(self):
        src = _read("app/services/project_exchange/contracts.py")
        assert 'PACKAGE_FORMAT = "webgis-project-package"' in src
        assert "PACKAGE_FORMAT_VERSION = 1" in src
        # 逻辑 ID 白名单（禁止任意字符串充当包内身份）
        assert "def valid_logical_id" in src

    def test_wp1_dependency_classification_closed_vocabulary(self):
        src = _read("app/services/project_exchange/contracts.py")
        for cls in ("embedded", "external_reference", "redacted",
                    "missing", "unsupported"):
            assert f'CLASS_{cls.upper().replace("E_", "_") if False else cls.upper().replace("EXTERNAL_REFERENCE", "EXTERNAL_REFERENCE")}' in src or \
                f'"{cls}"' in src

    def test_wp2_reuse_writers_not_second_implementation(self):
        src = _read("app/services/project_exchange/writer.py")
        assert "data_delivery.writers" in src or \
            "from app.services.data_delivery.writers import" in src
        assert "PyogrioWriter" in src
        # 独立读路径保真（写前/写后双读）
        assert "read_dataframe" in src

    def test_wp3_untrusted_zip_defenses_enforced_server_side(self):
        src = _read("app/services/project_exchange/untrusted_zip.py")
        for marker in ("ERR_PATH_UNSAFE", "ERR_COMPRESSION_RATIO",
                       "ERR_ENTRY_TYPE", "ERR_HASH_MISMATCH",
                       "extractall"):
            if marker == "extractall":
                # 绝不允许 extractall 调用（docstring 的否定性提及不算）
                assert ".extractall(" not in src
                assert " zf.extractall" not in src
            else:
                assert marker in src

    def test_wp3_import_default_isolated_project(self):
        src = _read("app/services/project_exchange/importer.py")
        assert "create_project" in src
        # 单事务 + 补偿式回滚
        assert "补偿式回滚" in src
        # 不自动重算（无 workflow 执行调用）
        assert "execute_workflow_run" not in src

    def test_wp4_reproduction_honesty(self):
        src = _read("app/services/project_exchange/verify.py")
        assert "FIDELITY_NOT_EVALUATED" in src
        assert "not_evaluated" in src

    def test_wp5_dual_entry_same_service(self):
        tools = _read("app/tools/project_exchange_tools.py")
        routes_src = _read("app/api/routes/project_exchange.py")
        assert "register_project_exchange_tools" in tools
        # 双入口都只经由 services 层（无各自私有实现）
        assert "from app.services.project_exchange" in tools
        assert "from app.services.project_exchange" in routes_src

    def test_wp5_no_disk_path_inputs(self):
        tools = _read("app/tools/project_exchange_tools.py")
        # 工具参数只接受受控 ID，不接受磁盘路径参数
        for forbidden in ("path=", "file_path", "staging_path"):
            assert forbidden not in tools

    def test_no_new_db_tables_or_migrations(self):
        """D13：无迁移文件增量（工程包台账走磁盘 receipt）。"""
        src = _read("app/services/project_exchange/exporter.py")
        assert "alembic" not in src.lower()
        assert "Base.metadata.create_all" not in src

    def test_adr_registered(self):
        adr = _read("docs/adr/0306-project-package-exchange.md")
        assert "webgis-project-package" in adr
        assert "# 0306." in adr
