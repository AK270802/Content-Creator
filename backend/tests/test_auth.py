"""
Auth contract tests:
- No token -> 401 from get_current_user dependency
- Invalid JWT -> 401
- Cross-user resource access -> 404 (security-by-obscurity)
- Every protected route exposes get_current_user_id in its dependency chain
"""
import uuid
import asyncio
import inspect
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
import pytest


def _inject_mocks():
    """
    Inject minimal mocks so app.dependencies and both routers import cleanly.
    slowapi.Limiter must be a pass-through so @limiter.limit() does not wrap
    the endpoint and lose its signature.
    """
    # jose
    jose_mod = ModuleType("jose")
    jose_mod.JWTError = type("JWTError", (Exception,), {})
    jwt_mod = ModuleType("jose.jwt")
    jwt_mod.decode = MagicMock(side_effect=jose_mod.JWTError("bad token"))
    jose_mod.jwt = jwt_mod
    sys.modules["jose"]     = jose_mod
    sys.modules["jose.jwt"] = jwt_mod

    # slowapi — limiter must be a pass-through decorator so endpoint signatures survive
    def _passthrough(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator

    slowapi_mock = MagicMock()
    slowapi_mock.Limiter.return_value.limit = _passthrough
    sys.modules["slowapi"]      = slowapi_mock
    sys.modules["slowapi.util"] = MagicMock()

    # other heavy deps
    for mod in ("minio", "asyncpg"):
        sys.modules.setdefault(mod, MagicMock())
    # Force-set multipart every time — setdefault can miss re-imports
    _mp = MagicMock()
    sys.modules["multipart"] = _mp
    sys.modules["multipart.multipart"] = _mp
    db_mock = MagicMock()
    sys.modules["app.db"]         = db_mock
    sys.modules["app.db.session"] = db_mock
    sys.modules.setdefault("app.services.storage", MagicMock())

    # evict cached modules so they re-import with fresh mocks
    for key in list(sys.modules):
        if key in ("app.dependencies", "app.api.v1.edit_plans",
                   "app.api.v1.videos", "app.api.v1"):
            del sys.modules[key]


def _dep_functions(route) -> set:
    """Return dependency callables declared on a FastAPI route endpoint."""
    sig = inspect.signature(route.endpoint)
    return {
        p.default.dependency
        for p in sig.parameters.values()
        if hasattr(p.default, "dependency")
    }


# ---- dependency-level 401 tests -----------------------------------------------

def test_get_current_user_returns_401_with_no_credentials():
    _inject_mocks()
    from app.dependencies import get_current_user
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user(credentials=None))
    assert exc.value.status_code == 401
    assert "Bearer" in exc.value.headers.get("WWW-Authenticate", "")


def test_get_current_user_returns_401_with_bad_jwt():
    _inject_mocks()
    from app.dependencies import get_current_user
    from fastapi.security import HTTPAuthorizationCredentials
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.valid.jwt")

    async def _run():
        with patch("app.dependencies._fetch_jwks", return_value={"keys": [{"kty": "RSA"}]}):
            return await get_current_user(credentials=creds)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_run())
    assert exc.value.status_code == 401


def test_get_current_user_id_extracts_sub():
    _inject_mocks()
    from app.dependencies import get_current_user_id
    assert get_current_user_id({"sub": "user-abc", "preferred_username": "alice"}) == "user-abc"


def test_get_current_user_id_falls_back_to_preferred_username():
    _inject_mocks()
    from app.dependencies import get_current_user_id
    assert get_current_user_id({"preferred_username": "bob"}) == "bob"


# ---- owner-scoping / cross-user 404 tests -------------------------------------

def _make_empty_db():
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    return mock_db


def test_get_video_for_user_raises_404_for_wrong_user():
    _inject_mocks()
    from app.api.v1.edit_plans import _get_video_for_user
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_get_video_for_user(uuid.uuid4(), "user-b", _make_empty_db()))
    assert exc.value.status_code == 404


def test_get_plan_for_user_raises_404_for_wrong_user():
    from app.api.v1.edit_plans import _get_plan_for_user
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_get_plan_for_user(uuid.uuid4(), "user-b", _make_empty_db()))
    assert exc.value.status_code == 404


# ---- route dependency coverage ------------------------------------------------

def test_all_edit_plan_routes_require_auth():
    """Every edit_plans route must declare get_current_user_id as a dependency."""
    _inject_mocks()
    from app.api.v1 import edit_plans
    from app.dependencies import get_current_user_id

    unprotected = [
        route.path
        for route in edit_plans.router.routes
        if get_current_user_id not in _dep_functions(route)
    ]
    assert unprotected == [], f"Routes missing auth: {unprotected}"


def test_all_video_routes_require_auth():
    """Every videos route must declare get_current_user_id as a dependency."""
    _inject_mocks()
    from app.api.v1 import videos
    from app.dependencies import get_current_user_id

    unprotected = [
        route.path
        for route in videos.router.routes
        if get_current_user_id not in _dep_functions(route)
    ]
    assert unprotected == [], f"Routes missing auth: {unprotected}"
