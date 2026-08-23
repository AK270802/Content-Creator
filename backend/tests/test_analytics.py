import sys
from unittest.mock import MagicMock, patch


def test_capture_is_noop_when_no_api_key():
    """capture() must silently do nothing when posthog_api_key is empty."""
    from app.services.analytics import capture
    with patch("app.config.settings") as mock_settings:
        mock_settings.posthog_api_key = ""
        capture("user-1", "video_uploaded", {"video_id": "abc"})
    # No exception = pass


def test_capture_calls_posthog_client():
    """capture() must call posthog.capture with the correct arguments."""
    posthog_mock = MagicMock()
    sys.modules["posthog"] = posthog_mock

    from app.services import analytics
    import importlib
    importlib.reload(analytics)

    with patch("app.services.analytics.settings") as mock_settings:
        mock_settings.posthog_api_key = "phc_test"
        mock_settings.posthog_host = "https://app.posthog.com"
        analytics.capture("user-1", "video_uploaded", {"video_id": "abc"})

    posthog_mock.capture.assert_called_once_with(
        distinct_id="user-1",
        event="video_uploaded",
        properties={"video_id": "abc"},
    )


def test_capture_empty_properties_defaults_to_dict():
    """capture() with properties=None must not raise and pass an empty dict."""
    posthog_mock = MagicMock()
    sys.modules["posthog"] = posthog_mock

    from app.services import analytics
    import importlib
    importlib.reload(analytics)

    with patch("app.services.analytics.settings") as mock_settings:
        mock_settings.posthog_api_key = "phc_test"
        mock_settings.posthog_host = "https://app.posthog.com"
        analytics.capture("user-2", "edit_plan_approved", None)

    posthog_mock.capture.assert_called_once()
    _, kwargs = posthog_mock.capture.call_args
    assert kwargs["properties"] == {}


def test_capture_swallows_posthog_exception():
    """capture() must not propagate exceptions from the PostHog client."""
    posthog_mock = MagicMock()
    posthog_mock.capture.side_effect = RuntimeError("network error")
    sys.modules["posthog"] = posthog_mock

    from app.services import analytics
    import importlib
    importlib.reload(analytics)

    with patch("app.config.settings") as mock_settings:
        mock_settings.posthog_api_key = "phc_test"
        mock_settings.posthog_host = "https://app.posthog.com"
        analytics.capture("user-3", "render_failed", {"error": "boom"})
    # Exception swallowed — no raise


def test_config_has_posthog_fields():
    from app.config import settings
    assert hasattr(settings, "posthog_api_key")
    assert hasattr(settings, "posthog_host")
    assert settings.posthog_api_key == ""
    assert "posthog" in settings.posthog_host
