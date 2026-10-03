# Plan: AI-Powered Content Creation Platform

## Summary
Build a FastAPI + Celery backend for an AI-powered video content creation platform. The system ingests raw video, runs scene detection and speech transcription, stores embeddings in Qdrant, and exposes REST APIs for a frontend editor. It is backed by Postgres (metadata), Redis (task queue), MinIO (objects), Qdrant (vectors), and Keycloak (auth).

## User Story
As a content creator, I want to upload a video and have it automatically transcribed, scene-segmented, and ready for AI-assisted editing, so that I can produce polished content faster.

## Problem → Solution
Empty project directory with infra config only → Fully structured FastAPI + Celery application with video ingestion, transcription, scene detection, and agent-orchestrated editing pipeline.

## Metadata
- **Complexity**: XL
- **Source PRD**: N/A (standalone)
- **PRD Phase**: N/A
- **Estimated Files**: 40+

---

## UX Design

### Before
```
No backend exists. Infra (docker-compose) defined but no app code.
```

### After
```
POST /api/v1/videos/upload        → MinIO upload → Celery task queued
GET  /api/v1/videos/{id}/status   → processing | ready | failed
GET  /api/v1/videos/{id}/transcript → timestamped segments
GET  /api/v1/videos/{id}/scenes   → scene boundaries + thumbnail URLs
POST /api/v1/agent/edit           → LangGraph agent → edit plan JSON
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Upload | N/A | Multipart POST, returns job_id | Async, <200ms |
| Status poll | N/A | GET /status | Celery state reflected |
| Transcript | N/A | Paginated JSON with word timestamps | From Qdrant |
| Scene list | N/A | JSON array with thumbnail URLs | Pre-signed MinIO URLs |
| Agent edit | N/A | Natural-language to edit-plan | LangGraph pipeline |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `docker-compose.yml` | all | Service names, ports, env var names |
| P0 | `requirements.txt` | all | Exact package versions |
| P0 | `prometheus.yml` | all | Metrics endpoint expected at /metrics |
| P1 | `.env` (create) | all | Runtime secrets |

---

## Patterns to Mirror

### DIRECTORY_LAYOUT
```
backend/
  app/
    main.py              # FastAPI app factory
    config.py            # pydantic-settings BaseSettings
    dependencies.py      # FastAPI Depends factories
    worker.py            # Celery app instance
    api/v1/
      router.py
      videos.py
      agent.py
      health.py
    models/
      video.py           # SQLAlchemy ORM models
      enums.py
    schemas/
      video.py           # Pydantic I/O schemas
      agent.py
    services/
      storage.py         # MinIO wrapper
      transcription.py   # faster-whisper wrapper
      scene.py           # scenedetect wrapper
      embedding.py       # Qdrant upsert/search
      agent.py           # LangGraph graph
    tasks/
      video_pipeline.py  # Celery tasks
    db/
      session.py         # async SQLAlchemy engine
      init_db.py
  alembic/env.py
  Dockerfile
  .env.example
```

### SETTINGS_PATTERN
```python
# SOURCE: pydantic-settings convention
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin123"
    minio_bucket: str = "videos"
    keycloak_url: str
    keycloak_realm: str = "editor"
    keycloak_client_id: str
    posthog_api_key: str = ""
    vllm_base_url: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
```

### ASYNC_DB_SESSION
```python
# SOURCE: SQLAlchemy 2.x async pattern
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
```

### CELERY_APP
```python
# SOURCE: Celery 5 + Redis pattern
from celery import Celery

celery_app = Celery(
    "editor",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.video_pipeline"],
)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.timezone = "UTC"
```

### FASTAPI_ROUTER
```python
# SOURCE: FastAPI APIRouter pattern
from fastapi import APIRouter, Depends, HTTPException, status

router = APIRouter(prefix="/videos", tags=["videos"])

@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_video(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> VideoCreateResponse:
    ...
```

### ERROR_HANDLING
```python
# SOURCE: FastAPI HTTPException pattern
raise HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail=f"Video {video_id} not found",
)
```

### LOGGING_PATTERN
```python
# SOURCE: loguru convention
from loguru import logger

logger.info("Starting transcription", video_id=video_id)
logger.error("Transcription failed", video_id=video_id, error=str(e))
```

### CELERY_TASK
```python
# SOURCE: Celery task best practices
@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def process_video(self, video_id: str) -> dict:
    try:
        ...
    except Exception as exc:
        logger.error("Task failed", video_id=video_id, error=str(exc))
        raise self.retry(exc=exc)
```

### LANGGRAPH_AGENT
```python
# SOURCE: LangGraph 0.2.x StateGraph pattern
from langgraph.graph import StateGraph, END
from typing import TypedDict

class AgentState(TypedDict):
    video_id: str
    instruction: str
    transcript: list[dict]
    scenes: list[dict]
    edit_plan: dict

graph = StateGraph(AgentState)
graph.add_node("analyze", analyze_node)
graph.add_node("plan", plan_node)
graph.set_entry_point("analyze")
graph.add_edge("analyze", "plan")
graph.add_edge("plan", END)
agent = graph.compile()
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/app/main.py` | CREATE | FastAPI app factory, CORS, metrics, lifespan |
| `backend/app/config.py` | CREATE | pydantic-settings, all env vars |
| `backend/app/worker.py` | CREATE | Celery app instance |
| `backend/app/dependencies.py` | CREATE | get_db, get_current_user |
| `backend/app/db/session.py` | CREATE | Async SQLAlchemy engine |
| `backend/app/db/init_db.py` | CREATE | Table creation on startup |
| `backend/app/models/video.py` | CREATE | Video, Scene, Segment ORM models |
| `backend/app/models/enums.py` | CREATE | VideoStatus enum |
| `backend/app/schemas/video.py` | CREATE | Pydantic I/O schemas |
| `backend/app/schemas/agent.py` | CREATE | Agent request/response schemas |
| `backend/app/api/v1/videos.py` | CREATE | Upload, status, transcript, scenes endpoints |
| `backend/app/api/v1/agent.py` | CREATE | Agent edit endpoint |
| `backend/app/api/v1/health.py` | CREATE | /health liveness check |
| `backend/app/api/v1/router.py` | CREATE | Include all sub-routers |
| `backend/app/services/storage.py` | CREATE | MinIO get/put/presign wrappers |
| `backend/app/services/transcription.py` | CREATE | faster-whisper inference |
| `backend/app/services/scene.py` | CREATE | scenedetect scene boundary extraction |
| `backend/app/services/embedding.py` | CREATE | Qdrant upsert + search |
| `backend/app/services/agent.py` | CREATE | LangGraph edit agent |
| `backend/app/tasks/video_pipeline.py` | CREATE | Celery tasks: ingest → transcribe → embed |
| `backend/alembic/env.py` | CREATE | Async Alembic migration environment |
| `backend/alembic/versions/001_initial.py` | CREATE | Initial schema migration |
| `backend/Dockerfile` | CREATE | Multi-stage Python image |
| `backend/.env.example` | CREATE | All required env vars with placeholders |
| `monitoring/prometheus.yml` | MOVE | Move from root (docker-compose expects `./monitoring/`) |

## NOT Building
- Frontend / Next.js editor UI
- vLLM model serving (separate GPU service per docker-compose notes)
- PostHog self-hosted (use cloud API key)
- Multi-tenant Keycloak realm provisioning (assume realm pre-configured)
- Real-time WebSocket streaming (SSE only for status)

---

## Step-by-Step Tasks

### Task 1: Fix prometheus.yml path mismatch
- **ACTION**: Move `prometheus.yml` from project root to `monitoring/prometheus.yml`
- **IMPLEMENT**: Create `monitoring/` directory, move the file
- **MIRROR**: `docker-compose.yml` line 144 mounts `./monitoring/prometheus.yml`
- **GOTCHA**: Without this, `docker compose up` fails with a volume mount error for Prometheus
- **VALIDATE**: `docker compose config` shows no path errors for prometheus volume

### Task 2: Create backend/app/config.py
- **ACTION**: CREATE settings with pydantic-settings BaseSettings
- **IMPLEMENT**: All env vars from docker-compose notes (DATABASE_URL, REDIS_URL, QDRANT_URL, MINIO_*, KEYCLOAK_*, POSTHOG_*, VLLM_BASE_URL)
- **MIRROR**: SETTINGS_PATTERN above
- **IMPORTS**: `from pydantic_settings import BaseSettings`
- **GOTCHA**: `DATABASE_URL` must use `postgresql+asyncpg://` prefix for SQLAlchemy async
- **VALIDATE**: `python -c "from app.config import settings; print(settings.redis_url)"`

### Task 3: Database session + ORM models
- **ACTION**: CREATE `backend/app/db/session.py` and `backend/app/models/video.py`
- **IMPLEMENT**:
  - `Video`: id (UUID PK), user_id (str), filename, status (VideoStatus), s3_key, created_at, updated_at
  - `Scene`: id, video_id (FK→Video), start_time (float), end_time (float), thumbnail_key
  - `Segment`: id, video_id (FK→Video), start_time (float), end_time (float), text, speaker
  - `VideoStatus` enum: PENDING, PROCESSING, READY, FAILED
- **MIRROR**: ASYNC_DB_SESSION pattern above
- **IMPORTS**: `from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column`, `from sqlalchemy.dialects.postgresql import UUID`
- **GOTCHA**: Use `server_default=text("gen_random_uuid()")` for UUID PKs; requires `pgcrypto` extension in Postgres or `uuid-ossp`
- **VALIDATE**: Alembic `revision --autogenerate` produces non-empty migration

### Task 4: Celery app + worker entry point
- **ACTION**: CREATE `backend/app/worker.py`
- **IMPLEMENT**: Celery instance with Redis broker/backend, JSON serializer, UTC timezone
- **MIRROR**: CELERY_APP pattern above
- **GOTCHA**: Import `settings` inside the factory or at module level after `app.config` is ready to avoid circular imports
- **VALIDATE**: `celery -A app.worker inspect ping` returns pong from worker

### Task 5: MinIO storage service
- **ACTION**: CREATE `backend/app/services/storage.py`
- **IMPLEMENT**: `StorageService` with `upload_file(key, data, content_type)`, `download_file(key) -> bytes`, `presign_url(key, expires=3600) -> str`, `ensure_bucket()`
- **IMPORTS**: `from minio import Minio`, `from minio.error import S3Error`
- **GOTCHA**: `secure=False` for local HTTP endpoint; `presigned_get_object` returns a URL string

### Task 6: faster-whisper transcription service
- **ACTION**: CREATE `backend/app/services/transcription.py`
- **IMPLEMENT**: `TranscriptionService.transcribe(audio_path: str) -> list[dict]`; each dict has `start`, `end`, `text`; auto-detect CUDA vs CPU
- **IMPORTS**: `from faster_whisper import WhisperModel`
- **GOTCHA**: Use `faster_whisper` (underscore) not `whisper` (openai-whisper). Both are in requirements.txt but new code must use `faster_whisper` only. Model loads once at service instantiation — not per request
- **VALIDATE**: Returns list with `start`, `end`, `text` keys for a short test WAV

### Task 7: Scene detection service
- **ACTION**: CREATE `backend/app/services/scene.py`
- **IMPLEMENT**: `SceneService.detect_scenes(video_path: str) -> list[dict]` using `scenedetect` `ContentDetector`
- **IMPORTS**: `from scenedetect import open_video, SceneManager`, `from scenedetect.detectors import ContentDetector`
- **GOTCHA**: Call `scene_manager.detect_scenes(video)` then `scene_manager.get_scene_list()`; convert `FrameTimecode` objects to float seconds with `.get_seconds()`

### Task 8: Qdrant embedding service
- **ACTION**: CREATE `backend/app/services/embedding.py`
- **IMPLEMENT**: `EmbeddingService.upsert_segments(video_id, segments)` and `search_similar(video_id, query, top_k=5)`; collection per video: `f"video_{video_id}_segments"`
- **IMPORTS**: `from qdrant_client import QdrantClient`, `from qdrant_client.models import PointStruct, VectorParams, Distance`
- **GOTCHA**: Create collection before upserting; check existence first for idempotency. Vector size = 384 for `all-MiniLM-L6-v2` (add `sentence-transformers` to requirements.txt)

### Task 9: Celery video pipeline task
- **ACTION**: CREATE `backend/app/tasks/video_pipeline.py`
- **IMPLEMENT**: `process_video(video_id: str)` chain:
  1. Download video from MinIO → tempfile
  2. `SceneService.detect_scenes()` → upsert Scene rows to DB
  3. Extract audio via `ffmpeg.input(path).output(audio_path, vn=None, acodec="pcm_s16le").run()`
  4. `TranscriptionService.transcribe()` → upsert Segment rows
  5. `EmbeddingService.upsert_segments()`
  6. Update `Video.status = READY`
  7. On error: `Video.status = FAILED`, `raise self.retry(exc=exc)`
- **MIRROR**: CELERY_TASK pattern (bind=True, max_retries=3)
- **GOTCHA**: `ffmpeg` CLI binary must be in Dockerfile (`apt-get install ffmpeg`); `ffmpeg-python` only wraps the CLI

### Task 10: FastAPI video endpoints
- **ACTION**: CREATE `backend/app/api/v1/videos.py`
- **IMPLEMENT**:
  - `POST /videos/upload` — receive UploadFile, chunk to tempfile, upload to MinIO, create Video row, enqueue task
  - `GET /videos/{video_id}/status` — return Video.status
  - `GET /videos/{video_id}/transcript` — return paginated Segments
  - `GET /videos/{video_id}/scenes` — return Scenes with pre-signed thumbnail URLs
- **MIRROR**: FASTAPI_ROUTER pattern above
- **GOTCHA**: Do NOT load the entire UploadFile into memory — stream chunks with `while chunk := await file.read(1024*1024)`
- **VALIDATE**: POST 10MB video → MinIO object exists → Celery task enqueued

### Task 11: LangGraph agent
- **ACTION**: CREATE `backend/app/services/agent.py` and `backend/app/api/v1/agent.py`
- **IMPLEMENT**: `AgentState` TypedDict → `analyze_node` (fetch transcript + scenes) → `plan_node` (call vLLM/Ollama) → return structured edit plan; `POST /agent/edit` endpoint
- **MIRROR**: LANGGRAPH_AGENT pattern above
- **GOTCHA**: If `VLLM_BASE_URL` is empty, return a template edit plan so the endpoint doesn't fail in dev. Use `langchain_openai.ChatOpenAI(base_url=settings.vllm_base_url)` for vLLM

### Task 12: FastAPI app factory
- **ACTION**: CREATE `backend/app/main.py`
- **IMPLEMENT**: Lifespan context manager (create tables on startup), CORS middleware, include `api_v1_router` at `/api/v1`, Prometheus instrumentator
- **IMPORTS**: `from prometheus_fastapi_instrumentator import Instrumentator`, `from contextlib import asynccontextmanager`
- **GOTCHA**: Call `Instrumentator().instrument(app).expose(app)` at module level, not inside lifespan
- **VALIDATE**: `GET /health` → 200; `GET /metrics` → Prometheus text

### Task 13: Keycloak JWT auth dependency
- **ACTION**: CREATE `backend/app/dependencies.py`
- **IMPLEMENT**: `get_current_user` validates Bearer JWT against Keycloak JWKS; cache public key with TTL
- **MIRROR**: KEYCLOAK_AUTH pattern above
- **GOTCHA**: JWKS endpoint = `{keycloak_url}/realms/{realm}/protocol/openid-connect/certs`; `aud` claim = client_id

### Task 14: Alembic async migration setup
- **ACTION**: CREATE `backend/alembic/env.py` and first migration
- **IMPLEMENT**: Async `run_migrations_online` using `asyncio.run()`; `target_metadata = Base.metadata`
- **GOTCHA**: Alembic's default `env.py` is sync; must use `AsyncEngine.connect()` pattern for async migrations
- **VALIDATE**: `alembic upgrade head` creates all tables

### Task 15: Dockerfile
- **ACTION**: CREATE `backend/Dockerfile`
- **IMPLEMENT**:
  ```dockerfile
  FROM python:3.11-slim AS base
  RUN apt-get update && apt-get install -y ffmpeg libmagic1 && rm -rf /var/lib/apt/lists/*
  WORKDIR /app
  COPY requirements.txt .
  RUN pip install --no-cache-dir setuptools wheel && pip install --no-cache-dir -r requirements.txt
  COPY . .
  ```
- **GOTCHA**: `setuptools wheel` must be installed BEFORE `-r requirements.txt` — same root cause as the local `pkg_resources` error. Also add `sentence-transformers` to requirements.txt for embeddings
- **VALIDATE**: `docker build -t editor-backend backend/` completes without errors

### Task 16: .env.example and fix monitoring path
- **ACTION**: CREATE `backend/.env.example`; CREATE `monitoring/` directory; MOVE `prometheus.yml` there
- **IMPLEMENT**: List every setting from config.py with placeholder values
- **VALIDATE**: `docker compose config` shows no volume path errors

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_upload_video` | Valid MP4 multipart | 202, video_id returned | No |
| `test_upload_invalid_type` | .exe file | 422 | Yes |
| `test_get_status_not_found` | Unknown UUID | 404 | Yes |
| `test_transcription_service` | Short WAV | List with start/end/text dicts | No |
| `test_scene_detection` | Sample MP4 | List with >0 scenes | No |
| `test_qdrant_upsert_search` | 3 segments | Top match returned | No |
| `test_auth_invalid_token` | Malformed JWT | 401 | Yes |

### Edge Cases Checklist
- [ ] Zero-byte video upload
- [ ] Video with no speech (transcription returns empty list)
- [ ] Video with no scene changes (single scene returned)
- [ ] Celery retry on MinIO connection failure
- [ ] Qdrant collection already exists (idempotent upsert)
- [ ] vLLM unavailable — agent returns template plan

---

## Validation Commands

### Static Analysis
```bash
cd backend && python -m mypy app/ --ignore-missing-imports
```
EXPECT: Zero type errors on core models/schemas

### Tests
```bash
cd backend && pytest tests/ -v --cov=app --cov-report=term-missing
```
EXPECT: All pass, >70% coverage

### Smoke Test
```bash
docker compose up -d postgres redis qdrant minio
cd backend && uvicorn app.main:app --reload
curl http://localhost:8000/health
curl http://localhost:8000/metrics
```

### Migration
```bash
cd backend && alembic upgrade head && alembic current
```
EXPECT: "head" is current, no pending migrations

### Manual E2E
- [ ] `curl -F file=@sample.mp4 http://localhost:8000/api/v1/videos/upload`
- [ ] Poll `GET /api/v1/videos/{id}/status` until READY
- [ ] `GET /api/v1/videos/{id}/transcript`
- [ ] `GET /api/v1/videos/{id}/scenes`
- [ ] `POST /api/v1/agent/edit` with `{"video_id":"...","instruction":"cut intro to 30s"}`

---

## Acceptance Criteria
- [ ] All 16 tasks completed
- [ ] `GET /health` → 200
- [ ] `GET /metrics` → Prometheus text format
- [ ] Video upload triggers Celery task
- [ ] Transcript available after processing
- [ ] Scene list with pre-signed URLs returned
- [ ] Agent returns structured edit plan JSON
- [ ] All tests pass, no type errors
- [ ] Docker image builds cleanly

## Completion Checklist
- [ ] `backend/` directory with full `app/` layout
- [ ] `alembic upgrade head` runs without error
- [ ] Celery worker starts successfully
- [ ] FastAPI starts successfully
- [ ] `monitoring/prometheus.yml` moved from root
- [ ] `setuptools wheel` pre-installed in Dockerfile before requirements
- [ ] No `import whisper` (openai-whisper) in new code — use `faster_whisper` only

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| GPU unavailable for faster-whisper | High (local dev) | Medium | Auto-fallback to `device="cpu"` |
| vLLM not running | High (local dev) | Medium | Agent returns template plan if VLLM_BASE_URL empty |
| `openai-whisper` build still fails | Low | Low | Dockerfile pre-installs setuptools; venv already fixed |
| Keycloak realm not configured | Medium | High | Add `/health` endpoint with no auth for smoke tests |
| `sentence-transformers` not in requirements.txt | High | High | Add it before Task 8 |

## Notes
- **Immediate fix applied**: `setuptools==84.0.0` and `wheel==0.48.0` installed in venv to unblock `openai-whisper` build. Dockerfile replicates this.
- **prometheus.yml path**: Currently at project root; docker-compose expects `./monitoring/prometheus.yml`. Move in Task 16.
- **Both whisper packages**: Both in requirements.txt. Use `faster_whisper` in all new code.
- **sentence-transformers**: Not currently in requirements.txt but needed for Qdrant embeddings (Task 8). Add `sentence-transformers==3.3.1` to requirements.txt.
- **vLLM**: Excluded from docker-compose intentionally (GPU-heavy). Agent endpoint uses `VLLM_BASE_URL` env var pointing at an external OpenAI-compatible endpoint.
