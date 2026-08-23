import sys
from unittest.mock import MagicMock, patch


def test_build_chunks_snaps_to_scene_boundary():
    from app.services.event_timeline import EventTimelineService
    svc = EventTimelineService()
    # Scene boundary at 22s — ideal cut at 20s, within snap range
    chunks = svc._build_chunks(60.0, [22.0, 40.0])
    assert chunks[0] == (0.0, 22.0)
    assert chunks[1][0] == 22.0


def test_build_chunks_no_boundary_uses_ideal():
    from app.services.event_timeline import EventTimelineService
    svc = EventTimelineService()
    chunks = svc._build_chunks(60.0, [])
    assert chunks[0] == (0.0, 20.0)


def test_build_chunks_last_chunk_reaches_end():
    from app.services.event_timeline import EventTimelineService
    svc = EventTimelineService()
    chunks = svc._build_chunks(25.0, [])
    assert chunks[-1][1] == 25.0


def test_merge_adjacent_same_tag_within_gap():
    from app.services.event_timeline import EventTimelineService
    svc = EventTimelineService()
    events = [
        {"chunk_id": 0, "start_ts": 0.0, "end_ts": 5.0, "description": "A", "tags": ["speaking"], "confidence": 0.9},
        {"chunk_id": 0, "start_ts": 5.5, "end_ts": 10.0, "description": "B", "tags": ["speaking"], "confidence": 0.8},
    ]
    merged = svc._merge_adjacent_events(events)
    assert len(merged) == 1
    assert merged[0]["end_ts"] == 10.0
    assert "speaking" in merged[0]["tags"]


def test_merge_adjacent_different_tag_not_merged():
    from app.services.event_timeline import EventTimelineService
    svc = EventTimelineService()
    events = [
        {"chunk_id": 0, "start_ts": 0.0, "end_ts": 5.0, "description": "A", "tags": ["speaking"], "confidence": 0.9},
        {"chunk_id": 1, "start_ts": 5.5, "end_ts": 10.0, "description": "B", "tags": ["action shot"], "confidence": 0.8},
    ]
    merged = svc._merge_adjacent_events(events)
    assert len(merged) == 2


def test_merge_adjacent_gap_too_large_not_merged():
    from app.services.event_timeline import EventTimelineService
    svc = EventTimelineService()
    events = [
        {"chunk_id": 0, "start_ts": 0.0, "end_ts": 5.0, "description": "A", "tags": ["speaking"], "confidence": 0.9},
        {"chunk_id": 1, "start_ts": 7.5, "end_ts": 12.0, "description": "B", "tags": ["speaking"], "confidence": 0.8},
    ]
    merged = svc._merge_adjacent_events(events)
    assert len(merged) == 2


def test_parse_events_offsets_to_absolute_time():
    from app.services.event_timeline import EventTimelineService
    svc = EventTimelineService()
    raw = {"events": [{"start": 2.0, "end": 5.0, "description": "demo", "tags": ["product demo"], "confidence": 0.85}]}
    events = svc._parse_events(raw, chunk_start=10.0, chunk_id=1)
    assert len(events) == 1
    assert events[0]["start_ts"] == 12.0
    assert events[0]["end_ts"] == 15.0
    assert events[0]["chunk_id"] == 1


def test_parse_events_skips_malformed():
    from app.services.event_timeline import EventTimelineService
    svc = EventTimelineService()
    raw = {"events": [{"no_start": True}]}
    events = svc._parse_events(raw, chunk_start=0.0, chunk_id=0)
    assert events == []


def test_extract_timeline_returns_empty_when_no_url():
    sys.modules.setdefault("openai", MagicMock())
    from app.services.event_timeline import EventTimelineService
    svc = EventTimelineService()
    with patch("app.services.event_timeline.settings") as mock_s:
        mock_s.vision_model_base_url = ""
        result = svc.extract_timeline("/fake.mp4", 60.0, [20.0, 40.0])
    assert result == []


def test_timeline_event_model_imports():
    from app.models.timeline import TimelineEvent
    assert hasattr(TimelineEvent, "__tablename__")
    assert TimelineEvent.__tablename__ == "timeline_events"
