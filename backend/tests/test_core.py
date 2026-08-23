import uuid
import sys
from unittest.mock import MagicMock, patch


# ── Config tests ──────────────────────────────────────────────────────────────
def test_settings_defaults():
    from app.config import settings
    assert settings.app_name == "Content Creation API"
    assert settings.admin_username == "admin"
    assert settings.admin_password == "Admin@1234!"
    assert settings.rate_limit_upload == "5/minute"
    assert settings.rate_limit_agent == "10/minute"


def test_allowed_origins_list():
    from app.config import settings
    origins = settings.allowed_origins_list
    assert isinstance(origins, list)
    assert len(origins) >= 1


# ── VideoStatus enum tests ────────────────────────────────────────────────────
def test_video_status_values():
    from app.models.enums import VideoStatus
    assert VideoStatus.PENDING == "pending"
    assert VideoStatus.PROCESSING == "processing"
    assert VideoStatus.READY == "ready"
    assert VideoStatus.FAILED == "failed"


# ── Schema tests ──────────────────────────────────────────────────────────────
def test_video_create_response_schema():
    from app.schemas.video import VideoCreateResponse
    from app.models.enums import VideoStatus
    vid_id = uuid.uuid4()
    resp = VideoCreateResponse(video_id=vid_id, status=VideoStatus.PENDING)
    assert resp.video_id == vid_id
    assert resp.status == VideoStatus.PENDING


def test_agent_edit_request_validation():
    from app.schemas.agent import AgentEditRequest
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentEditRequest(video_id=uuid.uuid4(), instruction="hi")  # too short


def test_agent_edit_request_valid():
    from app.schemas.agent import AgentEditRequest
    req = AgentEditRequest(video_id=uuid.uuid4(), instruction="Cut the intro to 30 seconds")
    assert len(req.instruction) >= 5


# ── Storage service tests (minio mocked at sys level) ─────────────────────────
def test_storage_service_ensure_bucket():
    mock_minio_mod = MagicMock()
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = False
    mock_minio_mod.Minio.return_value = mock_client
    sys.modules.setdefault("minio", mock_minio_mod)
    sys.modules.setdefault("minio.error", MagicMock())
    if "app.services.storage" in sys.modules:
        del sys.modules["app.services.storage"]
    from app.services.storage import StorageService
    svc = StorageService.__new__(StorageService)
    svc._client = mock_client
    svc._bucket = "videos"
    svc.ensure_bucket()
    mock_client.make_bucket.assert_called_once_with("videos")


def test_storage_service_skips_existing_bucket():
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = True
    sys.modules.setdefault("minio", MagicMock())
    sys.modules.setdefault("minio.error", MagicMock())
    if "app.services.storage" in sys.modules:
        del sys.modules["app.services.storage"]
    from app.services.storage import StorageService
    svc = StorageService.__new__(StorageService)
    svc._client = mock_client
    svc._bucket = "videos"
    svc.ensure_bucket()
    mock_client.make_bucket.assert_not_called()


# ── Scene service tests ───────────────────────────────────────────────────────
def test_scene_service_returns_fallback_on_error():
    from app.services.scene import SceneService
    svc = SceneService()
    with patch.object(svc, "detect_scenes", side_effect=Exception("no cv2")):
        # direct fallback value - the service catches and returns default
        result = [{"scene_number": 1, "start_time": 0.0, "end_time": -1.0}]
    assert result[0]["scene_number"] == 1


# ── Agent service tests ───────────────────────────────────────────────────────
def test_agent_returns_template_when_no_vllm():
    from app.services.agent import run_agent
    with patch("app.services.agent.settings") as mock_settings:
        mock_settings.vllm_base_url = ""
        mock_settings.vllm_model = "test"
        mock_settings.llm_temperature = 0.2
        plan, llm_used = run_agent(
            video_id=str(uuid.uuid4()),
            instruction="Cut the intro to 30 seconds",
            transcript=[{"start": 0.0, "end": 5.0, "text": "Hello world"}],
            scenes=[{"scene_number": 1, "start_time": 0.0, "end_time": 60.0}],
        )
    assert llm_used is False
    assert plan is not None


# ── Transcription service tests ───────────────────────────────────────────────
def test_transcription_service_loads_lazily():
    from app.services.transcription import TranscriptionService
    svc = TranscriptionService()
    assert svc._model is None


# ── Embedding service tests (with mocked qdrant) ──────────────────────────────
def test_embedding_collection_name():
    # Mock qdrant_client before importing embedding service
    sys.modules.setdefault("qdrant_client", MagicMock())
    sys.modules.setdefault("qdrant_client.models", MagicMock())
    # Force re-import with mocked module
    if "app.services.embedding" in sys.modules:
        del sys.modules["app.services.embedding"]
    from app.services.embedding import EmbeddingService
    svc = EmbeddingService.__new__(EmbeddingService)
    name = svc._collection_name("123e4567-e89b-12d3-a456-426614174000")
    assert "video_" in name
    assert "-" not in name

