import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

# Checkpoints contain only JSON-like workflow state. Keep deserialization restricted
# even if a deployment forgets to set the recommended environment variable.
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.chat import router as chat_router
from app.api.feedback import router as feedback_router
from app.api.policies import router as policies_router
from app.api.projects import router as projects_router
from app.api.provider_health import router as provider_health_router
from app.api.simulations import router as simulations_router
from app.config import Settings, get_settings
from app.database import Database
from app.inference.catalog import create_model_catalog
from app.inference.engine import InferenceDecisionEngine
from app.observability import LangSmithObserver
from app.operations import configure_operations
from app.persistence.models import utc_now
from app.policy.checkpoints import open_checkpointer
from app.policy.workflow import create_policy_graph
from app.providers.factory import create_providers


def _is_unsafe_production_secret(value: str | None, *, development_value: str) -> bool:
    if value is None:
        return True
    normalized = value.strip()
    return (
        len(normalized) < 32
        or normalized == development_value
        or normalized.lower().startswith("replace-with")
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    if settings.inferenceiq_auth_required and _is_unsafe_production_secret(
        settings.inferenceiq_admin_token,
        development_value="dev-admin-key",
    ):
        raise RuntimeError(
            "Set a strong INFERENCEIQ_ADMIN_TOKEN before requiring authentication"
        )
    if settings.inferenceiq_auth_required and _is_unsafe_production_secret(
        settings.inferenceiq_audit_signing_key,
        development_value="dev-audit-signing-key",
    ):
        raise RuntimeError(
            "Set a strong INFERENCEIQ_AUDIT_SIGNING_KEY before requiring authentication"
        )
    database = Database(settings.database_url)
    app.state.database = database
    app.state.settings = settings
    if settings.auto_create_schema:
        await database.create_schema()
    app.state.providers = create_providers(settings)
    app.state.decision_engine = InferenceDecisionEngine(create_model_catalog(settings))
    app.state.observer = LangSmithObserver(
        enabled=settings.inferenceiq_langsmith_enabled,
        api_key=settings.langsmith_api_key,
        project=settings.langsmith_project,
    )
    try:
        async with open_checkpointer(settings.langgraph_checkpoint_url) as checkpointer:
            app.state.policy_graph = create_policy_graph(checkpointer)
            yield
    finally:
        await database.close()


def create_app(
    settings: Settings | None = None,
    *,
    health_clock: Callable[[], datetime] = utc_now,
) -> FastAPI:
    app = FastAPI(
        title="InferenceIQ",
        version="0.1.0",
        description="An intelligent inference optimization and control platform",
        lifespan=lifespan,
    )
    settings = settings or get_settings()
    app.state.settings = settings
    app.state.health_clock = health_clock
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in settings.dashboard_origins.split(",")
            if origin.strip()
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-InferenceIQ-Admin-Key"],
    )
    configure_operations(app)

    @app.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(chat_router)
    app.include_router(feedback_router)
    app.include_router(simulations_router)
    app.include_router(policies_router)
    app.include_router(projects_router)
    app.include_router(provider_health_router)
    dashboard_directory = Path(__file__).parent / "static" / "dashboard"
    if dashboard_directory.exists():
        app.mount(
            "/dashboard",
            StaticFiles(directory=dashboard_directory, html=True),
            name="dashboard",
        )
    return app


app = create_app()
