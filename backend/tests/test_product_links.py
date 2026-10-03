"""Unit tests for timeline ↔ plan patch mapping (frontend-parity logic mirrored in Python)."""

from app.services.export_presets import get_preset
from app.services.reframe import target_crop_size, ASPECT_MAP


def test_list_render_jobs_route_exists():
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "edit_plans.py"
    text = src.read_text(encoding="utf-8")
    assert '/videos/{video_id}/render-jobs' in text
    assert "list_render_jobs" in text


def test_brand_logo_route_exists():
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "studio.py"
    text = src.read_text(encoding="utf-8")
    assert "/brand-kits/{kit_id}/logo" in text
    assert "upload_brand_logo" in text


def test_shorts_preset_for_export_modal_default():
    p = get_preset("youtube_shorts")
    assert p.aspect_ratio == "9:16"
    w, h = target_crop_size(1920, 1080, ASPECT_MAP["9:16"])
    assert w < h
