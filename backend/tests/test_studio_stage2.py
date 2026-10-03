"""Tests for Stage 2–5 studio services: presets, reframe geometry, thumbnails helpers."""

from app.services.export_presets import list_presets, get_preset, EXPORT_PRESETS
from app.services.reframe import target_crop_size, ASPECT_MAP


def test_export_presets_cover_major_platforms():
    presets = {p["id"] for p in list_presets()}
    for required in (
        "youtube_1080p",
        "youtube_shorts",
        "instagram_reels",
        "tiktok",
        "linkedin",
        "source",
    ):
        assert required in presets


def test_get_preset_shorts_is_vertical():
    shorts = get_preset("youtube_shorts")
    assert shorts.aspect_ratio == "9:16"
    assert shorts.width == 1080
    assert shorts.height == 1920
    assert shorts.max_duration_s == 60.0


def test_unknown_preset_raises():
    try:
        get_preset("does_not_exist")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_target_crop_size_9x16_from_landscape():
    w, h = target_crop_size(1920, 1080, ASPECT_MAP["9:16"])
    assert w % 2 == 0 and h % 2 == 0
    assert abs(w / h - 9 / 16) < 0.02
    assert w <= 1920 and h <= 1080


def test_target_crop_size_1x1():
    w, h = target_crop_size(1920, 1080, ASPECT_MAP["1:1"])
    assert w == h
    assert w <= 1080


def test_all_presets_have_even_dimensions_or_source():
    for preset in EXPORT_PRESETS.values():
        if preset.id == "source":
            continue
        assert preset.width % 2 == 0
        assert preset.height % 2 == 0


def test_pick_highlight_timestamps_spacing():
    from app.services.thumbnails import pick_highlight_timestamps

    scenes = [
        {"start_time": 0, "end_time": 10},
        {"start_time": 10, "end_time": 40},
        {"start_time": 40, "end_time": 55},
        {"start_time": 55, "end_time": 70},
    ]
    picks = pick_highlight_timestamps(scenes, count=3)
    assert len(picks) == 3
    assert all(isinstance(t, float) for t in picks)


def test_render_job_has_export_fields():
    from app.models.render_job import RenderJob

    assert hasattr(RenderJob, "preset_id")
    assert hasattr(RenderJob, "aspect_ratio")
    assert hasattr(RenderJob, "burn_captions")
    assert hasattr(RenderJob, "brand_kit_id")


def test_video_has_thumbnail_key():
    from app.models.video import Video

    assert hasattr(Video, "thumbnail_key")


def test_brand_kit_model():
    from app.models.brand_kit import BrandKit, GeneratedThumbnail

    assert BrandKit.__tablename__ == "brand_kits"
    assert GeneratedThumbnail.__tablename__ == "generated_thumbnails"


def test_studio_module_defines_endpoints():
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "studio.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    decorators = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                decorators.append(ast.unparse(dec) if hasattr(ast, "unparse") else "")
    joined = " ".join(decorators)
    assert "export-presets" in joined
    assert "brand-kits" in joined
    assert "translate" in joined
    assert "thumbnails" in joined


def test_export_options_schema_defaults():
    from app.schemas.render_job import ExportOptions

    opts = ExportOptions()
    assert opts.preset_id == "youtube_1080p"
    assert opts.burn_captions is True
    assert opts.prefer_faces is True
