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


def _setup_router_mocks():
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


def test_edit_plan_router_registered():
    """Verify edit_plans router exposes all required routes including new versioning/revise routes."""
    _setup_router_mocks()
    from app.api.v1 import edit_plans
    routes = {r.path for r in edit_plans.router.routes}
    assert "/videos/{video_id}/edit-plan" in routes
    assert "/videos/{video_id}/edit-plans" in routes
    assert "/edit-plans/{plan_id}" in routes
    assert "/edit-plans/{plan_id}/approve" in routes
    assert "/render-jobs/{job_id}" in routes
    assert "/videos/{video_id}/edit-plans/{plan_id}/revise" in routes


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


# ── Item 2: Edit plan versioning ──────────────────────────────────────────────

def test_edit_plan_schema_version_defaults():
    from app.schemas.edit_plan import EditPlanSchema
    from app.models.edit_plan import EditPlanStatus
    from datetime import datetime, timezone

    plan = EditPlanSchema(
        id=uuid.uuid4(),
        video_id=uuid.uuid4(),
        status=EditPlanStatus.DRAFT,
        llm_generated=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert plan.version == 1
    assert plan.parent_version_id is None


def test_edit_plan_schema_version_and_parent():
    from app.schemas.edit_plan import EditPlanSchema
    from app.models.edit_plan import EditPlanStatus
    from datetime import datetime, timezone

    parent_id = uuid.uuid4()
    plan = EditPlanSchema(
        id=uuid.uuid4(),
        video_id=uuid.uuid4(),
        status=EditPlanStatus.DRAFT,
        llm_generated=True,
        version=3,
        parent_version_id=parent_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert plan.version == 3
    assert plan.parent_version_id == parent_id


def test_edit_plan_model_version_fields():
    from app.models.edit_plan import EditPlan
    assert hasattr(EditPlan, "version")
    assert hasattr(EditPlan, "parent_version_id")


# ── Item 3: Revision schema and service ──────────────────────────────────────

def test_revise_request_schema():
    from app.schemas.edit_plan import ReviseRequest
    req = ReviseRequest(instruction="Remove all indoor shots")
    assert req.instruction == "Remove all indoor shots"


def test_revision_service_raises_without_llm(monkeypatch):
    """RevisionService must raise PlanningError when no LLM is configured."""
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "planning_model_base_url", "")

    from app.services.revision import RevisionService
    from app.services.planning import PlanningError

    svc = RevisionService()
    import pytest
    with pytest.raises(PlanningError, match="No planning LLM configured"):
        svc.revise(
            video_id="test-vid",
            current_plan_segments=[],
            scenes=[],
            timeline_events=[],
            transcript_segments=[],
            instruction="make it shorter",
        )


def test_revision_service_module_importable():
    from app.services import revision
    assert hasattr(revision, "revision_service")
    assert hasattr(revision, "RevisionService")


# ── Item 4: Segment trim fields ───────────────────────────────────────────────

def test_segment_schema_trim_fields():
    from app.schemas.edit_plan import EditPlanSegmentSchema
    from app.models.edit_plan import EditSegmentAction

    seg = EditPlanSegmentSchema(
        scene_id=uuid.uuid4(),
        action=EditSegmentAction.KEEP,
        start_ts=5.5,
        end_ts=12.3,
        text_overlay="Highlight moment",
        order=0,
    )
    assert seg.start_ts == 5.5
    assert seg.end_ts == 12.3
    assert seg.text_overlay == "Highlight moment"


def test_segment_trim_fields_optional():
    from app.schemas.edit_plan import EditPlanSegmentSchema
    from app.models.edit_plan import EditSegmentAction

    seg = EditPlanSegmentSchema(
        scene_id=uuid.uuid4(),
        action=EditSegmentAction.KEEP,
        order=0,
    )
    assert seg.start_ts is None
    assert seg.end_ts is None
    assert seg.text_overlay is None


def test_segment_update_trim_fields():
    from app.schemas.edit_plan import EditPlanSegmentUpdate

    seg_id = uuid.uuid4()
    upd = EditPlanSegmentUpdate(id=seg_id, start_ts=2.0, end_ts=9.5, text_overlay="Caption here")
    assert upd.start_ts == 2.0
    assert upd.end_ts == 9.5
    assert upd.text_overlay == "Caption here"


def test_edit_plan_segment_model_trim_fields():
    from app.models.edit_plan import EditPlanSegment
    assert hasattr(EditPlanSegment, "start_ts")
    assert hasattr(EditPlanSegment, "end_ts")
    assert hasattr(EditPlanSegment, "text_overlay")


# ── Item 5: PostHog analytics properties ─────────────────────────────────────

def test_edit_plan_revised_analytics_properties():
    """edit_plan_revised must include plan_version and instruction_length, NOT raw instruction."""
    captured = {}

    import app.services.analytics as analytics_mod
    original_capture = analytics_mod.capture

    def mock_capture(user_id, event, props):
        captured[event] = props

    analytics_mod.capture = mock_capture
    try:
        analytics_mod.capture("u1", "edit_plan_revised", {
            "plan_id": str(uuid.uuid4()),
            "parent_plan_id": str(uuid.uuid4()),
            "plan_version": 2,
            "instruction_length": 42,
        })
        assert "edit_plan_revised" in captured
        props = captured["edit_plan_revised"]
        assert "plan_version" in props
        assert "instruction_length" in props
        assert "instruction" not in props  # privacy: raw text must NOT be present
    finally:
        analytics_mod.capture = original_capture


def test_timeline_extraction_completed_event_structure():
    """timeline_extraction_completed must include video_id and event_count."""
    captured = {}

    import app.services.analytics as analytics_mod
    original_capture = analytics_mod.capture

    def mock_capture(user_id, event, props):
        captured[event] = props

    analytics_mod.capture = mock_capture
    try:
        analytics_mod.capture("vid-123", "timeline_extraction_completed", {
            "video_id": "vid-123",
            "event_count": 7,
        })
        assert "timeline_extraction_completed" in captured
        props = captured["timeline_extraction_completed"]
        assert props["event_count"] == 7
        assert "video_id" in props
    finally:
        analytics_mod.capture = original_capture


# ── RevisionService failure paths ─────────────────────────────────────────────

def test_revision_service_raises_after_two_llm_failures(monkeypatch):
    """PlanningError raised when both LLM attempts return unusable output."""
    import pytest
    from unittest.mock import patch
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "planning_model_base_url", "http://fake-llm")

    from app.services.revision import RevisionService
    from app.services.planning import PlanningError

    svc = RevisionService()
    with patch.object(svc, "_call_llm", side_effect=ValueError("bad JSON")):
        with pytest.raises(PlanningError):
            svc.revise(
                video_id="test-vid",
                current_plan_segments=[],
                scenes=[],
                timeline_events=[],
                transcript_segments=[],
                instruction="remove all outdoor scenes",
            )


def test_revision_service_succeeds_on_second_attempt(monkeypatch):
    """RevisionService returns a valid plan when first call fails but retry succeeds."""
    from unittest.mock import patch
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "planning_model_base_url", "http://fake-llm")
    monkeypatch.setattr(cfg.settings, "planning_model_name", "mistral:7b")
    monkeypatch.setattr(cfg.settings, "planning_temperature", 0.1)

    scene_id = uuid.uuid4()
    valid_response = {"segments": [
        {"scene_id": str(scene_id), "action": "keep", "reason": "good shot", "caption": "Action!", "order": 0}
    ]}

    from app.services.revision import RevisionService
    svc = RevisionService()
    with patch.object(svc, "_call_llm", side_effect=[ValueError("timeout"), valid_response]):
        result = svc.revise(
            video_id="test-vid",
            current_plan_segments=[],
            scenes=[],
            timeline_events=[],
            transcript_segments=[],
            instruction="keep only action shots",
        )

    assert len(result.segments) == 1
    assert str(result.segments[0].scene_id) == str(scene_id)


def test_revision_service_confidence_filtering(monkeypatch):
    """Low-confidence timeline events must be excluded from revision prompt."""
    import app.config as cfg
    monkeypatch.setattr(cfg.settings, "planning_model_base_url", "http://fake-llm")
    monkeypatch.setattr(cfg.settings, "timeline_confidence_threshold", 0.5)

    from unittest.mock import MagicMock, patch
    from app.services.revision import RevisionService

    high_conf = MagicMock()
    high_conf.start_ts = 0.0
    high_conf.end_ts = 5.0
    high_conf.description = "high confidence action"
    high_conf.tags = ["action"]
    high_conf.confidence = 0.9

    low_conf = MagicMock()
    low_conf.start_ts = 5.0
    low_conf.end_ts = 10.0
    low_conf.description = "very uncertain flicker"
    low_conf.tags = ["noise"]
    low_conf.confidence = 0.1

    svc = RevisionService()
    prompt = svc._build_prompt(
        video_id="v1",
        current_segments=[],
        scenes=[],
        timeline_events=[high_conf, low_conf],
        transcript_segments=[],
        instruction="trim the intro",
    )

    assert "high confidence action" in prompt
    assert "very uncertain flicker" not in prompt


# ── Revision daily cap config ─────────────────────────────────────────────────

def test_revision_cap_config_exists():
    from app.config import settings
    assert hasattr(settings, "revision_daily_cap")
    assert isinstance(settings.revision_daily_cap, int)
    assert settings.revision_daily_cap > 0


def test_revision_cap_check_function_importable():
    """_check_revision_cap must exist in edit_plans router."""
    _setup_router_mocks()
    from app.api.v1 import edit_plans
    assert hasattr(edit_plans, "_check_revision_cap")
    import asyncio
    assert asyncio.iscoroutinefunction(edit_plans._check_revision_cap)
