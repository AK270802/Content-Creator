import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Union

import pyotp
from loguru import logger
from cryptography.fernet import Fernet
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import (
    EmailVerificationToken,
    Invitation,
    MFADevice,
    PasswordResetToken,
    RecoveryCode,
    SecurityEvent,
    User,
    UserRole,
    UserSession,
)
from app.schemas.auth import (
    MFAChallengeResponse,
    MigrationImportRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

_pwd_ctx = CryptContext(schemes=["argon2"], deprecated="auto")

_ALGORITHM = "HS256"
_ISSUER = "cutroom"
_PRE_AUTH_EXPIRE = timedelta(minutes=5)
_ACCESS_EXPIRE = timedelta(minutes=15)
_REFRESH_EXPIRE_DEFAULT = timedelta(hours=1)
_REFRESH_EXPIRE_REMEMBER = timedelta(days=30)


def _utcnow() -> datetime:
    """Naive UTC for TIMESTAMP WITHOUT TIME ZONE columns (asyncpg rejects aware datetimes)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _fernet() -> Fernet:
    key = settings.totp_encryption_key.encode() if isinstance(settings.totp_encryption_key, str) else settings.totp_encryption_key
    return Fernet(key)


def create_access_token(user: User, jti: str, expires_delta: timedelta | None = None) -> str:
    now = datetime.now(timezone.utc)
    exp = now + (expires_delta or _ACCESS_EXPIRE)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "jti": jti,
        "iss": _ISSUER,
        "iat": now,
        "exp": exp,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def create_refresh_token() -> tuple[str, str]:
    """Returns (plaintext_token, sha256_hash)."""
    token = secrets.token_urlsafe(48)
    return token, _token_hash(token)


def create_pre_auth_token(user_id: uuid.UUID) -> str:
    """Short-lived token issued after password check, before MFA."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "purpose": "mfa_challenge",
        "iat": now,
        "exp": now + _PRE_AUTH_EXPIRE,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_pre_auth_token(token: str) -> uuid.UUID | None:
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
        if data.get("purpose") != "mfa_challenge":
            return None
        return uuid.UUID(data["sub"])
    except JWTError:
        return None


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Email (SMTP via aiosmtplib)
# ---------------------------------------------------------------------------

async def _send_email(to: str, subject: str, body: str) -> None:
    if not getattr(settings, "smtp_host", None):
        logger.warning(f"SMTP disabled — email to {to} not sent. Subject: {subject}. Body: {body}")
        return
    try:
        import aiosmtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body, "html")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            start_tls=getattr(settings, "smtp_tls", True),
            username=getattr(settings, "smtp_username", None),
            password=getattr(settings, "smtp_password", None),
        )
        logger.info(f"Email sent to {to}: {subject}")
    except Exception as exc:
        # never raise so auth ops always succeed, but make failures visible
        logger.error(f"Failed to send email to {to} via {settings.smtp_host}:{settings.smtp_port}: {exc!r}")


# ---------------------------------------------------------------------------
# Security event logging
# ---------------------------------------------------------------------------

async def log_security_event(
    db: AsyncSession,
    event_type: str,
    user_id: uuid.UUID | None = None,
    ip: str | None = None,
    ua: str | None = None,
    extra: dict | None = None,
) -> None:
    db.add(SecurityEvent(
        user_id=user_id,
        event_type=event_type,
        ip_address=ip,
        user_agent=ua,
        extra=extra,
    ))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def register_user(
    db: AsyncSession,
    req: RegisterRequest,
    ip: str | None = None,
) -> User:
    email_norm = req.email.lower().strip()

    # Check for existing account (generic error to prevent enumeration)
    existing = await db.scalar(select(User).where(User.email_normalized == email_norm))
    if existing:
        # Silently succeed to prevent enumeration — but don't create duplicate
        raise ValueError("registration_failed")

    # Validate invitation token if provided
    if req.invitation_token:
        token_hash = _token_hash(req.invitation_token)
        invite = await db.scalar(
            select(Invitation).where(
                Invitation.token_hash == token_hash,
                Invitation.email_normalized == email_norm,
                Invitation.accepted_at.is_(None),
                Invitation.expires_at > _utcnow(),
            )
        )
        if not invite:
            raise ValueError("invalid_invitation")
        role = invite.role
    else:
        role = UserRole.USER

    user = User(
        email=req.email,
        email_normalized=email_norm,
        full_name=req.full_name,
        password_hash=hash_password(req.password),
        role=role,
    )
    db.add(user)
    await db.flush()

    if req.invitation_token and invite:
        invite.accepted_at = _utcnow()

    # Issue email verification token
    raw_token = secrets.token_urlsafe(32)
    db.add(EmailVerificationToken(
        user_id=user.id,
        token_hash=_token_hash(raw_token),
        expires_at=_utcnow() + timedelta(hours=24),
    ))

    await log_security_event(db, "register", user.id, ip)
    await db.commit()
    await db.refresh(user)

    verify_url = f"{getattr(settings, 'frontend_url', 'http://localhost:8081')}/verify-email?token={raw_token}"
    await _send_email(
        user.email,
        "Verify your Cutroom account",
        f"<p>Click <a href='{verify_url}'>here</a> to verify your email. Link expires in 24 hours.</p>",
    )

    return user


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

async def verify_email(db: AsyncSession, token: str) -> bool:
    token_hash = _token_hash(token)
    row = await db.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == token_hash,
            EmailVerificationToken.used_at.is_(None),
            EmailVerificationToken.expires_at > _utcnow(),
        )
    )
    if not row:
        return False

    row.used_at = _utcnow()
    await db.execute(
        update(User).where(User.id == row.user_id).values(email_verified=True)
    )
    await db.commit()
    return True


async def resend_verification(db: AsyncSession, email: str, ip: str | None = None) -> None:
    email_norm = email.lower().strip()
    user = await db.scalar(select(User).where(User.email_normalized == email_norm))
    if not user or user.email_verified:
        return  # generic non-response

    raw_token = secrets.token_urlsafe(32)
    db.add(EmailVerificationToken(
        user_id=user.id,
        token_hash=_token_hash(raw_token),
        expires_at=_utcnow() + timedelta(hours=24),
    ))
    await db.commit()

    verify_url = f"{getattr(settings, 'frontend_url', 'http://localhost:8081')}/verify-email?token={raw_token}"
    await _send_email(
        user.email,
        "Verify your Cutroom account",
        f"<p>Click <a href='{verify_url}'>here</a> to verify your email.</p>",
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def login_user(
    db: AsyncSession,
    email: str,
    password: str,
    remember_me: bool,
    ip: str | None = None,
    ua: str | None = None,
) -> Union[TokenResponse, MFAChallengeResponse]:
    email_norm = email.lower().strip()
    user = await db.scalar(select(User).where(User.email_normalized == email_norm))

    _GENERIC_FAIL = ValueError("invalid_credentials")

    if not user or not user.password_hash:
        # Still run hash to prevent timing attacks
        _pwd_ctx.dummy_verify()
        raise _GENERIC_FAIL

    if not user.is_active:
        raise _GENERIC_FAIL

    # Check lockout
    if user.is_locked:
        if user.locked_until and _utcnow() > user.locked_until:
            await db.execute(
                update(User).where(User.id == user.id).values(
                    is_locked=False, locked_until=None, failed_login_attempts=0
                )
            )
            await db.commit()
            await db.refresh(user)
        else:
            await log_security_event(db, "login_blocked_locked", user.id, ip, ua)
            await db.commit()
            raise ValueError("account_locked")

    if not verify_password(password, user.password_hash):
        new_attempts = user.failed_login_attempts + 1
        lock_vals: dict = {"failed_login_attempts": new_attempts}
        if new_attempts >= 5:
            lock_vals["is_locked"] = True
            lock_vals["locked_until"] = _utcnow() + timedelta(minutes=15)
        await db.execute(update(User).where(User.id == user.id).values(**lock_vals))
        await log_security_event(db, "login_failed", user.id, ip, ua)
        await db.commit()
        raise _GENERIC_FAIL

    # Reset failed attempts on success
    await db.execute(
        update(User).where(User.id == user.id).values(
            failed_login_attempts=0, is_locked=False, locked_until=None
        )
    )

    if user.totp_enabled or user.mfa_required:
        pre_auth = create_pre_auth_token(user.id)
        await log_security_event(db, "login_mfa_challenge", user.id, ip, ua)
        await db.commit()
        return MFAChallengeResponse(session_token=pre_auth)

    tokens = await _create_session_tokens(db, user, remember_me, ip, ua)
    await log_security_event(db, "login_success", user.id, ip, ua)
    await db.commit()
    return tokens


async def complete_mfa_login(
    db: AsyncSession,
    session_token: str,
    code: str,
    ip: str | None = None,
    ua: str | None = None,
) -> TokenResponse:
    user_id = decode_pre_auth_token(session_token)
    if not user_id:
        raise ValueError("invalid_session")

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise ValueError("invalid_session")

    # Try TOTP
    if user.totp_enabled and user.totp_secret_encrypted:
        secret_b32 = _fernet().decrypt(user.totp_secret_encrypted).decode()
        totp = pyotp.TOTP(secret_b32)
        if totp.verify(code, valid_window=1):
            tokens = await _create_session_tokens(db, user, False, ip, ua)
            await log_security_event(db, "mfa_success_totp", user.id, ip, ua)
            await db.commit()
            return tokens

    # Try recovery codes
    code_hash = _token_hash(code.replace("-", "").strip())
    recovery = await db.scalar(
        select(RecoveryCode).where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash == code_hash,
            RecoveryCode.used_at.is_(None),
        )
    )
    if recovery:
        recovery.used_at = _utcnow()
        tokens = await _create_session_tokens(db, user, False, ip, ua)
        await log_security_event(db, "mfa_success_recovery", user.id, ip, ua)
        await db.commit()
        return tokens

    await log_security_event(db, "mfa_failed", user.id, ip, ua)
    await db.commit()
    raise ValueError("invalid_mfa_code")


async def _create_session_tokens(
    db: AsyncSession,
    user: User,
    remember_me: bool,
    ip: str | None,
    ua: str | None,
) -> TokenResponse:
    jti = str(uuid.uuid4())
    refresh_expire = _REFRESH_EXPIRE_REMEMBER if remember_me else _REFRESH_EXPIRE_DEFAULT
    access_token = create_access_token(user, jti)
    raw_refresh, refresh_hash = create_refresh_token()

    session = UserSession(
        user_id=user.id,
        token_jti=jti,
        refresh_token_hash=refresh_hash,
        ip_address=ip,
        user_agent=ua,
        expires_at=_utcnow() + refresh_expire,
    )
    db.add(session)

    await db.execute(
        update(User).where(User.id == user.id).values(last_login_at=_utcnow())
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        token_type="bearer",
        expires_in=int(_ACCESS_EXPIRE.total_seconds()),
        user=UserResponse.model_validate(user),
    )


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

async def refresh_tokens(
    db: AsyncSession,
    refresh_token: str,
    ip: str | None = None,
    ua: str | None = None,
) -> TokenResponse:
    token_hash = _token_hash(refresh_token)
    session = await db.scalar(
        select(UserSession).where(
            UserSession.refresh_token_hash == token_hash,
            UserSession.expires_at > _utcnow(),
        )
    )
    if not session:
        raise ValueError("invalid_refresh_token")

    user = await db.get(User, session.user_id)
    if not user or not user.is_active:
        raise ValueError("invalid_refresh_token")

    # Rotate: invalidate old session, create new
    await db.delete(session)

    jti = str(uuid.uuid4())
    raw_refresh, refresh_hash = create_refresh_token()
    access_token = create_access_token(user, jti)
    expire = session.expires_at  # preserve original expiry

    new_session = UserSession(
        user_id=user.id,
        token_jti=jti,
        refresh_token_hash=refresh_hash,
        ip_address=ip,
        user_agent=ua,
        expires_at=expire,
    )
    db.add(new_session)
    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        token_type="bearer",
        expires_in=int(_ACCESS_EXPIRE.total_seconds()),
        user=UserResponse.model_validate(user),
    )


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

async def logout(db: AsyncSession, jti: str) -> None:
    session = await db.scalar(select(UserSession).where(UserSession.token_jti == jti))
    if session:
        await db.delete(session)
        await db.commit()


async def logout_all(db: AsyncSession, user_id: uuid.UUID, current_jti: str) -> None:
    result = await db.scalars(select(UserSession).where(UserSession.user_id == user_id))
    for session in result.all():
        await db.delete(session)
    await db.commit()


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

async def request_password_reset(db: AsyncSession, email: str) -> None:
    """Always returns success to prevent enumeration."""
    email_norm = email.lower().strip()
    user = await db.scalar(select(User).where(User.email_normalized == email_norm))
    if not user or not user.is_active:
        return

    raw_token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=_token_hash(raw_token),
        expires_at=_utcnow() + timedelta(hours=1),
    ))
    await db.commit()

    reset_url = f"{getattr(settings, 'frontend_url', 'http://localhost:8081')}/reset-password?token={raw_token}"
    await _send_email(
        user.email,
        "Reset your Cutroom password",
        f"<p>Click <a href='{reset_url}'>here</a> to reset your password. Link expires in 1 hour.</p>",
    )


async def reset_password(db: AsyncSession, token: str, new_password: str) -> None:
    token_hash = _token_hash(token)
    row = await db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > _utcnow(),
        )
    )
    if not row:
        raise ValueError("invalid_token")

    row.used_at = _utcnow()
    await db.execute(
        update(User).where(User.id == row.user_id).values(
            password_hash=hash_password(new_password),
            failed_login_attempts=0,
            is_locked=False,
            locked_until=None,
        )
    )

    # Revoke all sessions after password reset
    sessions = await db.scalars(select(UserSession).where(UserSession.user_id == row.user_id))
    for s in sessions.all():
        await db.delete(s)

    await log_security_event(db, "password_reset", row.user_id)
    await db.commit()


async def change_password(
    db: AsyncSession,
    user: User,
    current_password: str,
    new_password: str,
    current_jti: str,
) -> None:
    if not user.password_hash or not verify_password(current_password, user.password_hash):
        raise ValueError("invalid_credentials")

    await db.execute(
        update(User).where(User.id == user.id).values(password_hash=hash_password(new_password))
    )

    # Revoke all sessions except current
    sessions = await db.scalars(select(UserSession).where(UserSession.user_id == user.id))
    for s in sessions.all():
        if s.token_jti != current_jti:
            await db.delete(s)

    await log_security_event(db, "password_changed", user.id)
    await db.commit()


# ---------------------------------------------------------------------------
# TOTP enrollment
# ---------------------------------------------------------------------------

def totp_enroll_generate(user: User) -> tuple[str, str]:
    """Returns (secret_b32, otpauth_uri). Secret not yet saved — await confirmation."""
    secret_b32 = pyotp.random_base32()
    uri = pyotp.totp.TOTP(secret_b32).provisioning_uri(
        name=user.email, issuer_name="Cutroom"
    )
    return secret_b32, uri


async def totp_enroll_confirm(
    db: AsyncSession, user: User, secret_b32: str, code: str
) -> list[str]:
    totp = pyotp.TOTP(secret_b32)
    if not totp.verify(code, valid_window=1):
        raise ValueError("invalid_totp_code")

    encrypted = _fernet().encrypt(secret_b32.encode())
    await db.execute(
        update(User).where(User.id == user.id).values(
            totp_enabled=True,
            totp_secret_encrypted=encrypted,
        )
    )

    # Store device record
    db.add(MFADevice(
        user_id=user.id,
        device_type="totp",
        name="Authenticator app",
        secret_encrypted=encrypted,
    ))

    codes = await _generate_recovery_codes(db, user)
    await log_security_event(db, "totp_enabled", user.id)
    await db.commit()
    return codes


async def totp_disable(db: AsyncSession, user: User, code: str) -> None:
    if not user.totp_enabled or not user.totp_secret_encrypted:
        raise ValueError("totp_not_enabled")

    secret_b32 = _fernet().decrypt(user.totp_secret_encrypted).decode()
    if not pyotp.TOTP(secret_b32).verify(code, valid_window=1):
        raise ValueError("invalid_totp_code")

    await db.execute(
        update(User).where(User.id == user.id).values(
            totp_enabled=False,
            totp_secret_encrypted=None,
        )
    )

    devices = await db.scalars(
        select(MFADevice).where(
            MFADevice.user_id == user.id, MFADevice.device_type == "totp"
        )
    )
    for d in devices.all():
        await db.delete(d)

    await log_security_event(db, "totp_disabled", user.id)
    await db.commit()


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------

async def _generate_recovery_codes(db: AsyncSession, user: User) -> list[str]:
    # Remove existing unused codes
    existing = await db.scalars(
        select(RecoveryCode).where(RecoveryCode.user_id == user.id)
    )
    for rc in existing.all():
        await db.delete(rc)

    codes: list[str] = []
    for _ in range(10):
        raw = f"{secrets.token_hex(4)}-{secrets.token_hex(4)}"
        codes.append(raw)
        normalized = raw.replace("-", "")
        db.add(RecoveryCode(
            user_id=user.id,
            code_hash=_token_hash(normalized),
        ))

    return codes


async def regenerate_recovery_codes(
    db: AsyncSession, user: User, totp_code: str
) -> list[str]:
    if user.totp_enabled and user.totp_secret_encrypted:
        secret_b32 = _fernet().decrypt(user.totp_secret_encrypted).decode()
        if not pyotp.TOTP(secret_b32).verify(totp_code, valid_window=1):
            raise ValueError("invalid_totp_code")

    codes = await _generate_recovery_codes(db, user)
    await log_security_event(db, "recovery_codes_regenerated", user.id)
    await db.commit()
    return codes


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

async def list_sessions(db: AsyncSession, user_id: uuid.UUID) -> list[UserSession]:
    result = await db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user_id)
        .order_by(UserSession.last_active_at.desc())
    )
    return list(result.all())


async def revoke_session(
    db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    session = await db.scalar(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
        )
    )
    if not session:
        raise ValueError("session_not_found")
    await db.delete(session)
    await db.commit()


# ---------------------------------------------------------------------------
# Admin operations
# ---------------------------------------------------------------------------

async def invite_user(
    db: AsyncSession,
    email: str,
    role: UserRole,
    invited_by_id: uuid.UUID,
) -> str:
    email_norm = email.lower().strip()
    raw_token = secrets.token_urlsafe(32)
    db.add(Invitation(
        email=email,
        email_normalized=email_norm,
        invited_by_id=invited_by_id,
        role=role,
        token_hash=_token_hash(raw_token),
        expires_at=_utcnow() + timedelta(days=7),
    ))
    await db.commit()

    invite_url = f"{getattr(settings, 'frontend_url', 'http://localhost:8081')}/register?invite={raw_token}"
    await _send_email(
        email,
        "You've been invited to Cutroom",
        f"<p>Click <a href='{invite_url}'>here</a> to create your account. Invite expires in 7 days.</p>",
    )
    return raw_token


async def deactivate_user(
    db: AsyncSession, target_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    await db.execute(update(User).where(User.id == target_id).values(is_active=False))
    sessions = await db.scalars(select(UserSession).where(UserSession.user_id == target_id))
    for s in sessions.all():
        await db.delete(s)
    await log_security_event(db, "user_deactivated", target_id, extra={"by": str(admin_id)})
    await db.commit()


async def reactivate_user(
    db: AsyncSession, target_id: uuid.UUID, admin_id: uuid.UUID
) -> None:
    await db.execute(
        update(User).where(User.id == target_id).values(
            is_active=True, is_locked=False, locked_until=None, failed_login_attempts=0
        )
    )
    await log_security_event(db, "user_reactivated", target_id, extra={"by": str(admin_id)})
    await db.commit()


# ---------------------------------------------------------------------------
# Legacy user import
# ---------------------------------------------------------------------------

async def import_keycloak_user(
    db: AsyncSession, req: MigrationImportRequest
) -> User:
    email_norm = req.email.lower().strip()

    # Upsert when an external identity id already exists
    existing = await db.scalar(
        select(User).where(User.keycloak_id == req.keycloak_id)
    )
    if existing:
        existing.email = req.email
        existing.email_normalized = email_norm
        existing.full_name = req.full_name
        existing.email_verified = req.email_verified
        existing.role = req.role
        await db.commit()
        await db.refresh(existing)
        return existing

    user = User(
        email=req.email,
        email_normalized=email_norm,
        full_name=req.full_name,
        email_verified=req.email_verified,
        role=req.role,
        keycloak_id=req.keycloak_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
