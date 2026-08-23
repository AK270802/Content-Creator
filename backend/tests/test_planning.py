import uuid
import sys
from unittest.mock import patch, MagicMock


def _mock_scene(scene_id=None, scene_number=1, start=0.0, end=10.0,
                description="A test scene", visual_tags=None, quality_flags=None):
    sc = MagicMock()
    sc.id = scene_id or uuid.uuid4()
    sc.scene_number = scene_number
    sc.start_time = start
    sc.end_time = end
    sc.description = description
    sc.visual_tags = visual_tags or ["outdoor"]
    sc.quality_flags = quality_flags or {"static": False, "blurry": False}
    return sc


def _mock_segment(start=0.0, end=5.0, text="Hello world"):
    seg = MagicMock()
    seg.start_time = start
    seg.end_time = end
    seg.text = text
    return seg


# ── Template plan tests ───────────────────────────────────────────────────────
def test_planning_service_template_keeps_good_scenes():
    from app.services.planning import PlanningService
    from app.models.edit_plan import EditSegmentAction
    svc = PlanningService()
    scenes = [
        _mock_scene(scene_number=1, quality_flags={"static": False, "blurry": False}),
        _mock_scene(scene_number=2, quality_flags={"static": True, "blurry": False}),
    ]
    with patch("app.services.planning.settings") as ms:
        ms.planning_model_base_url = ""
        result = svc._template_plan("fake-id", scenes)
    kept = [s for s in result.segments if s.action == EditSegmentAction.KEEP]
    cut = [s for s in result.segments if s.action == EditSegmentAction.CUT]
    assert len(kept) == 1
    assert len(cut) == 1


def test_planning_service_template_cuts_blurry():
    from app.services.planning import PlanningService
    from app.models.edit_plan import EditSegmentAction
    svc = PlanningService()
    scenes = [_mock_scene(quality_flags={"blurry": True, "static": False})]
    with patch("app.services.planning.settings") as ms:
        ms.planning_model_base_url = ""
        result = svc._template_plan("fake-id", scenes)
    assert result.segments[0].action == EditSegmentAction.CUT


def test_planning_service_returns_template_when_no_llm():
    from app.services.planning import PlanningService
    svc = PlanningService()
    scenes = [_mock_scene()]
    segs = [_mock_segment()]
    with patch("app.services.planning.settings") as ms:
        ms.planning_model_base_url = ""
        ms.planning_model_name = "mistral"
        ms.planning_temperature = 0.1
        result = svc.generate_plan("fake-video-id", scenes, segs)
    assert len(result.segments) == 1


def test_planning_service_retry_on_validation_failure():
    from app.services.planning import PlanningService, PlanningError
    from pydantic import ValidationError
    svc = PlanningService()
    scenes = [_mock_scene()]
    segs = [_mock_segment()]
    with patch("app.services.planning.settings") as ms:
        ms.planning_model_base_url = "http://fake:11434"
        ms.planning_model_name = "mistral"
        ms.planning_temperature = 0.1
        # Both calls return invalid data
        with patch.object(svc, "_call_llm", side_effect=[
            {"segments": [{"scene_id": "not-a-uuid", "action": "keep"}]},
            Exception("still broken"),
        ]):
            try:
                svc.generate_plan("fake-id", scenes, segs)
                assert False, "Should have raised"
            except PlanningError:
                pass  # expected


def test_planning_service_retry_succeeds_on_second_attempt():
    from app.services.planning import PlanningService
    from app.schemas.edit_plan import LLMPlan
    svc = PlanningService()
    scene_id = uuid.uuid4()
    scenes = [_mock_scene(scene_id=scene_id)]
    segs = [_mock_segment()]
    valid_response = {"segments": [
        {"scene_id": str(scene_id), "action": "keep", "reason": "good", "caption": "Hi", "order": 0}
    ]}
    with patch("app.services.planning.settings") as ms:
        ms.planning_model_base_url = "http://fake:11434"
        ms.planning_model_name = "mistral"
        ms.planning_temperature = 0.1
        with patch.object(svc, "_call_llm", side_effect=[
            Exception("timeout"),
            valid_response,
        ]):
            result = svc.generate_plan("fake-id", scenes, segs)
    assert result.segments[0].action.value == "keep"


def test_llm_plan_schema_validates_correctly():
    from app.schemas.edit_plan import LLMPlan
    scene_id = uuid.uuid4()
    plan = LLMPlan(segments=[
        {"scene_id": str(scene_id), "action": "keep", "caption": "Hello", "order": 1}
    ])
    assert plan.segments[0].order == 1


def test_edit_plan_model_imports():
    from app.models.edit_plan import EditPlan, EditPlanSegment, EditPlanStatus, EditSegmentAction
    assert EditPlanStatus.DRAFT == "draft"
    assert EditSegmentAction.KEEP == "keep"
    assert EditSegmentAction.CUT == "cut"


def test_edit_plan_status_transitions():
    from app.models.edit_plan import EditPlanStatus
    statuses = list(EditPlanStatus)
    assert EditPlanStatus.APPROVED in statuses
    assert EditPlanStatus.RENDERING in statuses
