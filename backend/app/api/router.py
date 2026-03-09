from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.motor import router as motor_router
from app.api.routes.vehicle import router as vehicle_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(motor_router)
api_router.include_router(vehicle_router)
