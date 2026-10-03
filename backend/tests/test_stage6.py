"""Stage 6 unit tests: reframe keyframes, translation stubs, models."""

from app.services.reframe import target_crop_size, ASPECT_MAP, _smooth, _piecewise_expr, CropKeyframe
from app.services.translation import translate_segments


def test_smooth_moving_average():
    assert _smooth([1, 2, 3, 4, 5], window=3)[2] == 3.0


def test_piecewise_single_keyframe():
    expr = _piecewise_expr([CropKeyframe(0, 10, 20)], "x")
    assert expr == "10"


def test_piecewise_two_keyframes_contains_if():
    expr = _piecewise_expr(
        [CropKeyframe(0, 0, 0), CropKeyframe(2, 100, 50)],
        "x",
    )
    assert "if" in expr
    assert "100" in expr


def test_translate_stub_without_llm(monkeypatch):
    segs = [{"start": 0, "end": 1, "text": "Hello world"}]

    def fake_base():
        return "", "model"

    monkeypatch.setattr("app.services.translation._llm_base", fake_base)
    out, used = translate_segments(segs, "hi")
    assert used is False
    assert out[0].translated_text.startswith("[hi]")
    assert out[0].status == "stub"


def test_9x16_crop_geometry():
    w, h = target_crop_size(1920, 1080, ASPECT_MAP["9:16"])
    assert w < h
    assert abs(w / h - 9 / 16) < 0.05


def test_proxy_key_on_video_model():
    from app.models.video import Video
    assert hasattr(Video, "proxy_key")


def test_localization_job_model():
    from app.models.localization import LocalizationJob
    assert LocalizationJob.__tablename__ == "localization_jobs"


def test_tts_silence_helper(tmp_path):
    from app.services.tts import _write_silence_wav
    out = str(tmp_path / "t.wav")
    dur = _write_silence_wav(out, 0.5)
    assert dur > 0
    assert (tmp_path / "t.wav").stat().st_size > 0


def test_segment_effects_on_model():
    from app.models.edit_plan import EditPlanSegment
    for col in ("brightness", "contrast", "saturation", "fade_in", "fade_out", "effect"):
        assert hasattr(EditPlanSegment, col)


def test_segment_effects_schema_roundtrip():
    from uuid import uuid4
    from app.models.edit_plan import EditSegmentAction
    from app.schemas.edit_plan import EditPlanSegmentSchema, EditPlanSegmentUpdate

    seg = EditPlanSegmentSchema(
        id=uuid4(),
        scene_id=uuid4(),
        action=EditSegmentAction.KEEP,
        brightness=1.2,
        fade_in=0.5,
        effect="fade",
    )
    assert seg.brightness == 1.2
    assert seg.fade_in == 0.5

    upd = EditPlanSegmentUpdate(id=uuid4(), contrast=1.1, fade_out=0.3)
    assert upd.contrast == 1.1
    assert upd.fade_out == 0.3