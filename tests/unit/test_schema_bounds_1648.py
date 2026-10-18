"""#1648 请求 schema 无界字段一批：字段帽 + 语义帽回归锁。

覆盖 issue #1648 选项 A 第 1 步（schema 帽）与第 2 项
（ClarificationAnswerRequest 双空放行）：

- data_fabric / project / storymap / template / chat 请求体的
  ``max_length`` / ``max_items`` / 序列化字节帽（拒绝式，不静默截断）；
- geocompute plan 自由结构**不设运行时帽**（语义帽走 description 文档
  + 服务层 typed 413 PLAN_SNAPSHOT_TOO_LARGE 权威）—— 既有契约不漂移；
- 路由级：``POST /data-fabric/sources`` 超限字段在 schema 层 422，
  不进入业务层（探针/同步不被触发）。

全局 body 兜底（BodySizeLimitMiddleware）由
tests/unit/core/test_body_limit.py 覆盖。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas.chat_schema import ChatRequest, ClarificationAnswerRequest
from app.schemas.data_fabric_schema import (
    CreateDataSourceRequest,
    MaterializeRequest,
)
from app.schemas.geocompute_schema import ExecutionNodeIn, ExecutionPlanIn
from app.schemas.project_schema import (
    RunReplayRequest,
    WorkflowRerunRequest,
    WorkflowRunRequest,
)
from app.schemas.storymap_schema import StoryExportRequest
from app.schemas.template_schema import (
    CartographyTemplateCreate,
    CreateTemplateRequest,
)


def _oversized_dict_bytes(n: int) -> dict:
    """构造约 n 字节序列化长度的自由结构 dict（值域语义无关，只测帽）。"""
    return {"blob": "x" * n}


# ── data_fabric ──────────────────────────────────────────────────────


class TestCreateDataSourceRequestBounds:
    def test_valid_minimal_passes(self):
        req = CreateDataSourceRequest(
            name="城市 poi 库", source_type="postgis",
            endpoint_url="postgresql://host/db", options={},
        )
        assert req.name == "城市 poi 库"

    @pytest.mark.parametrize("field,value", [
        ("name", "A" * 256),
        ("source_type", "x" * 65),
        ("endpoint_url", "u" * 2049),
    ])
    def test_string_fields_over_limit_422(self, field, value):
        with pytest.raises(ValidationError):
            CreateDataSourceRequest(**{
                "name": "n", "source_type": "postgis",
                "endpoint_url": "u", field: value,
            })

    def test_options_over_byte_cap_422(self):
        with pytest.raises(ValidationError):
            CreateDataSourceRequest(
                name="n", source_type="postgis", endpoint_url="u",
                options=_oversized_dict_bytes(64 * 1024),
            )

    def test_options_at_cap_passes(self):
        req = CreateDataSourceRequest(
            name="n", source_type="postgis", endpoint_url="u",
            options=_oversized_dict_bytes(64 * 1024 - 1024),
        )
        assert "blob" in req.options


class TestMaterializeRequestBounds:
    @pytest.mark.parametrize("field", ["session_id", "catalog_item_id"])
    def test_id_fields_over_limit_422(self, field):
        with pytest.raises(ValidationError):
            MaterializeRequest(**{field: "x" * 129})


# ── geocompute（语义帽文档化，运行时权威在服务层 typed 413）──────────


class TestGeocomputePlanSemanticCap:
    def test_dict_fields_document_plan_byte_budget(self):
        for field in ("parameters", "retry"):
            desc = ExecutionNodeIn.model_fields[field].description or ""
            assert "256KB" in desc and "MAX_PLAN_SNAPSHOT_BYTES" in desc
        budget_desc = ExecutionPlanIn.model_fields["budget"].description or ""
        assert "256KB" in budget_desc and "MAX_PLAN_SNAPSHOT_BYTES" in budget_desc

    def test_large_inline_parameters_still_validate_at_schema_layer(self):
        """大 parameters（>256KB）在 schema 层放行 —— 服务层 typed 413
        （PLAN_SNAPSHOT_TOO_LARGE）保持既有权威，不被提前成 422。"""
        node = ExecutionNodeIn(
            node_id="scan", category="source_scan",
            parameters={"features": [
                {"type": "Feature", "properties": {"blob": "x" * 2048}}
                for _ in range(200)
            ]},
        )
        plan = ExecutionPlanIn(plan_id="p", nodes=[node])
        assert plan.nodes[0].parameters["features"]


# ── project ─────────────────────────────────────────────────────────


class TestWorkflowRunRequestBounds:
    def test_valid_passes(self):
        req = WorkflowRunRequest(input_bindings={"aoi": "Haidian"},
                                 start_from_step="s1")
        assert req.start_from_step == "s1"

    def test_start_from_step_over_limit_422(self):
        with pytest.raises(ValidationError):
            WorkflowRunRequest(start_from_step="s" * 129)

    def test_input_bindings_over_byte_cap_422(self):
        with pytest.raises(ValidationError):
            WorkflowRunRequest(input_bindings=_oversized_dict_bytes(256 * 1024))


class TestWorkflowRerunRequestBounds:
    def test_from_step_over_limit_422(self):
        with pytest.raises(ValidationError):
            WorkflowRerunRequest(from_step="s" * 129)

    def test_input_bindings_over_byte_cap_422(self):
        with pytest.raises(ValidationError):
            WorkflowRerunRequest(input_bindings=_oversized_dict_bytes(256 * 1024))


class TestRunReplayRequestBounds:
    def test_mode_over_limit_422(self):
        with pytest.raises(ValidationError):
            RunReplayRequest(mode="m" * 33)

    def test_documented_modes_pass(self):
        assert RunReplayRequest(mode="exact").mode == "exact"
        assert RunReplayRequest(mode="latest").mode == "latest"


# ── storymap ────────────────────────────────────────────────────────


class TestStoryExportRequestBounds:
    def test_layers_over_items_cap_422(self):
        with pytest.raises(ValidationError):
            StoryExportRequest(spec={"chapters": []},
                               layers=[{"id": i} for i in range(65)])

    def test_layers_at_cap_passes(self):
        req = StoryExportRequest(spec={"chapters": []},
                                 layers=[{"id": i} for i in range(64)])
        assert len(req.layers) == 64


# ── template ────────────────────────────────────────────────────────


class TestTemplateRequestBounds:
    def test_create_valid_passes(self):
        req = CreateTemplateRequest(
            name="我的底图", kind="basemap",
            keywords=["dark", "basemap"], payload={"providerId": "dark"},
        )
        assert req.keywords == ["dark", "basemap"]

    def test_keywords_over_items_cap_422(self):
        with pytest.raises(ValidationError):
            CreateTemplateRequest(
                name="n", kind="basemap", payload={},
                keywords=[f"k{i}" for i in range(33)],
            )

    def test_single_keyword_over_length_422(self):
        with pytest.raises(ValidationError):
            CreateTemplateRequest(
                name="n", kind="basemap", payload={}, keywords=["k" * 65],
            )

    def test_payload_over_byte_cap_422(self):
        with pytest.raises(ValidationError):
            CreateTemplateRequest(
                name="n", kind="basemap", keywords=[],
                payload=_oversized_dict_bytes(64 * 1024),
            )

    def test_cartography_template_create_payload_over_byte_cap_422(self):
        with pytest.raises(ValidationError):
            CartographyTemplateCreate(
                name="n", kind="basemap", keywords=[],
                payload=_oversized_dict_bytes(64 * 1024),
            )


# ── chat ────────────────────────────────────────────────────────────


class TestChatRequestBounds:
    @pytest.mark.parametrize("field", ["skill_name", "project_id"])
    def test_optional_keys_over_limit_422(self, field):
        with pytest.raises(ValidationError):
            ChatRequest(message="对这份数据做缓冲区分析", **{field: "x" * 129})

    def test_valid_passes(self):
        req = ChatRequest(message="你好", skill_name="buffer", project_id="p1")
        assert req.skill_name == "buffer"


# ── ClarificationAnswerRequest 双空放行 ─────────────────────────────


class TestClarificationAnswerRequestBounds:
    def test_double_empty_rejected_at_schema_layer(self):
        with pytest.raises(ValidationError):
            ClarificationAnswerRequest(request_id="x", answers=[], waive=[])

    def test_answers_only_passes(self):
        req = ClarificationAnswerRequest(
            request_id="x",
            answers=[{"context_key": "aoi.name", "value": "成都"}],
        )
        assert req.answers

    def test_waive_only_passes(self):
        req = ClarificationAnswerRequest(request_id="x", waive=["aoi.name"])
        assert req.waive == ["aoi.name"]


# ── 路由级：schema 层 422，不进入业务层 ─────────────────────────────


class TestDataSourceRouteSchemaRejection:
    """超限字段在解析期 422 —— 探针/同步/落库均不触发（无 DB 依赖）。"""

    @pytest.fixture
    def auth_headers(self):
        from app.core.auth import create_access_token
        token = create_access_token({"sub": "sb-1648-user", "role": "editor"})
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.parametrize("payload", [
        {"name": "A" * 256, "source_type": "postgis",
         "endpoint_url": "postgresql://host/db"},
        {"name": "n", "source_type": "x" * 65,
         "endpoint_url": "postgresql://host/db"},
        {"name": "n", "source_type": "postgis",
         "endpoint_url": "u" * 2049},
        {"name": "n", "source_type": "postgis", "endpoint_url": "u",
         "options": _oversized_dict_bytes(64 * 1024)},
    ])
    def test_over_limit_fields_422(self, auth_headers, payload):
        from app.main import app

        resp = TestClient(app).post(
            "/api/v1/data-fabric/sources", json=payload, headers=auth_headers)
        assert resp.status_code == 422, resp.text
