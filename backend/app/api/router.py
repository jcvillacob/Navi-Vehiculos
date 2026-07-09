from fastapi import APIRouter

from app.api.routes.audit import router as audit_router
from app.api.routes.auth import router as auth_router
from app.api.routes.cpk_cph import router as cpk_cph_router
from app.api.routes.customer import router as customer_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.disponibilidad import router as disponibilidad_router
from app.api.routes.geotab_taller import router as geotab_taller_router
from app.api.routes.health import router as health_router
from app.api.routes.integration import router as integration_router
from app.api.routes.motor import router as motor_router
from app.api.routes.rendimientos import router as rendimientos_router
from app.api.routes.roles import router as roles_router
from app.api.routes.user_preferences import router as user_preferences_router
from app.api.routes.users import router as users_router
from app.api.routes.vehicle import router as vehicle_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(roles_router)
api_router.include_router(audit_router)
api_router.include_router(cpk_cph_router)
api_router.include_router(customer_router)
api_router.include_router(dashboard_router)
api_router.include_router(disponibilidad_router)
api_router.include_router(geotab_taller_router)
api_router.include_router(health_router)
api_router.include_router(integration_router)
api_router.include_router(motor_router)
api_router.include_router(rendimientos_router)
api_router.include_router(vehicle_router)
api_router.include_router(user_preferences_router)
