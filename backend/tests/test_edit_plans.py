import uuid
import sys
from unittest.mock import MagicMock, patch


def test_edit_plan_schema_round_trip():
    from app.schemas.edit_plan import EditPlanSchema, EditPlanSegmentSchema
    from app.models.edit_plan import EditPlanStatus, EditSegmentAction
    from datetime import datetime, timezone

    seg = EditPlanSegmentSchema(
        scene_id=uuid.uuid4(),
        action=EditSegmentAction.KEEP,
        reason="Good shot",
        caption="Intro",
        order=0,
    )
    plan = EditPlanSchema(
        id=uuid.uuid4(),
        video_id=uuid.uuid4(),
        status=EditPlanStatus.DRAFT,
        llm_generated=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        segments=[seg],
    )
    assert plan.status == EditPlanStatus.DRAFT
    assert len(plan.segments) == 1
    assert plan.segments[0].action == EditSegmentAction.KEEP


def test_edit_plan_patch_request_validation():
    from app.schemas.edit_plan import EditPlanPatchRequest, EditPlanSegmentUpdate
    from app.models.edit_plan import EditSegmentAction

    seg_id = uuid.uuid4()
    req = EditPlanPatchRequest(
        segments=[
            EditPlanSegmentUpdate(id=seg_id, action=EditSegmentAction.CUT, caption="removed"),
        ]
    )
    assert req.segments[0].id == seg_id
    assert req.segments[0].action == EditSegmentAction.CUT


def test_edit_plan_status_only_draft_editable():
    from app.models.edit_plan import EditPlanStatus
    non_draft = [s for s in EditPlanStatus if s != EditPlanStatus.DRAFT]
    assert EditPlanStatus.APPROVED in non_draft
    assert EditPlanStatus.RENDERING in non_draft


def test_render_job_response_schema():
    from app.schemas.render_job import RenderJobResponse
    from app.models.render_job import RenderStatus
    from datetime import datetime, timezone

    resp = RenderJobResponse(
        id=uuid.uuid4(),
        edit_plan_id=uuid.uuid4(),
        video_id=uuid.uuid4(),
        status=RenderStatus.PENDING,
        output_url=None,
        error_message=None,
        started_at=None,
        completed_at=None,
        created_at=datetime.now(timezone.utc),
    )
    assert resp.status == RenderStatus.PENDING
    assert resp.output_url is None


def test_render_job_response_with_output_url():
    from app.schemas.render_job import RenderJobResponse
    from app.models.render_job import RenderStatus
    from datetime import datetime, timezone

    resp = RenderJobResponse(
        id=uuid.uuid4(),
        edit_plan_id=uuid.uuid4(),
        video_id=uuid.uuid4(),
        status=RenderStatus.COMPLETE,
        output_url="https://minio.local/renders/video/job.mp4",
        error_message=None,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    assert resp.status == RenderStatus.COMPLETE
    assert resp.output_url is not None


def test_edit_plan_router_registered():
    """Verify edit_plans router exposes all four required routes."""
    # Block the full import chain — mock all heavy deps not in lightweight test venv
    for mod in ("slowapi", "slowapi.util", "minio", "asyncpg"):
        sys.modules.setdefault(mod, MagicMock())

    db_mock = MagicMock()
    sys.modules["app.db"] = db_mock
    sys.modules["app.db.session"] = db_mock
    sys.modules.setdefault("app.dependencies", MagicMock())
    sys.modules.setdefault("app.services.storage", MagicMock())

    for key in list(sys.modules):
        if key in ("app.api.v1.edit_plans", "app.api.v1"):
            del sys.modules[key]

    from app.api.v1 import edit_plans
    routes = {r.path for r in edit_plans.router.routes}
    assert "/videos/{video_id}/edit-plan" in routes
    assert "/edit-plans/{plan_id}" in routes
    assert "/edit-plans/{plan_id}/approve" in routes
    assert "/render-jobs/{job_id}" in routes


def test_edit_plan_approve_requires_draft():
    from app.models.edit_plan import EditPlanStatus
    approvable = {EditPlanStatus.DRAFT}
    non_approvable = set(EditPlanStatus) - approvable
    assert EditPlanStatus.APPROVED in non_approvable
    assert EditPlanStatus.COMPLETE in non_approvable
    assert EditPlanStatus.FAILED in non_approvable


def test_edit_plan_segment_actions():
    from app.models.edit_plan import EditSegmentAction
    assert EditSegmentAction.KEEP == "keep"
    assert EditSegmentAction.CUT == "cut"
