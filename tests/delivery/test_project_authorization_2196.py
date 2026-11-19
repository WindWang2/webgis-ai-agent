"""#2196: publishing to a project requires live write authority, actor-scoped reuse."""
import pytest
from sqlalchemy import func, select

from app.models.db_model import AnalysisTask, Organization, User
from app.models.project import Artifact, Project
from app.schemas.data_delivery_schema import DeliverySourceRef, DeliverySpec
from app.services.data_delivery import service, store
from app.services.data_delivery.source_resolver import DeliveryActor
from tests.delivery.conftest import run_job_to_completion


@pytest.fixture()
def project_env(delivery_env):
    factory = delivery_env["sync_factory"]
    with factory() as db:
        db.add_all([Organization(id=1, name="One", slug="one"), Organization(id=2, name="Two", slug="two")])
        db.add_all([User(id="user-1", username="one", email="one@example.test", org_id=1, role="viewer", is_active=True), User(id="user-2", username="two", email="two@example.test", org_id=2, role="admin", is_active=True)])
        db.add_all([
            Project(id="own", name="Own", owner_id="user-1", org_id=1),
            Project(id="foreign", name="Foreign", owner_id="user-2", org_id=2),
            Project(id="same-org", name="Same org", owner_id="user-2", org_id=1),
            Project(id="private", name="Private", owner_id="user-2", org_id=None),
            Project(id="legacy", name="Public read-only", org_id=None, owner_id=None),
            Project(id="deleted", name="Deleted", owner_id="user-1", org_id=1, status="deleted"),
        ])
        db.commit()
    return factory


def _spec(ref):
    return DeliverySpec(source=DeliverySourceRef(kind="session_ref", session_id="sess-1", ref=ref), format="geojson", filename="auth-regression")


@pytest.mark.parametrize("project", ["foreign", "same-org", "private", "legacy", "deleted", "missing"])
async def test_unauthorized_target_rejected_before_job_or_publication(project_env, seeded_session, project):
    # Caller genuinely owns the source. Only the injected destination is forbidden.
    actor = DeliveryActor(user_id="user-1", org_id=1)
    spec = _spec(seeded_session["ref"])
    preview = await service.preview_delivery(spec, actor)
    try:
        result = await service.submit_delivery(spec, actor, project_id=project, acknowledged_losses=preview.required_acknowledgments)
    except service.DeliveryError as exc:
        assert exc.status_code == 404
        assert exc.error_code == service.dl_contracts.ERR_UNAUTHORIZED
    else:
        await run_job_to_completion(result["job_id"])
        pytest.fail("unauthorized project accepted a delivery job")
    with project_env() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisTask)) == 0
        assert db.scalar(select(func.count()).select_from(Artifact)) == 0


@pytest.mark.parametrize("target", ["own", "same-org"])
async def test_live_owner_or_same_org_editor_can_deliver_and_promote(project_env, seeded_session, target):
    actor = DeliveryActor(user_id="user-1", org_id=1)
    spec = _spec(seeded_session["ref"])
    preview = await service.preview_delivery(spec, actor)
    if target == "same-org":
        with project_env() as db:
            db.get(User, "user-1").role = "editor"
            db.commit()
    result = await service.submit_delivery(spec, actor, project_id=target, acknowledged_losses=preview.required_acknowledgments)
    await run_job_to_completion(result["job_id"])
    view = await service.get_delivery_job(result["job_id"], actor)
    assert view.status == "completed", view.error
    assert view.receipt.project_artifact_id
    with project_env() as db:
        assert db.get(Artifact, view.receipt.project_artifact_id).project_id == target


def test_execution_key_contains_actor_even_for_same_project_or_session():
    spec = _spec("ref:same")
    for target in (None, "own"):
        a = DeliveryActor(user_id="a", org_id=1)
        b = DeliveryActor(user_id="b", org_id=1)
        assert service.execution_key(spec, a, target) == service.execution_key(spec, a, target)
        assert service.execution_key(spec, a, target) != service.execution_key(spec, b, target)
        assert service.execution_key(spec, DeliveryActor(owner_token="token-a"), target) != service.execution_key(spec, DeliveryActor(owner_token="token-b"), target)


def test_direct_project_registration_rechecks_live_write_authority(project_env):
    # Lower-level promotion must not become a bypass if called outside submit.
    artifact = store.register_project_artifact("foreign", "delivery-test", "x", "geojson", None, 0, 0, None)
    assert artifact is None
    with project_env() as db:
        assert db.scalar(select(func.count()).select_from(Artifact)) == 0


async def test_project_permission_revoked_after_submit_prevents_promotion(project_env, seeded_session):
    actor = DeliveryActor(user_id="user-1", org_id=1)
    spec = _spec(seeded_session["ref"])
    preview = await service.preview_delivery(spec, actor)
    result = await service.submit_delivery(spec, actor, project_id="own", acknowledged_losses=preview.required_acknowledgments)
    with project_env() as db:
        target = db.get(Project, "own")
        target.owner_id, target.org_id = "user-2", 2
        db.commit()
    await run_job_to_completion(result["job_id"])
    view = await service.get_delivery_job(result["job_id"], actor)
    assert view.status == "failed"
    assert view.error_code == service.dl_contracts.ERR_UNAUTHORIZED
    with project_env() as db:
        assert db.scalar(select(func.count()).select_from(Artifact)) == 0


def test_direct_data_object_publication_requires_project_actor(project_env, monkeypatch):
    from unittest.mock import Mock
    from app.services.lakehouse import data_object
    delivery_id = store.new_delivery_id()
    final = store.final_dir(delivery_id)
    final.mkdir(parents=True, exist_ok=True)
    (final / "result.geojson").write_text("{}")
    publish = Mock(side_effect=AssertionError("must not publish unauthorized data"))
    monkeypatch.setattr(data_object, "publish_data_object", publish)
    result = store.publish_data_object_for_delivery(delivery_id, owner_scope={"project_id": "foreign"})
    assert result["state"] == "failed"
    publish.assert_not_called()
    result = store.publish_data_object_for_delivery(delivery_id, owner_scope={"project_id": "foreign"}, actor=DeliveryActor(user_id="user-1", org_id=1))
    assert result["state"] == "failed"
    publish.assert_not_called()
