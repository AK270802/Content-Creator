import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_role
from app.models.user import User
from app.schemas.auth import (
    AdminUserResponse,
    InviteRequest,
    MigrationImportRequest,
    UpdateUserRoleRequest,
    UserResponse,
)
import app.services.auth_service as svc

router = APIRouter(prefix="/admin/users", tags=["admin"])

_ADMIN_ROLES = ("super_admin", "org_admin")


@router.get("", response_model=list[AdminUserResponse])
async def list_users(
    _: dict = Depends(require_role(*_ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.scalars(select(User).order_by(User.created_at.desc()))
    return [AdminUserResponse.model_validate(u) for u in result.all()]


@router.get("/{user_id}", response_model=AdminUserResponse)
async def get_user(
    user_id: str,
    _: dict = Depends(require_role(*_ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, uuid.UUID(user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return AdminUserResponse.model_validate(user)


@router.post("/invite", response_model=dict, status_code=status.HTTP_201_CREATED)
async def invite_user(
    req: InviteRequest,
    admin: dict = Depends(require_role(*_ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    await svc.invite_user(db, req.email, req.role, uuid.UUID(admin["sub"]))
    return {"message": "Invitation sent."}


@router.patch("/{user_id}/role", response_model=UserResponse)
async def update_role(
    user_id: str,
    req: UpdateUserRoleRequest,
    _: dict = Depends(require_role("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import update as sa_update
    target_id = uuid.UUID(user_id)
    await db.execute(sa_update(User).where(User.id == target_id).values(role=req.role))
    await db.commit()
    user = await db.get(User, target_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return UserResponse.model_validate(user)


@router.post("/{user_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    user_id: str,
    admin: dict = Depends(require_role(*_ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    await svc.deactivate_user(db, uuid.UUID(user_id), uuid.UUID(admin["sub"]))


@router.post("/{user_id}/reactivate", status_code=status.HTTP_204_NO_CONTENT)
async def reactivate_user(
    user_id: str,
    admin: dict = Depends(require_role(*_ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    await svc.reactivate_user(db, uuid.UUID(user_id), uuid.UUID(admin["sub"]))


@router.post("/migrate/keycloak", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def migrate_keycloak_user(
    req: MigrationImportRequest,
    _: dict = Depends(require_role("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    user = await svc.import_keycloak_user(db, req)
    return UserResponse.model_validate(user)
