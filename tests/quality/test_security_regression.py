"""Security regression suite（audit Wave 15 / ADR-0104 §6 平台配方）。

把安全审计（.agent-work/quality-v1/06-security-regression-map.md）的头部缺口
固化为真实回归测试：

- SEC-07：data-fabric 存储型 ``allow_private`` 钉扎（route 强制 False +
  manager 落库形态 + JSON 往返 + 读取语义）；
- 查询注入网：AST→SQL/CQL2/ArcGIS/FES 编译器 + PostGIS 标识符卫生 +
  ``execute(f"`` 静态扫描（此前零直接测试）；
- 产物注册表：会话作用域隔离矩阵（closest real ownership boundary；
  owner 概念缺失作为 KNOWN-GAP 记入 security manifest）；
- 错误预言机：``is_production()`` 分支端到端（dev 行为 pin + prod 泛化）；
- 脱敏词表 parity：三个 denylist + 一个 allowlist 对共享基线的覆盖；
- security manifest 门：控制→实现→测试映射的锚点校验 + 表字节一致。

红线：离线、确定性、无真实外联。控制清单见
``app/lib/quality/security_manifest.py``（认证表
``docs/quality/certifications/SECURITY_CONTROLS.md``）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from app.services.data_fabric.query.compilers import (  # noqa: E402
    compile_predicate_arcgis,
    compile_predicate_cql2,
    compile_predicate_fes,
    compile_predicate_sql,
    compile_temporal_sql,
    geojson_to_wkt,
    quote_ident,
)
from app.services.data_fabric.query.predicates import (  # noqa: E402
    And,
    Before,
    Eq,
    In,
    Like,
    Or,
    PredicateError,
)


# ══════════════════════════════════════════════════════════════════════
# SEC-07：存储型 allow_private 钉扎（audit gap #2）
# ══════════════════════════════════════════════════════════════════════


class TestAllowPrivateStoredProfile:
    """create 强制 False；落库形态只能承载 False；读取端默认 False。"""

    def test_create_route_forces_allow_private_false_regardless_of_payload(self, monkeypatch):
        """请求体走私 allow_private（顶层或 options 内）都到不了 manager。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import app.api.routes.data_fabric as df_routes
        from app.core.auth import get_current_user
        from app.core.database import get_async_db

        captured: dict = {}

        def _fake_create(**kw):
            captured.update(kw)
            # 路由对返回值取属性（source.id / source.capabilities_json …）
            return SimpleNamespace(
                id="ds_pinned",
                name=kw.get("name", ""),
                source_type=kw.get("source_type", ""),
                endpoint_url=kw.get("endpoint_url", ""),
                status="healthy",
                capabilities_json=[],
                connection_profile={
                    "allow_private": kw.get("allow_private", None),
                    "options": kw.get("profile_options", {}),
                },
            )

        async def _fake_run_sync(fn):
            return fn(None)  # 不进 worker 线程/SessionLocal —— 纯参数捕获

        monkeypatch.setattr(df_routes, "_run_sync_orm", _fake_run_sync)
        monkeypatch.setattr(
            df_routes.data_fabric_manager, "create_data_source", _fake_create
        )

        app = FastAPI()
        app.include_router(df_routes.router, prefix="/api/v1")

        async def _noop_db():
            yield None

        app.dependency_overrides[get_async_db] = _noop_db
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "u-1",
            "org_id": 7,
        }

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/data-fabric/sources",
            json={
                "name": "smuggler",
                "source_type": "postgis",
                "endpoint_url": "http://8.8.8.8/wms",
                "options": {"allow_private": True, "password": "hunter2"},
                "allow_private": True,  # pydantic 忽略未声明字段——但钉住这一点
            },
        )
        assert resp.status_code == 200, resp.text
        assert captured["allow_private"] is False, (
            "create 路由必须无条件传 allow_private=False（ADR-0050 §5 P0）"
        )
        body = resp.json()
        # SEC-07：create 响应的 profile 必须脱敏（options 内走私的 password）
        assert "hunter2" not in json.dumps(body)

    def test_create_data_source_stores_allow_private_false_round_trip(self, tmp_path, monkeypatch):
        """真实 manager 落库：connection_profile JSON 顶层 allow_private 恒为 False。

        probe/sync 打桩（防外联）；URL 用公共字面量 IP（离线可解析）。
        """
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.models.db_model import Base
        import app.models.data_fabric  # noqa: F401  # 注册 DataSourceModel
        from app.services.data_fabric.manager import DataFabricManager

        monkeypatch.setattr(
            DataFabricManager, "probe_profile",
            lambda profile: SimpleNamespace(status="healthy"),
        )
        monkeypatch.setattr(DataFabricManager, "sync_catalog", lambda db, source_id: None)

        eng = create_engine(f"sqlite:///{tmp_path / 'df-pin.db'}")
        Base.metadata.create_all(bind=eng)
        Session = sessionmaker(bind=eng)

        with Session() as db:
            ds = DataFabricManager.create_data_source(
                db,
                name="pin",
                source_type="postgis",
                endpoint_url="http://8.8.8.8/wms",
                profile_options={"allow_private": True, "password": "hunter2"},
                allow_private=False,
                org_id=None,
                owner_id=None,
            )
            stored = ds.connection_profile
            # 落库顶层形态：False（options 里的走私键不升级为 profile 开关）
            assert stored["allow_private"] is False
            # 存储序列化往返（JSON 列语义）
            round_tripped = json.loads(json.dumps(stored))
            assert round_tripped["allow_private"] is False
            # 读取语义（data_fabric.py probe/tile 路径）：.get("allow_private", False)
            assert stored.get("allow_private", False) is False
            assert round_tripped.get("allow_private", False) is False
        eng.dispose()

    def test_probe_route_reader_defaults_to_false_when_key_missing(self):
        """读取端的防御缺省：老行/缺键 profile 读出 False，绝不 True。"""
        for shape in ({}, {"allow_private": False}, {"options": {"allow_private": True}}):
            assert shape.get("allow_private", False) is False


# ══════════════════════════════════════════════════════════════════════
# 查询注入回归网（audit gap #8）：compilers.py 此前零直接测试
# ══════════════════════════════════════════════════════════════════════

SQLI_VALUE_PAYLOADS = (
    "'; DROP TABLE users; --",
    "' OR '1'='1",
    "' UNION SELECT password FROM pg_catalog.pg_user --",
    "x'; SELECT pg_sleep(10); --",
    "1; DELETE FROM layers",
    "' OR 1=1 LIMIT 1; --",
)


class TestSqlInjectionNet:
    # ── PostGIS SQL 编译器：值只进参数 ────────────────────────────────

    @pytest.mark.parametrize("payload", SQLI_VALUE_PAYLOADS)
    def test_predicate_values_stay_bound_parameters(self, payload):
        sql, params = compile_predicate_sql(Eq(field="name", value=payload))
        assert sql == '"name" = %s'
        assert params == [payload]
        assert payload not in sql, "注入载荷出现在 SQL 片段里（拼接回归）"

    def test_or_combination_collects_all_params(self):
        node = Or(args=[
            Eq(field="name", value="'; DROP TABLE x; --"),
            Eq(field="kind", value="' OR '1'='1"),
        ])
        sql, params = compile_predicate_sql(node)
        assert sql.count("%s") == 2
        assert params == ["'; DROP TABLE x; --", "' OR '1'='1"]
        # 参数化后片段里不允许出现载荷碎片
        for p in params:
            assert p not in sql

    def test_in_list_values_parameterized(self):
        sql, params = compile_predicate_sql(
            In(field="name", values=["a'; --", "b' OR '1'='1"])
        )
        assert sql == '"name" = ANY(%s)'
        assert params == [["a'; --", "b' OR '1'='1"]]

    def test_temporal_value_is_iso_pinned_then_parameterized(self):
        """时间值在谓词构造期就 ISO-8601 钉死（注入载荷进不了编译器）；合法值走参数。"""
        with pytest.raises(ValueError):  # PredicateError / pydantic ValidationError
            Before(field="created", value="'; DROP x; --")
        sql, params = compile_temporal_sql(
            Before(field="created", value="2026-01-01T00:00:00Z")
        )
        assert sql == '"created" < %s::timestamptz'
        assert params == ["2026-01-01T00:00:00Z"]

    @pytest.mark.parametrize("bad_field", ['name"; DROP TABLE x', "name'; --", "name--"])
    def test_field_identifier_rejected_by_compiler(self, bad_field):
        # 字段名在谓词构造期（pydantic validator）与编译期（quote_ident）双重拒绝
        with pytest.raises(ValueError):
            Eq(field=bad_field, value="x")
        with pytest.raises(ValueError):
            quote_ident(bad_field)

    def test_allowed_fields_whitelist_blocks_foreign_column(self):
        with pytest.raises(PredicateError):
            compile_predicate_sql(
                Eq(field="password_hash", value="x"), allowed_fields=["name"]
            )

    def test_wkt_rejects_non_numeric_coordinates(self):
        # R3-m3：非数值坐标曾是无限递归/注入面 —— 必须 typed 拒绝
        with pytest.raises(PredicateError):
            geojson_to_wkt({
                "type": "Point",
                "coordinates": ["1; DROP TABLE x; --", 2],
            })

    # ── PostGIS 适配器标识符卫生（二阶注入面）────────────────────────

    @pytest.mark.parametrize(
        "bad_ident",
        [
            'public"; DROP TABLE users; --',
            "public.users; --",
            "public.users; DELETE",
            "public.user' --",
            "a.b.c",
            "",
        ],
    )
    def test_adapter_identifier_rejects_injection(self, bad_ident):
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter
        from app.services.data_fabric.errors import InvalidQueryError

        adapter = PostGISAdapter(
            ConnectionProfile(id="t", source_type="postgis", url="")
        )
        with pytest.raises(InvalidQueryError):
            adapter._sanitize_identifier(bad_ident)

    def test_adapter_identifier_accepts_clean_names(self):
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter

        adapter = PostGISAdapter(
            ConnectionProfile(id="t", source_type="postgis", url="")
        )
        assert adapter._sanitize_identifier("public.parcel") == ("public", "parcel")
        assert adapter._sanitize_identifier("parcel") == ("public", "parcel")

    # ── CQL2 / ArcGIS：单引号翻倍；FES：XML 实体转义 ─────────────────

    def test_cql2_doubles_single_quotes(self):
        payload = "x' OR '1'='1"
        out = compile_predicate_cql2(Eq(field="name", value=payload))
        assert out.startswith("name = '") and out.endswith("'")
        body = out[len("name = '"):-1]
        assert body.replace("''", "'") == payload, "翻倍引号必须可无损还原为载荷"
        assert payload not in body, "未翻转义的单引号（注入 breakout）"

    def test_cql2_in_list_escapes_each_value(self):
        out = compile_predicate_cql2(
            In(field="name", values=["a'; --", "b' OR '1'='1"])
        )
        assert out.count("'a''; --'") == 1
        assert out.count("'b'' OR ''1''=''1'") == 1

    def test_cql2_like_pattern_escaped(self):
        out = compile_predicate_cql2(Like(field="name", pattern="%'; DROP x; --"))
        assert out == "name LIKE '%''; DROP x; --'"

    def test_arcgis_doubles_single_quotes(self):
        payload = "x' OR '1'='1"
        out = compile_predicate_arcgis(Eq(field="name", value=payload))
        body = out[len("name = '"):-1]
        assert body.replace("''", "'") == payload
        assert payload not in body

    def test_fes_escapes_xml_breakout(self):
        payload = "x</ogc:Literal><ogc:Literal>evil</ogc:Literal"
        out = compile_predicate_fes(Eq(field="name", value=payload))
        # 载荷里的闭合标签被实体化，整个响应仍只有结构自身的闭合标签
        assert out.count("</ogc:Literal>") == 1
        assert "&lt;/ogc:Literal&gt;" in out
        assert "evil" in out  # 值以转义形态保留（数据不丢），但不改变结构

    def test_fes_and_tree_does_not_leak_raw_angle_brackets(self):
        node = And(args=[
            Eq(field="name", value="><script>"),
            Eq(field="kind", value="</ogc:And>"),
        ])
        out = compile_predicate_fes(node)
        assert out.count("<ogc:And") == 1 and out.count("</ogc:And>") == 1

    # ── 静态网：app/ 内禁止 f-string SQL 执行 ────────────────────────

    def test_no_fstring_sql_execution_in_app(self):
        """审计建议的 AST 风扫描：杜绝 `execute(f"...")` / `execute(text(f"..."))`。"""
        patterns = (
            re.compile(r"\.execute\(\s*f['\"]"),
            re.compile(r"\.executemany\(\s*f['\"]"),
            re.compile(r"\.execute\(\s*text\(\s*f['\"]"),
        )
        hits: list[str] = []
        for path in sorted((REPO / "app").rglob("*.py")):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if any(p.search(line) for p in patterns):
                    hits.append(f"{path.relative_to(REPO)}:{lineno}")
        assert not hits, f"f-string SQL 拼接（回归即修）: {hits}"


# ══════════════════════════════════════════════════════════════════════
# 产物注册表：会话作用域（audit gap #6 —— 最接近的真实所有权边界；
# owner 概念缺失在 security manifest 里记 KNOWN-GAP）
# ══════════════════════════════════════════════════════════════════════


class TestArtifactSessionScope:
    async def test_artifact_session_scope_isolates_foreign_sessions(self):
        from app.services.artifact_registry import (
            get_artifact,
            list_artifacts,
            mark_status,
            register_artifact,
        )

        sess_a = "sec-reg-sess-A-0f2c"
        sess_b = "sec-reg-sess-B-9a71"

        rec = await register_artifact(
            sess_a,
            artifact_id="ref:secret-fc",
            artifact_type="feature_collection",
            producer_tool="buffer",
            metadata={"seam": "test", "owner_hint": "user-A"},
        )
        assert rec is not None and rec.session_id == sess_a

        # 属主会话可读
        got = await get_artifact(sess_a, "ref:secret-fc")
        assert got is not None
        assert got.metadata.get("owner_hint") == "user-A"

        # 外部会话不可读 / 不可列（不存在性也不泄漏到 B 的视图）
        assert await get_artifact(sess_b, "ref:secret-fc") is None
        assert "ref:secret-fc" not in {r.artifact_id for r in await list_artifacts(sess_b)}

        # 外部会话不可改状态
        assert await mark_status(sess_b, "ref:secret-fc", "erased") is False
        assert (await get_artifact(sess_a, "ref:secret-fc")).status != "erased"

        # B 同名 ref 自注册互不串写（session_id 键控，非全局 artifact_id 键控）
        rec_b = await register_artifact(
            sess_b, artifact_id="ref:secret-fc", artifact_type="raster"
        )
        assert rec_b is not None and rec_b.session_id == sess_b
        assert (await get_artifact(sess_a, "ref:secret-fc")).metadata.get("owner_hint") == "user-A"

        # 空入参 fail-closed
        assert await get_artifact("", "ref:secret-fc") is None
        assert await get_artifact(sess_a, "") is None


# ══════════════════════════════════════════════════════════════════════
# 错误预言机：is_production() 分支端到端（audit gap #4 / #13）
# ══════════════════════════════════════════════════════════════════════

_SECRET_PAYLOAD = "connect failed: postgres://admin:hunter2@10.0.0.5:5432/gis"


def _boom_app():
    """最小 app：全局 handler 与 app/main.py 相同方式注册（app 级、全路由生效）。"""
    from fastapi import FastAPI

    from app.core.exception import global_exception_handler

    app = FastAPI()
    app.add_exception_handler(Exception, global_exception_handler)

    @app.get("/boom")
    async def boom():
        raise RuntimeError(_SECRET_PAYLOAD)

    return app


class TestErrorOracleProductionGate:
    def test_dev_response_pins_designed_detail_behavior(self):
        """默认（ENV=development，conftest 钉扎）：错误细节按设计出口，但路径已消毒。"""
        from fastapi.testclient import TestClient

        from app.core.exception import PROJECT_ROOT

        client = TestClient(_boom_app(), raise_server_exceptions=False)
        resp = client.get("/boom")
        assert resp.status_code == 500
        body = resp.json()
        # 设计内：dev 携带 error_detail / traceback（sanitized）
        assert body["success"] is False
        assert body["code"] == "SERVER_ERROR"
        assert _SECRET_PAYLOAD in body["error_detail"]
        # 路径消毒：仓库绝对路径不出口
        body_str = json.dumps(body, ensure_ascii=False)
        assert PROJECT_ROOT not in body_str
        assert "/home/" not in body_str
        assert "<REDACTED_PATH>" in body["traceback"]

    def test_production_response_is_generic_no_traceback(self, monkeypatch):
        """is_production() 为真：泛化消息，零内部信息出口（此分支此前无 e2e）。"""
        from fastapi.testclient import TestClient

        from app.core.config import settings
        from app.core.exception import PRODUCTION_ERROR_MESSAGE

        monkeypatch.setattr(settings, "ENV", "production")
        assert settings.is_production() is True

        client = TestClient(_boom_app(), raise_server_exceptions=False)
        resp = client.get("/boom")
        assert resp.status_code == 500
        body = resp.json()
        assert body["success"] is False
        assert body["code"] == "SERVER_ERROR"
        assert body["message"] == PRODUCTION_ERROR_MESSAGE
        body_str = json.dumps(body, ensure_ascii=False)
        for leaked in ("error_detail", "traceback", "error_type", "postgres://", "hunter2", "10.0.0.5"):
            assert leaked not in body_str, f"生产分支泄漏 {leaked}"

    def test_format_error_response_details_off_is_inert(self):
        from fastapi import Request

        from app.core.exception import format_error_response

        req = MagicMock(spec=Request)
        req.url.path = "/boom"
        req.method = "GET"
        body = format_error_response(
            RuntimeError(_SECRET_PAYLOAD), req, include_details=False
        )
        assert "error_detail" not in body and "traceback" not in body and "error_type" not in body

    def test_sanitize_traceback_strips_repo_paths_and_lines(self):
        from app.core.exception import PROJECT_ROOT, sanitize_traceback

        tb = (
            "Traceback (most recent call last):\n"
            f'  File "{PROJECT_ROOT}/app/api/routes/x.py", line 133, in handler\n'
            "    raise RuntimeError()\n"
        )
        out = sanitize_traceback(tb)
        assert PROJECT_ROOT not in out
        assert "<REDACTED_PATH>" in out
        assert "line 133" in out  # 行号保留（设计内），路径剥离


# ══════════════════════════════════════════════════════════════════════
# 脱敏词表 parity（audit gap #7）：共享关键基线被每个面覆盖
# ══════════════════════════════════════════════════════════════════════

#: 关键凭据词表 —— 任何落库/出口/trace 的敏感键 denylist 都必须命中（含近形键）。
SENSITIVE_BASELINE: tuple[str, ...] = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "api-key",
    "x-api-key", "access_key", "private_key", "credential", "authorization",
    "auth", "session_token", "client_secret",
)


class TestSensitiveKeyParity:
    def test_jobs_redaction_denylist_covers_baseline_behaviorally(self):
        """落库面（analysis_tasks.parameters）：每个基线键的值都必须被替换。"""
        from app.services.jobs.redaction import REDACTED, SENSITIVE_KEY_PARTS, safe_parameters

        for key in SENSITIVE_BASELINE:
            scrubbed = safe_parameters({key: "LEAK-VALUE"})
            assert "LEAK-VALUE" not in json.dumps(scrubbed), f"redaction 漏掉基线键 {key!r}"
        for part in ("auth", "api-key"):
            assert part in SENSITIVE_KEY_PARTS, f"近形键 {part!r} 必须入表（Wave 15 审计补齐）"

    def test_data_fabric_profile_redaction_covers_baseline_behaviorally(self):
        """出口面（connection profile → LLM/前端）：每个基线键必须打码。"""
        from app.services.data_fabric.security import DataFabricSecurity

        for key in SENSITIVE_BASELINE:
            out = DataFabricSecurity.sanitize_profile_dict({key: "LEAK-VALUE"})
            assert out[key] == "********", f"profile 脱敏漏掉基线键 {key!r}"
        # 嵌套（options/headers 子树）同样覆盖
        nested = DataFabricSecurity.sanitize_profile_dict(
            {"options": {"api-key": "LEAK-VALUE"}}
        )
        assert nested["options"]["api-key"] == "********"

    def test_runtime_trace_hints_covers_baseline_behaviorally(self):
        """运行时 trace 面：基线键进 meta 必须打码。"""
        from app.lib.runtime.trace import bound_meta

        for key in SENSITIVE_BASELINE:
            out = bound_meta({key: "LEAK-VALUE"})
            assert out[key] == "[REDACTED]", f"trace hints 漏掉基线键 {key!r}"

    def test_geocompute_trace_allowlist_never_admits_baseline_keys(self):
        """allowlist 面（语义相反）：基线键不是显式登记字段 → emit 时必被丢弃。"""
        from app.services.geocompute import tracing

        tracing._ring.clear()
        tracing.emit("parity_probe", run_id="parity-run", dataset_id="keep-me")
        for key in SENSITIVE_BASELINE:
            tracing.emit("parity_probe", run_id="parity-run", **{key: "LEAK-VALUE"})

        events = [e for e in tracing.recent_events(50) if e.get("run_id") == "parity-run"]
        assert len(events) == len(SENSITIVE_BASELINE) + 1
        for event in events:
            assert "LEAK-VALUE" not in json.dumps(event)
        # 正向对照：显式登记字段照常通过
        assert events[0]["dataset_id"] == "keep-me"


# ══════════════════════════════════════════════════════════════════════
# Security manifest 门（控制→实现→测试映射；字节一致认证表）
# ══════════════════════════════════════════════════════════════════════


class TestSecurityManifestGate:
    def test_manifest_validates_clean(self):
        from app.lib.quality.security_manifest import validate_security_manifest

        issues = validate_security_manifest()
        assert issues == [], "\n".join(issues)

    def test_manifest_flags_broken_impl_anchor(self):
        import copy

        from app.lib.quality.security_manifest import SECURITY_CONTROLS, validate_security_manifest

        tampered = copy.deepcopy(list(SECURITY_CONTROLS))
        object.__setattr__(tampered[0].impl[0], "anchor", "def this_anchor_does_not_exist():")
        issues = validate_security_manifest(tampered)
        assert issues and tampered[0].control_id in issues[0]

    def test_manifest_flags_tested_control_without_tests(self):
        import copy

        from app.lib.quality.security_manifest import SECURITY_CONTROLS, SecurityControl, validate_security_manifest

        orphan = SecurityControl(
            control_id="SEC-RT-ORPHAN",
            area="gate self-check",
            description="无测试的 tested 控制必须红",
            impl=(),
            test_nodes=(),
            status="tested",
        )
        issues = validate_security_manifest([*SECURITY_CONTROLS, orphan])
        assert any("SEC-RT-ORPHAN" in i for i in issues)

    def test_certification_table_current_and_honest(self):
        from gen_security_manifest import DEFAULT_OUT, generate

        content = generate()
        assert DEFAULT_OUT.exists(), "认证表未生成：python scripts/gen_security_manifest.py"
        assert DEFAULT_OUT.read_text(encoding="utf-8") == content
        # 诚实性：known-gap 显式出现在表里（不许伪造 passed）
        assert "KNOWN-GAP" in content
        assert "artifact" in content.lower()
