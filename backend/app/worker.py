from celery import Celery
from app.config import settings

celery_app = Celery(
    "editor",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.video_pipeline", "app.tasks.render_pipeline"],
)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.timezone = "UTC"
celery_app.conf.enable_utc = True
celery_app.conf.task_track_started = True
celery_app.conf.task_acks_late = True
