from fastapi import APIRouter
from app.api.v1 import health, videos, agent, edit_plans

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router)
api_v1_router.include_router(videos.router)
api_v1_router.include_router(agent.router)
api_v1_router.include_router(edit_plans.router)
