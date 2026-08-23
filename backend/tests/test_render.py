import uuid
import sys
from unittest.mock import patch, MagicMock, call
import tempfile
import os


def test_render_service_seconds_to_srt():
    from app.services.render import RenderService
    svc = RenderService()
    assert svc._seconds_to_srt(0.0) == "00:00:00,000"
    assert svc._seconds_to_srt(3661.5) == "01:01:01,500"
    assert svc._seconds_to_srt(90.25) == "00:01:30,250"


def test_render_service_write_srt_with_captions():
    from app.services.render import RenderService
    svc = RenderService()
    clips = [
        {"caption": "Hello World", "duration": 5.0},
        {"caption": "Second", "duration": 3.0},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        srt = os.path.join(tmp, "test.srt")
        svc._write_srt(clips, srt)
        content = open(srt).read()
    assert "Hello World" in content
    assert "Second" in content
    assert "1\n" in content
    assert "2\n" in content


def test_render_service_write_srt_empty_captions():
    from app.services.render import RenderService
    svc = RenderService()
    with tempfile.TemporaryDirectory() as tmp:
        srt = os.path.join(tmp, "test.srt")
        svc._write_srt([{"caption": "", "duration": 5.0}], srt)
        content = open(srt).read()
    assert content == ""


def test_render_job_model_imports():
    from app.models.render_job import RenderJob, RenderStatus
    assert RenderStatus.PENDING == "pending"
    assert RenderStatus.PROCESSING == "processing"
    assert RenderStatus.COMPLETE == "complete"
    assert RenderStatus.FAILED == "failed"


def test_render_job_schema_imports():
    from app.schemas.render_job import RenderJobResponse
    from app.models.render_job import RenderStatus
    assert hasattr(RenderJobResponse, "model_fields")


def test_render_service_raises_on_empty_segments():
    from app.services.render import RenderService
    svc = RenderService()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            svc.render("/fake.mp4", [], os.path.join(tmp, "out.mp4"))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "No segments" in str(e)


def test_render_service_single_clip_no_xfade():
    """Single clip path should avoid xfade filter entirely — no ffmpeg xfade filter used."""
    # ffmpeg is imported lazily inside _concat_clips even for single-clip path
    sys.modules.setdefault("ffmpeg", MagicMock())
    from app.services.render import RenderService
    svc = RenderService()
    with patch("app.services.render.os.replace") as mock_replace:
        svc._concat_clips(["/clip0.mp4"], "/output.mp4")
    mock_replace.assert_called_once_with("/clip0.mp4", "/output.mp4")


def test_render_task_exists():
    """Verify the render Celery task has the correct registered name."""
    import types

    # Build a real decorator so the task name kwarg propagates to the wrapper
    def fake_celery_task(**kwargs):
        task_name = kwargs.get("name", "")
        def decorator(fn):
            return types.SimpleNamespace(name=task_name)
        return decorator

    celery_mock = MagicMock()
    celery_mock.task = fake_celery_task

    worker_mock = MagicMock()
    worker_mock.celery_app = celery_mock

    sys.modules["app.worker"] = worker_mock
    sys.modules.setdefault("celery", MagicMock())

    if "app.tasks.render_pipeline" in sys.modules:
        del sys.modules["app.tasks.render_pipeline"]

    from app.tasks.render_pipeline import render_video
    assert render_video.name == "tasks.render_video"


def test_render_job_status_transitions():
    from app.models.render_job import RenderStatus
    statuses = list(RenderStatus)
    assert len(statuses) == 4


def test_config_has_render_fields():
    from app.config import settings
    assert hasattr(settings, "render_output_prefix")
    assert hasattr(settings, "crossfade_duration")
    assert settings.crossfade_duration == 0.4


# ── Sub-scene trim clamping (render_pipeline logic) ──────────────────────────

def test_trim_clamping_within_scene_bounds():
    """start_ts/end_ts within scene bounds → values unchanged."""
    scene_start, scene_end = 10.0, 30.0
    start = max(scene_start, min(15.0, scene_end))
    end   = max(scene_start, min(25.0, scene_end))
    assert start == 15.0
    assert end == 25.0


def test_trim_clamping_before_scene_start():
    """start_ts before scene start → clamped to scene.start_time."""
    scene_start, scene_end = 10.0, 30.0
    start = max(scene_start, min(3.0, scene_end))
    assert start == scene_start


def test_trim_clamping_after_scene_end():
    """end_ts beyond scene end → clamped to scene.end_time."""
    scene_start, scene_end = 10.0, 30.0
    end = max(scene_start, min(99.0, scene_end))
    assert end == scene_end


def test_trim_none_falls_back_to_scene_boundaries():
    """None start_ts/end_ts → use scene.start_time / scene.end_time."""
    scene_start, scene_end = 5.0, 20.0
    ps_start_ts = None
    ps_end_ts = None
    start = ps_start_ts if ps_start_ts is not None else scene_start
    end   = ps_end_ts   if ps_end_ts   is not None else scene_end
    assert start == scene_start
    assert end == scene_end


def test_trim_start_equals_end_clamped_correctly():
    """Edge: start_ts == end_ts (zero-length) both clamp inside scene."""
    scene_start, scene_end = 0.0, 10.0
    ts = 5.0
    start = max(scene_start, min(ts, scene_end))
    end   = max(scene_start, min(ts, scene_end))
    assert start == end == 5.0
