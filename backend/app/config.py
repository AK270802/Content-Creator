from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Application
    app_name: str = "Content Creation API"
    app_version: str = "1.0.0"
    debug: bool = False
    allowed_origins: str = "http://localhost:3000,http://localhost:8000"

    # Default admin credentials (change in production via env)
    admin_username: str = "admin"
    admin_password: str = "Admin@1234!"
    admin_email: str = "admin@contentplatform.local"

    # Database
    database_url: str = "postgresql+asyncpg://editor:changeme@localhost:5432/video_editor"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin123"
    minio_bucket: str = "videos"
    minio_secure: bool = False

    # Keycloak
    keycloak_url: str = "http://localhost:8080"
    keycloak_realm: str = "editor"
    keycloak_client_id: str = "editor-api"
    keycloak_client_secret: str = ""

    # vLLM / LLM
    vllm_base_url: str = ""
    vllm_model: str = "Qwen/Qwen3-VL-7B-Instruct"
    llm_temperature: float = 0.2

    # Whisper
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    # Security
    secret_key: str = "changeme-use-a-long-random-string-in-production"
    access_token_expire_minutes: int = 60


    # Vision model (OpenAI-compatible: Ollama llava, vLLM, etc.)
    vision_model_base_url: str = ""
    vision_model_name: str = "llava:7b"
    vision_frame_count: int = 3  # frames extracted per scene

    # Planning model (may differ from vision model)
    planning_model_base_url: str = ""
    planning_model_name: str = "mistral:7b"
    planning_temperature: float = 0.1

    # Render
    render_output_prefix: str = "renders"
    crossfade_duration: float = 0.4  # seconds between clips
    # Rate limiting (slowapi format: "N/period")
    rate_limit_upload: str = "5/minute"
    rate_limit_default: str = "60/minute"
    rate_limit_agent: str = "10/minute"

    # PostHog analytics
    posthog_api_key: str = ""
    posthog_host: str = "https://app.posthog.com"

    # Timeline confidence filtering — events below threshold excluded from LLM context
    # (kept in DB for auditing; only filtered from planning/revision prompts)
    timeline_confidence_threshold: float = 0.5

    # Revision governance — max /revise calls per user per UTC day
    revision_daily_cap: int = 20

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

