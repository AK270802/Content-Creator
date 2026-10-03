from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_current_user, get_current_user_db
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MFAVerifyRequest,
    RecoveryCodesResponse,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SessionResponse,
    TOTPConfirmRequest,
    TOTPEnrollResponse,
    TokenResponse,
    UserResponse,
    VerifyEmailRequest,
)
import app.services.auth_service as svc

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------
# Registration & email verification
# ---------------------------------------------------------------------------

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        user = await svc.register_user(db, req, ip=_client_ip(request))
    except ValueError as e:
        if str(e) == "invalid_invitation":
            raise HTTPException(status_code=400, detail="Invalid or expired invitation.")
        raise HTTPException(status_code=400, detail="Registration failed. Please try again.")
    return UserResponse.model_validate(user)


@router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(req: VerifyEmailRequest, db: AsyncSession = Depends(get_db)):
    ok = await svc.verify_email(db, req.token)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token.")


@router.post("/resend-verification", status_code=status.HTTP_204_NO_CONTENT)
async def resend_verification(req: ResendVerificationRequest, db: AsyncSession = Depends(get_db)):
    await svc.resend_verification(db, req.email)


# ---------------------------------------------------------------------------
# Login / MFA / Logout
# ---------------------------------------------------------------------------

@router.post("/login")
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        result = await svc.login_user(
            db, req.email, req.password, req.remember_me,
            ip=_client_ip(request), ua=request.headers.get("User-Agent"),
        )
    except ValueError as e:
        if str(e) == "account_locked":
            raise HTTPException(status_code=403, detail="Account is temporarily locked. Try again later.")
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return result


@router.post("/mfa/verify", response_model=TokenResponse)
async def mfa_verify(req: MFAVerifyRequest, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        return await svc.complete_mfa_login(
            db, req.session_token, req.code,
            ip=_client_ip(request), ua=request.headers.get("User-Agent"),
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA code.")


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        return await svc.refresh_tokens(
            db, req.refresh_token,
            ip=_client_ip(request), ua=request.headers.get("User-Agent"),
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    jti = user.get("jti")
    if jti:
        await svc.logout(db, jti)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    import uuid
    await svc.logout_all(db, uuid.UUID(user["sub"]), user.get("jti", ""))


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------

@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user_db)):
    return UserResponse.model_validate(current_user)


# ---------------------------------------------------------------------------
# Password management
# ---------------------------------------------------------------------------

@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(req: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    await svc.request_password_reset(db, req.email)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(req: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    try:
        await svc.reset_password(db, req.token, req.password)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token.")


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user_db),
    user_payload: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await svc.change_password(
            db, current_user, req.current_password, req.new_password,
            current_jti=user_payload.get("jti", ""),
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Current password is incorrect.")


# ---------------------------------------------------------------------------
# TOTP MFA
# ---------------------------------------------------------------------------

@router.post("/totp/enroll", response_model=TOTPEnrollResponse)
async def totp_enroll(current_user: User = Depends(get_current_user_db)):
    secret_b32, uri = svc.totp_enroll_generate(current_user)
    return TOTPEnrollResponse(secret=secret_b32, qr_uri=uri)


@router.post("/totp/confirm", response_model=RecoveryCodesResponse)
async def totp_confirm(
    req: TOTPConfirmRequest,
    current_user: User = Depends(get_current_user_db),
    db: AsyncSession = Depends(get_db),
):
    try:
        codes = await svc.totp_enroll_confirm(db, current_user, req.secret, req.code)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid TOTP code.")
    return RecoveryCodesResponse(codes=codes)


@router.post("/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def totp_disable(
    req: TOTPConfirmRequest,
    current_user: User = Depends(get_current_user_db),
    db: AsyncSession = Depends(get_db),
):
    try:
        await svc.totp_disable(db, current_user, req.code)
    except ValueError as e:
        detail = "TOTP is not enabled." if "not_enabled" in str(e) else "Invalid TOTP code."
        raise HTTPException(status_code=400, detail=detail)


@router.post("/recovery-codes/regenerate", response_model=RecoveryCodesResponse)
async def regenerate_recovery_codes(
    req: TOTPConfirmRequest,
    current_user: User = Depends(get_current_user_db),
    db: AsyncSession = Depends(get_db),
):
    try:
        codes = await svc.regenerate_recovery_codes(db, current_user, req.code)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid TOTP code.")
    return RecoveryCodesResponse(codes=codes)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    current_user: User = Depends(get_current_user_db),
    user_payload: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sessions = await svc.list_sessions(db, current_user.id)
    current_jti = user_payload.get("jti")
    result = []
    for s in sessions:
        sr = SessionResponse.model_validate(s)
        sr.is_current = s.token_jti == current_jti
        result.append(sr)
    return result


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: str,
    current_user: User = Depends(get_current_user_db),
    db: AsyncSession = Depends(get_db),
):
    import uuid
    try:
        await svc.revoke_session(db, current_user.id, uuid.UUID(session_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found.")
