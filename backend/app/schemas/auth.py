import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from app.models.user import UserRole


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    confirm_password: str
    full_name: str | None = None
    invitation_token: str | None = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 10:
            raise ValueError("Password must be at least 10 characters.")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter.")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit.")
        return v

    @model_validator(mode="after")
    def passwords_match(self) -> "RegisterRequest":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = False


class MFAVerifyRequest(BaseModel):
    session_token: str
    code: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    role: UserRole
    email_verified: bool
    totp_enabled: bool
    mfa_required: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class MFAChallengeResponse(BaseModel):
    mfa_required: bool = True
    session_token: str


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str
    confirm_password: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 10:
            raise ValueError("Password must be at least 10 characters.")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain an uppercase letter.")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain a digit.")
        return v

    @model_validator(mode="after")
    def passwords_match(self) -> "ResetPasswordRequest":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

    @model_validator(mode="after")
    def passwords_match(self) -> "ChangePasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class TOTPEnrollResponse(BaseModel):
    secret: str
    qr_uri: str


class TOTPConfirmRequest(BaseModel):
    secret: str
    code: str


class RecoveryCodesResponse(BaseModel):
    codes: list[str]


class SessionResponse(BaseModel):
    id: uuid.UUID
    ip_address: str | None
    user_agent: str | None
    last_active_at: datetime
    created_at: datetime
    is_current: bool = False

    model_config = {"from_attributes": True}


class InviteRequest(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.USER


class SecurityEventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    ip_address: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminUserResponse(UserResponse):
    is_active: bool
    is_locked: bool
    last_login_at: datetime | None
    keycloak_id: str | None

    model_config = {"from_attributes": True}


class UpdateUserRoleRequest(BaseModel):
    role: UserRole


class MigrationImportRequest(BaseModel):
    keycloak_id: str
    email: str
    full_name: str | None = None
    email_verified: bool = False
    role: UserRole = UserRole.USER
