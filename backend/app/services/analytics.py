from __future__ import annotations
from loguru import logger
from app.config import settings


def _get_client():
    """Return a PostHog client, or None if no API key is configured."""
    if not settings.posthog_api_key:
        return None
    try:
        import posthog
        posthog.api_key = settings.posthog_api_key
        posthog.host = settings.posthog_host
        posthog.sync_mode = False  # non-blocking
        return posthog
    except Exception as exc:
        logger.warning(f"PostHog init failed: {exc}")
        return None


def capture(user_id: str, event: str, properties: dict | None = None) -> None:
    """Fire-and-forget PostHog event. Silently drops if PostHog is not configured."""
    client = _get_client()
    if client is None:
        return
    try:
        client.capture(
            distinct_id=user_id,
            event=event,
            properties=properties or {},
        )
    except Exception as exc:
        logger.warning(f"PostHog capture failed [{event}]: {exc}")
