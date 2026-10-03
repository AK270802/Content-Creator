from fastapi import APIRouter
from app.api.v1 import health, videos, agent, edit_plans, auth, admin_users, smart_clips, studio

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(admin_users.router)
api_v1_router.include_router(videos.router)
api_v1_router.include_router(agent.router)
api_v1_router.include_router(edit_plans.router)
api_v1_router.include_router(smart_clips.router)
api_v1_router.include_router(studio.router)
